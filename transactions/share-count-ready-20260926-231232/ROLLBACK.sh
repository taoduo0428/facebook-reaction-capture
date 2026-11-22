#!/usr/bin/env sh
set -eu
target=${1:?usage: ROLLBACK.sh TARGET_COPY}
dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
baseline="$dir/BASELINE_FILE.ps1"
test -f "$baseline"
cp "$baseline" "$target"
