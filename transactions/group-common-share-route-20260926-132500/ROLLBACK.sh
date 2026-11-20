#!/usr/bin/env sh
set -eu

target=${1:?usage: ROLLBACK.sh <target-copy>}
base_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
baseline="$base_dir/BASELINE_FILE.ps1"

test -f "$target"
test -f "$baseline"
cp "$baseline" "$target"
