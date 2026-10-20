#!/usr/bin/env bash
# CI entry point: versions stay pinned via uv.lock (`--locked`). The current
# environment uses wheels only. HA 2026.3.1 pins lru-dict 1.3.0, which needs a
# source build on Python 3.14; the minimum-version job permits that build.
# mock-open and pyric are sdist-only on PyPI and are skipped via
# --no-install-package: mock-open is an unused requirement of
# pytest-homeassistant-custom-component, and pyric is only imported by the
# bluetooth auto-recovery helper, which this integration never loads.
set -euo pipefail

test_group="${1:-ha-current}"
case "$test_group" in
  ha-min|ha-current) ;;
  *) echo "Expected ha-min or ha-current" >&2; exit 2 ;;
esac

build_flags=(--no-build)
if [[ "$test_group" == "ha-min" ]]; then
  build_flags=()
fi

uv sync --locked "${build_flags[@]}" --no-install-package mock-open --no-install-package pyric \
  --no-default-groups --group dev --group "$test_group"
uv run --locked --no-build --no-sync ruff check custom_components tests scripts
uv run --locked --no-build --no-sync ruff format --check custom_components tests scripts
uv run --locked --no-build --no-sync pytest -q
uv run --locked --no-build --no-sync python scripts/package.py
