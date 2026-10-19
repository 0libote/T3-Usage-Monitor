#!/usr/bin/env bash
# CI entry point: versions stay pinned via uv.lock (`--locked`) and no source
# distributions are built (`--no-build`).
# mock-open and pyric are sdist-only on PyPI and are skipped via
# --no-install-package: mock-open is an unused requirement of
# pytest-homeassistant-custom-component, and pyric is only imported by the
# bluetooth auto-recovery helper, which this integration never loads.
set -euo pipefail

uv sync --locked --no-build --no-install-package mock-open --no-install-package pyric --group dev
uv run --locked --no-build --no-sync ruff check custom_components tests scripts
uv run --locked --no-build --no-sync ruff format --check custom_components tests scripts
uv run --locked --no-build --no-sync pytest -q
uv run --locked --no-build --no-sync python scripts/package.py
