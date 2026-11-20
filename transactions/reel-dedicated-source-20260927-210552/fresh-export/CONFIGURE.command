#!/bin/bash
set -eu
cd -- "$(dirname -- "$0")"

on_exit() {
  status=$?
  if [ "$status" -ne 0 ]; then
    printf '\nCONFIGURE.command failed (exit %s). Fix the message above, then run it again.\n' "$status" >&2
    read -r -p 'Press Return to close this window.' _ || true
  fi
  exit "$status"
}
trap on_exit EXIT

/bin/bash ./setup.sh --install-only
./.venv/bin/python facebook_setup.py
/bin/bash ./setup.sh
