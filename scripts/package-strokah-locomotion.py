#!/usr/bin/env python3
"""Package the selected walk v4 / sprint v7 bytes; never modify animation assets."""
import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def package(derivative, destination):
    alignment = derivative / 'model-alignment-v1'
    sources = {'walk': alignment / 'aligned-motion-candidate-v4',
               'sprint': alignment / 'aligned-sprint-candidate-v7/r01'}
    expected = {'walk': '01fd692dd008d147e76c06322b191142344e03b5e5741e1a1da4367d303deca2',
                'sprint': '9ce8a5de3b7222184aac617922b532acd120acbe0b073353d77cd3956e61e0e1'}
    destination.mkdir(parents=True, exist_ok=False)
    bundle = destination / 'bundle'
    bundle.mkdir()
    contract = {'schema': 'strokah-locomotion/v2', 'release': 'strokah-animation-dummy-v2',
                'source_action_3p3s_retained': True, 'kimodo_retarget_used': False,
                'model_bones': 62, 'dummy_bones': 63, 'clips': {},
                'runtime_policy': {'contact_phase_right_offset': .5,
                    'terrain_is_runtime_additive': True, 'terrain_runtime_acceptance': False,
                    'physics_acceptance': False, 'final_art_acceptance': False}}
    for clip, source in sources.items():
        for kind in ['model', 'dummy']:
            directory = source / f'{kind}-{clip}'
            readback = json.loads((directory / 'readback.json').read_text())
            revision = 4 if clip == 'walk' else 7
            stem = f'strokah-{kind}-{clip}-aligned-v{revision}'
            for extension, folder in [('blend', 'native'), ('fbx', 'exports')]:
                path = directory / f'{stem}.{extension}'
                assert digest(path) == readback['output_hashes'][path.name], path
                if kind == 'model' and extension == 'fbx': assert digest(path) == expected[clip]
                target = bundle / folder / f'{kind}-{clip}.{extension}'
                target.parent.mkdir(exist_ok=True)
                shutil.copyfile(path, target)
            evidence = bundle / 'evidence' / f'{kind}-{clip}'
            evidence.mkdir(parents=True)
            for name in ['readback.json', 'motion-validation.json']:
                shutil.copyfile(directory / name, evidence / name)
            if kind == 'model':
                contract['clips'][clip] = {
                    'fbx': f'exports/model-{clip}.fbx', 'sha256': expected[clip],
                    'action': readback['new_action'], 'action_signature': readback['action_signature'],
                    'rig_signature': readback['rig_signature'],
                    'duration_seconds': readback['duration_seconds'], 'fps': readback['fps'],
                    'duty': readback['parameters']['duty'],
                    'authored_stance_speed_mps': readback['parameters']['stride'] /
                        (readback['parameters']['duty'] * readback['duration_seconds'])}
    assert contract['clips']['walk']['rig_signature'] == contract['clips']['sprint']['rig_signature']
    previews = bundle / 'previews'; previews.mkdir()
    shutil.copyfile(sources['walk'] / 'previews/walk/walk-v4-comparison-loop.gif', previews / 'walk.gif')
    sprint = sources['sprint'].parent
    for name in ['sprint-v7-full-body-loop.gif', 'sprint-v7-foot-peel-loop.gif']:
        shutil.copyfile(sprint / 'previews' / name, previews / name)
    shutil.copyfile(sprint / 'visual-review.json', bundle / 'evidence/sprint-visual-review.json')
    (bundle / 'locomotion.json').write_text(json.dumps(contract, indent=2)+'\n')
    (bundle / 'README.md').write_text('''# Strokah animation dummy v2

Selected walk v4 (2 seconds) and sprint v7 (0.7 seconds), at 60 fps.
All native Blend and FBX files are byte-for-byte copies of their reviewed sources.
The original 3.3-second action remains in the native files. No Kimodo retarget.

For engine import use the **model-sprint mesh/skeleton**, which includes corrected
torso cheek weights, then import model-walk as animation only onto that skeleton.
Do not replace that corrected mesh with the older model-walk mesh. The 62-bone
rig signatures match. The tube dummy retains its pre-existing 63rd helper bone.

Heel, ball and toe articulation stays authored. Ground placement belongs to a
separate runtime layer and must disable foot locks during swing and sprint flight.
The GIFs show the source animations on flat ground, not terrain runtime or physics.
This package does not claim engine acceptance, collision/hitbox physics, or final art.
''')
    files = [{'path': p.relative_to(bundle).as_posix(), 'sha256': digest(p), 'bytes': p.stat().st_size}
             for p in sorted(bundle.rglob('*')) if p.is_file()]
    archive = destination / 'strokah-animation-dummy-v2.zip'
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for row in files:
            info = zipfile.ZipInfo(row['path'], (2026, 10, 31, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, (bundle / row['path']).read_bytes())
    release = {'schema_version': 1, 'release': contract['release'], 'asset_id': 'strokah-animation-dummy',
        'release_version': 2, 'authoring_revision': 'walk-v4_sprint-v7-r01',
        'status': 'selected_animation_release', 'bucket': 'mech-art-library-20260905225047699700000001',
        'artifacts': {'bundle': {'object_key': f'models/mechs/strokah-animation-dummy/v2/sha256-{digest(archive)}/{archive.name}',
            'sha256': digest(archive), 'bytes': archive.stat().st_size, 'content_type': 'application/zip'}},
        'entrypoints': {'locomotion_contract': 'locomotion.json', 'corrected_mesh': 'exports/model-sprint.fbx',
                       'walk_animation_only': 'exports/model-walk.fbx', 'dummy': 'native/dummy-sprint.blend'},
        'clips': contract['clips'], 'acceptance': contract['runtime_policy'], 'files': files}
    (destination / 'release-draft.json').write_text(json.dumps(release, indent=2)+'\n')
    print(json.dumps({'archive': str(archive), 'sha256': digest(archive), 'files': len(files)}))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--derivative', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    package(args.derivative, args.destination)
