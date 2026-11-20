#!/bin/sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
"${PYTHON:-python}" - "$HERE/baseline.zip" "${1:?Usage: ROLLBACK.sh target-project-directory}" <<'PY'
from pathlib import Path
import sys, zipfile
root=Path(sys.argv[2]).resolve()
if not (root/'facebook_capture_api.py').is_file():
    raise SystemExit('Not a Facebook project copy')
with zipfile.ZipFile(sys.argv[1]) as z:
    for name in z.namelist():
        dest=(root/name).resolve()
        if not dest.is_relative_to(root): raise SystemExit('Unsafe archive member')
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes(z.read(name))
print('RESTORED',len(z.namelist()),'files')
PY
