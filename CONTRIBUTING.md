# Contributing

Thanks for helping improve `fluid-bridge`.

## Setup

```bash
uv sync --all-extras
```

## Checks

```bash
uv run pytest
uv run ruff check .
```

Tests should not require FluidAudio model downloads by default. Add live FluidAudio checks only when
they are opt-in and clearly marked.

## Design Guidelines

- Keep the package a thin adapter around FluidAudio's public CLI.
- Do not add application-specific behavior.
- Preserve raw stdout, stderr, and exit codes for debugging.
- Add typed parsing only when the upstream CLI output contract is stable.
- Keep future Rust/PyO3 support optional.
