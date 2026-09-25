#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_root/plugins/pacific-gym"
python3 -m pacific_gym compare \
  --reference ../../proof/slice-06/reference.png \
  --candidate ../../proof/slice-06/candidate.png \
  --out .pacific-gym/comparison-acceptance.json
python3 - <<'PY'
import json
import re
from pathlib import Path

actual = json.loads(Path('.pacific-gym/comparison-acceptance.json').read_text())
proof = json.loads(Path('../../proof/slice-06/proof.json').read_text())
assert actual['model']['tag'] == proof['model']['tag']
assert actual['model']['digest'] == proof['model']['digest']
assert [(i['role'], i['sha256']) for i in actual['inputs']] == [
    (i['role'], i['sha256']) for i in proof['inputs']
]
result = actual['comparison']
assert result['confidence'] in ('high', 'medium'), result
for key in ('reference_orange_center_x', 'candidate_orange_center_x'):
    assert f"{actual['measurement'][key]:.1f}px" in result['evidence'], result
assert actual['measurement']['reference_orange_center_x'] > actual['measurement']['candidate_orange_center_x'] + 100
assert re.search(r'\b(left|orange)\s+foot\b', result['next_action'], re.I), result
assert re.search(r'\b(forward|right)\b', result['next_action'], re.I), result
assert re.match(r'^move\b', result['next_action'], re.I), result
print('COMPARISON_PULSE_PASS')
PY
