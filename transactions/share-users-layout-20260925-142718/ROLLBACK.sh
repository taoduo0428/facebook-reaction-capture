#!/usr/bin/env bash
set -eu
TARGET="${1:?usage: ROLLBACK.sh <START.cmd.copy> [baseline_START.cmd]}"
BASELINE="${2:-$(dirname "$TARGET")/baseline_START.cmd}"
cp -- "$BASELINE" "$TARGET"
echo ROLLBACK_OK
