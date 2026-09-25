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
python3 - <<'PY'
import hashlib
import json
from pathlib import Path

directory = Path('proof/slice-03/renders/industrial')
receipt = json.loads((directory / 'render.json').read_text())
assert receipt['source_sha256'] == '65c6ef7de40c529c75c82dad8ab0f5ac13638882b1ef1eddee9a49a89ee42af1'
assert set(receipt['views']) == {'walk-left'}
image = directory / 'walk-left.png'
assert hashlib.sha256(image.read_bytes()).hexdigest() == receipt['views']['walk-left']['sha256']
print('INDUSTRIAL_START_FRAME_HASH_PASS')
PY
python3 scripts/build-flux3-proof-sheet.py
python3 scripts/run-flux3-gait.py
python3 - <<'PY'
import hashlib
import json
from pathlib import Path

proof = json.loads(Path('proof/slice-03/proof.json').read_text())
request = proof['bfl_request']
for path, expected in (
    (request['spec'], request['spec_sha256']),
    (request['video'], request['video_sha256']),
    (request['video_contact_sheet'], request['video_contact_sheet_sha256']),
    (request['feet_contact_sheet'], request['feet_contact_sheet_sha256']),
    (proof['heft_candidate']['spec'], proof['heft_candidate']['spec_sha256']),
    (proof['heft_candidate']['video'], proof['heft_candidate']['video_sha256']),
    (proof['heft_candidate']['contact_sheet'], proof['heft_candidate']['contact_sheet_sha256']),
    (proof['industrial_candidate']['spec'], proof['industrial_candidate']['spec_sha256']),
    (proof['industrial_candidate']['start_frame'], proof['industrial_candidate']['start_frame_sha256']),
    (proof['industrial_candidate']['video'], proof['industrial_candidate']['video_sha256']),
    (proof['industrial_candidate']['contact_sheet'], proof['industrial_candidate']['contact_sheet_sha256']),
    (proof['industrial_candidate']['feet_sheet'], proof['industrial_candidate']['feet_sheet_sha256']),
    (proof['industrial_candidate']['full_frames_manifest'], proof['industrial_candidate']['full_frames_manifest_sha256']),
):
    assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == expected, path
manifest = json.loads(Path(proof['industrial_candidate']['full_frames_manifest']).read_text())
assert manifest['source_video_sha256'] == proof['industrial_candidate']['video_sha256']
assert manifest['crop'] is None and manifest['dimensions'] == [960, 960]
assert len(manifest['frames']) == proof['industrial_candidate']['full_frame_count']
frame_root = Path(proof['industrial_candidate']['full_frames_manifest']).parent
for frame in manifest['frames']:
    image = frame_root / frame['path']
    assert hashlib.sha256(image.read_bytes()).hexdigest() == frame['sha256']
    assert image.stat().st_size == frame['bytes']
print('FLUX3_VIDEO_PROOF_HASH_PASS')

publication = json.loads(Path(proof['s3_publication']['receipt']).read_text())
handoff = json.loads(Path(proof['s3_publication']['manifest']).read_text())
assert publication['status'] == 'published_and_readback_verified_visual_candidate'
assert publication['candidate_only'] is True
for role, path in (('video', Path(proof['industrial_candidate']['video'])),
                   ('manifest', Path(proof['s3_publication']['manifest']))):
    item = publication[role]
    assert item['version_id'] and item['sha256'] == item['readback_sha256']
    assert hashlib.sha256(path.read_bytes()).hexdigest() == item['sha256']
    assert path.stat().st_size == item['bytes']
assert handoff['video']['sha256'] == publication['video']['sha256']
assert handoff['status'] == 'visual_candidate_not_accepted_for_foot_contacts_or_physics'
print('S3_CANDIDATE_RECEIPT_HASH_PASS')
PY

if [ "${1:-}" = "--live" ]; then
  python3 scripts/run-flux3-gait.py --live
elif [ "${1:-}" = "--live-from-clipboard" ]; then
  python3 scripts/run-flux3-gait.py --live --clipboard-key
else
  echo FLUX3_LOCAL_PREFLIGHT_PASS
fi
