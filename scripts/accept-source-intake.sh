#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
plugin_root="$repo_root/plugins/pacific-gym"
cd "$plugin_root"

python3 - <<'PY'
import json
from pathlib import Path

root = Path.cwd()
assert json.loads((root / '.codex-plugin/plugin.json').read_text())['name'] == 'pacific-gym'
assert json.loads((root / 'hooks/hooks.json').read_text())['hooks']['SessionStart']
assert (root / 'skills/isaac-ready/SKILL.md').is_file()
print('PLUGIN_PACKAGE_LOCAL_PASS')
PY
PLUGIN_ROOT="$plugin_root" python3 hooks/session_start.py
python3 -m pacific_gym inspect \
  --spec fixtures/strokah-source.json \
  --out .pacific-gym/inspect.json >/dev/null
python3 - <<'PY'
import json
from pathlib import Path

report = json.loads(Path('.pacific-gym/inspect.json').read_text())
assets = {asset['role']: asset for asset in report['assets']}
assert set(assets) == {'visual_lod0', 'rig_reference'}
visual = assets['visual_lod0']['structure']
rig = assets['rig_reference']['structure']
assert visual['skins'] == [] and visual['animations'] == []
assert visual['weighted_primitives'] == 0
assert rig['skins'] == [{'name': 'Strokah_MechanicalRig', 'joints': 62}]
assert rig['animations'] == [] and rig['weighted_primitives'] == 0
assert rig['rigid_parented_mesh_nodes'] == 90
print('SOURCE_INTAKE_LOCAL_PASS')
PY
