"""Incremental stdout and stderr consumption for FluidAudio commands."""

from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Iterator, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from threading import Lock, Thread
from time import monotonic
from typing import IO, Literal, NoReturn

from fluid_bridge.bridge import CommandResult, FluidAudioBridgeError

_STREAM_CLOSED = object()


@dataclass(frozen=True)
class StreamEvent:
    """One text line emitted by a running FluidAudio command."""

    stream: Literal["stdout", "stderr"]
    text: str


class StreamingCommand:
    """A running FluidAudio process with iterable output events."""

    def __init__(
        self,
        process: subprocess.Popen[str],
        command: Sequence[str],
        timeout_s: float | None,
    ) -> None:
        self._process = process
        self._command = tuple(command)
        self._timeout_s = timeout_s
        self._deadline = monotonic() + timeout_s if timeout_s is not None else None
        self._events: Queue[StreamEvent | object] = Queue()
        self._events_started = False
        self._events_lock = Lock()
        self._stdout: list[str] = []
        self._stderr: list[str] = []
        self._threads = [
            self._start_reader(process.stdout, "stdout", self._stdout),
            self._start_reader(process.stderr, "stderr", self._stderr),
        ]

    @classmethod
    def start(
        cls,
        command: Sequence[str],
        env: Mapping[str, str],
        cwd: Path | None,
        timeout_s: float | None,
    ) -> StreamingCommand:
        """Start a command with captured pipes available as incremental events."""
        try:
            process = subprocess.Popen(
                list(command),
                bufsize=1,
                cwd=cwd,
                env=dict(env),
                stderr=subprocess.PIPE,
                start_new_session=True,
                stdout=subprocess.PIPE,
                text=True,
            )
        except OSError as exc:
            raise FluidAudioBridgeError(f"Unable to start FluidAudio CLI: {exc}") from exc
        return cls(process, command, timeout_s)

    def __iter__(self) -> Iterator[StreamEvent]:
        return self.events()

    def events(self) -> Iterator[StreamEvent]:
        """Yield stdout and stderr lines until both channels close."""
        with self._events_lock:
            if self._events_started:
                raise FluidAudioBridgeError("Streaming events can only be consumed once")
            self._events_started = True
        closed_streams = 0
        while closed_streams < 2:
            timeout = self._remaining_timeout()
            if self._process.poll() is None and timeout is not None and timeout <= 0:
                self._raise_timeout()
            try:
                item = self._events.get(timeout=timeout)
            except Empty:
                self._raise_timeout()
            if item is _STREAM_CLOSED:
                closed_streams += 1
            elif isinstance(item, StreamEvent):
                yield item

    def cancel(self, grace_s: float = 1.0) -> None:
        """Terminate the command, escalating to a kill after ``grace_s``."""
        if self._process.poll() is not None:
            return
        self._signal_process_group(signal.SIGTERM)
        if grace_s <= 0:
            self._signal_process_group(signal.SIGKILL)
            self._process.wait()
            return
        try:
            self._process.wait(timeout=grace_s)
        except subprocess.TimeoutExpired:
            self._signal_process_group(signal.SIGKILL)
            self._process.wait()

    def wait(self) -> CommandResult:
        """Wait for completion and return the accumulated process result."""
        timeout = self._remaining_timeout()
        if self._process.poll() is None and timeout is not None and timeout <= 0:
            self._raise_timeout()
        try:
            returncode = self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._raise_timeout()
        self._join_readers()
        return CommandResult(
            command=self._command,
            returncode=returncode,
            stdout="".join(self._stdout),
            stderr="".join(self._stderr),
        )

    def _remaining_timeout(self) -> float | None:
        if self._deadline is None:
            return None
        return max(0.0, self._deadline - monotonic())

    def _raise_timeout(self) -> NoReturn:
        self.cancel(grace_s=0)
        self._join_readers()
        raise FluidAudioBridgeError(
            f"FluidAudio CLI timed out after {self._timeout_s} seconds"
        )

    def _signal_process_group(self, signal_number: signal.Signals) -> None:
        with suppress(ProcessLookupError):
            os.killpg(self._process.pid, signal_number)

    def _join_readers(self) -> None:
        for thread in self._threads:
            thread.join()

    def _start_reader(
        self,
        pipe: IO[str] | None,
        stream: Literal["stdout", "stderr"],
        output: list[str],
    ) -> Thread:
        if pipe is None:
            raise FluidAudioBridgeError(f"FluidAudio {stream} pipe was not created")

        def read() -> None:
            try:
                for text in iter(pipe.readline, ""):
                    output.append(text)
                    self._events.put(StreamEvent(stream, text))
            finally:
                pipe.close()
                self._events.put(_STREAM_CLOSED)

        thread = Thread(target=read, daemon=True)
        thread.start()
        return thread
