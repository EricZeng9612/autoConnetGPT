#!/bin/zsh
set -eu
exec "${AUTOCONNETGPT_PYTHON:-python3}" "${0:A:h}/manage.py" install
