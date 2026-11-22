#!/usr/bin/env sh
set -eu
target=${1:?usage: ROLLBACK.sh TARGET_COPY}
dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cp "$dir/BASELINE_FILE.py" "$target"
