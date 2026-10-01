#!/usr/bin/env bash

set -Eeuo pipefail

# ============================================================================
# zlink build script
#
# Builds:
#   dist/*.whl
#   dist/*.tar.gz
#
# Then validates the wheel in a clean virtual environment.
# ============================================================================

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

VENV="$PROJECT_ROOT/.venv"
DIST="$PROJECT_ROOT/dist"
BUILD="$PROJECT_ROOT/build"

PYTHON="${PYTHON:-python3}"

echo "==> Project : $PROJECT_ROOT"
echo "==> Python  : $PYTHON"

# ----------------------------------------------------------------------------
# Python check
# ----------------------------------------------------------------------------

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "ERROR: Python not found: $PYTHON"
    exit 1
fi

echo "==> Python version:"
"$PYTHON" --version

# ----------------------------------------------------------------------------
# Virtual environment
# ----------------------------------------------------------------------------
#
# A venv contains absolute paths in its scripts. If it was copied/moved from
# another project, recreate it instead of trying to repair it.
# ----------------------------------------------------------------------------

if [[ -x "$VENV/bin/python" ]]; then
    VENV_PYTHON="$VENV/bin/python"

    if ! "$VENV_PYTHON" -c 'import sys; print(sys.prefix)' >/dev/null 2>&1; then
        echo "==> Existing virtual environment is invalid, recreating..."
        rm -rf "$VENV"
    fi
else
    echo "==> Creating virtual environment..."
    rm -rf "$VENV"
fi

if [[ ! -x "$VENV/bin/python" ]]; then
    "$PYTHON" -m venv "$VENV"
fi

VENV_PYTHON="$VENV/bin/python"

echo "==> Virtual environment:"
"$VENV_PYTHON" --version
echo "    $VENV"

# ----------------------------------------------------------------------------
# Build dependencies
# ----------------------------------------------------------------------------

echo "==> Installing build dependencies..."

"$VENV_PYTHON" -m pip install --upgrade \
    pip \
    build \
    wheel

# ----------------------------------------------------------------------------
# Clean previous build
# ----------------------------------------------------------------------------

echo "==> Cleaning previous artifacts..."

rm -rf "$BUILD"
rm -rf "$DIST"

# Remove stale packaging metadata if present.
rm -rf ./*.egg-info
rm -rf ./src/*.egg-info

mkdir -p "$DIST"

# ----------------------------------------------------------------------------
# Build wheel + source distribution
# ----------------------------------------------------------------------------

echo "==> Building package..."

"$VENV_PYTHON" -m build \
    --outdir "$DIST"

# ----------------------------------------------------------------------------
# Show generated artifacts
# ----------------------------------------------------------------------------

echo
echo "==> Build artifacts:"
find "$DIST" -maxdepth 1 -type f -print

# ----------------------------------------------------------------------------
# Validate the wheel
# ----------------------------------------------------------------------------

WHEEL="$(find "$DIST" -maxdepth 1 -type f -name '*.whl' -print -quit)"

if [[ -z "$WHEEL" ]]; then
    echo "ERROR: No wheel was generated."
    exit 1
fi

echo
echo "==> Validating wheel:"
echo "    $WHEEL"

TEST_VENV="$(mktemp -d "${TMPDIR:-/tmp}/zlink-test.XXXXXX")"

cleanup() {
    rm -rf "$TEST_VENV"
}

trap cleanup EXIT

"$PYTHON" -m venv "$TEST_VENV"

TEST_PYTHON="$TEST_VENV/bin/python"

"$TEST_PYTHON" -m pip install --upgrade pip >/dev/null

"$TEST_PYTHON" -m pip install "$WHEEL"

echo
echo "==> Package installation succeeded."

# ----------------------------------------------------------------------------
# Optional import test
# ----------------------------------------------------------------------------
#
# If your Python package is named "zlink", this verifies that the installed
# package can actually be imported.
#
# Remove this block if your import name is different.
# ----------------------------------------------------------------------------

if "$TEST_PYTHON" - <<'PY'
import importlib.util
import sys

if importlib.util.find_spec("zlink") is None:
    print("WARNING: module 'zlink' not found; skipping import test.")
    sys.exit(10)

import zlink

print(f"Imported zlink from: {zlink.__file__}")
PY
then
    echo "==> Import test succeeded."
else
    STATUS=$?

    if [[ "$STATUS" -eq 10 ]]; then
        echo "==> Import test skipped."
    else
        echo "ERROR: Import test failed."
        exit "$STATUS"
    fi
fi

echo
echo "========================================"
echo " Build successful"
echo "========================================"
echo
echo "Wheel:"
echo "  $WHEEL"
echo
echo "Source distribution:"
find "$DIST" -maxdepth 1 -type f -name '*.tar.gz' -print
echo
echo "To install locally:"
echo "  $VENV_PYTHON -m pip install \"$WHEEL\""
echo