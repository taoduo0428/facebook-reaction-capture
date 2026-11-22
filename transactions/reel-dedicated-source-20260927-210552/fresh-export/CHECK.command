#!/bin/bash
set -eu
cd -- "$(dirname -- "$0")"
if ! /bin/bash ./setup.sh --check; then
  printf '\nCheck the diagnostics above and README.md.\n'
fi
read -r -p 'Press Return to close.' _answer
