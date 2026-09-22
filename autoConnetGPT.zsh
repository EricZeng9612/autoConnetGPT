#!/bin/zsh
set -eu
SCRIPT_DIR="${0:A:h}"
PYTHON_BIN="${AUTOCONNETGPT_PYTHON:-python3}"
if [[ -f "$SCRIPT_DIR/python-path" ]]; then
  PYTHON_BIN="$(<"$SCRIPT_DIR/python-path")"
fi
exec "$PYTHON_BIN" "$SCRIPT_DIR/autoConnetGPT.py" "$@"
