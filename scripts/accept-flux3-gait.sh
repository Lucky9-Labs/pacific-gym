#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_root"

sh scripts/accept-source-intake.sh
python3 - <<'PY'
import hashlib
import json
from pathlib import Path

root = Path('proof/slice-03/renders')
expected = {
    'visual': '65c6ef7de40c529c75c82dad8ab0f5ac13638882b1ef1eddee9a49a89ee42af1',
    'rig': '4f8bdb1d15ed6d164f1d8fa2aca63c11884f7019ed892f2862c61d5245135506',
}
for role, source_sha in expected.items():
    directory = root / role
    manifest = json.loads((directory / 'render.json').read_text())
    assert manifest['source_sha256'] == source_sha
    assert set(manifest['views']) == {'negative-y', 'positive-y', 'negative-x', 'positive-x', 'three-quarter'}
    for view, receipt in manifest['views'].items():
        image = directory / receipt['path']
        assert image.stat().st_size == receipt['bytes']
        assert hashlib.sha256(image.read_bytes()).hexdigest() == receipt['sha256']
print('BLENDER_RENDERS_HASH_PASS')
PY
python3 scripts/build-flux3-proof-sheet.py
python3 scripts/run-flux3-gait.py

if [ "${1:-}" = "--live" ]; then
  python3 scripts/run-flux3-gait.py --live
else
  echo FLUX3_LOCAL_PREFLIGHT_PASS
fi
