#!/usr/bin/env bash
set -euo pipefail
TARGET_DIR="${1:?usage: ROLLBACK.sh <target-directory-copy>}"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
BASELINE_DIR="$SCRIPT_DIR/baseline-target-validation"
if [[ ! -d "$TARGET_DIR" ]]; then
  printf 'target directory does not exist: %s\n' "$TARGET_DIR" >&2
  exit 2
fi
for name in open_facebook_post_readonly.ps1 run_facebook_capture.ps1 explore_facebook_readonly.ps1 facebook_capture_api.py; do
  cp -- "$BASELINE_DIR/$name" "$TARGET_DIR/$name"
done
printf 'rollback_target=%s\n' "$TARGET_DIR"
sha256sum "$TARGET_DIR/open_facebook_post_readonly.ps1" "$TARGET_DIR/run_facebook_capture.ps1" "$TARGET_DIR/explore_facebook_readonly.ps1" "$TARGET_DIR/facebook_capture_api.py"
