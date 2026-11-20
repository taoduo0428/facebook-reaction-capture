#!/usr/bin/env bash
set -eu
TARGET="${1:?usage: ROLLBACK.sh <target-directory>}"
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cp -- "$HERE/baseline/facebook_capture_api.py" "$TARGET/facebook_capture_api.py"
mkdir -p "$TARGET/web_v2" "$TARGET/tools"
cp -- "$HERE/baseline/app.js" "$TARGET/web_v2/app.js"
rm -f -- "$TARGET/tools/verify_share_result.py"
echo ROLLBACK_OK
