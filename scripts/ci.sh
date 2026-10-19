#!/usr/bin/env bash
# CI entry point: versions stay pinned via uv.lock (`--locked`).
# `--no-build` is intentionally absent: one transitive dev dependency
# (mock-open) ships sdist-only, so disabling builds breaks the install.
# Runners are ephemeral and the lockfile pins every version.
set -euo pipefail

uv sync --locked --group dev
uv run --locked ruff check custom_components tests scripts
uv run --locked ruff format --check custom_components tests scripts
uv run --locked pytest -q
uv run --locked python scripts/package.py
