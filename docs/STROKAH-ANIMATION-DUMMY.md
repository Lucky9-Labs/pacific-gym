# Strokah Animation Dummy

The current selected locomotion package is **release 2**: the approved walk v4
(2 seconds) and sprint v7 (0.7 seconds), with the detailed model and egg/tube
dummy. The animation files are byte-preserved, including the original 3.3-second
action in their native Blend files. No Kimodo retarget is used.

The [v2 manifest](../releases/strokah-animation-dummy-v2.json) pins the private
S3 version, archive hash and all 22 member hashes. Restore with:

```sh
python3 scripts/restore-animation-dummy.py --profile remote-engine-admin \
  --destination /mnt/d/PacificGym/assets/strokah-animation-dummy/v2
```

Use `exports/model-sprint.fbx` for the shared **mesh and skeleton**, then import
`exports/model-walk.fbx` as **animation only** onto it. This keeps the sprint's
corrected torso cheek weights. Both model clips have the same 62-bone rig
signature. The dummy retains its existing 63rd helper bone. `locomotion.json`
records timing, contact duty and authored stance speed for runtime foot placement.
The native sources are under `native/`; previews and validation are included.

The walk remains unchanged. The sprint retains its front-knee float, extended
rear push-off and independent heel/ball/toe peel. Terrain adaptation is a separate
Hullscape runtime layer, not baked into these clips. The source GIFs show flat
ground animation; they are not terrain or physics evidence. The release does not
grant engine, physics, collider or final-art acceptance.

To package the same selected bytes again, run
`scripts/package-strokah-locomotion.py --derivative <tube-playtest-proxy-v1> --destination <new-directory>`.
It verifies the selected source hashes and never edits animation assets. Publish
a new immutable version for future changes; never overwrite a released key.

## Archived release 1

Release 1 remains frozen from the user-reviewed
cockpit/feet proxy **v6** on 2026-10-28. Use it for basic animation and hitbox
layout. It does not replace the detailed Strokah design or the original source
fixture, and is not final art or a validated physics asset.

## Where it lives

- Git record: [release manifest](../releases/strokah-animation-dummy-v1.json).
- Durable binary storage: the existing private, versioned art-library S3 bucket.
  The manifest pins an exact object version, archive SHA-256, and every file hash.
- L-GPC working reference: `D:\PacificGym\assets\strokah-animation-dummy\v1\`.
- Local backup archive: `D:\PacificGym\archives\strokah-animation-dummy-v1.zip`.

The model binaries are private and are not committed to this public repository.
The original run and earlier proxy revisions remain intact. Comparison Blender
scenes remain in the run; this release includes their screenshots, not the
original detailed design mesh.

| File inside the bundle | Use |
| --- | --- |
| `native/strokah-animation-dummy.blend` | Authoritative editable source, Pose Position, authored walk ready to play |
| `native/strokah-animation-dummy-neutral.blend` | Neutral bilateral inspection; switch to Pose Position to animate |
| `exports/strokah-animation-dummy.fbx` | Engine interchange candidate |
| `evidence/` | Native Windows Blender playback/neutral/overlay screenshots and validation receipts |
| `bundle.json` | File hashes, rig/action contract, provenance and acceptance scope |

## Restore on another machine

Requires Python 3.10+, AWS CLI, and an AWS profile with read access to the private
art bucket. From the Pacific Gym checkout in WSL:

```sh
python3 scripts/restore-animation-dummy.py \
  --manifest releases/strokah-animation-dummy-v1.json \
  --profile remote-engine-admin \
  --destination /mnt/d/PacificGym/assets/strokah-animation-dummy/v1
```

Use a profile available on that machine; `remote-engine-admin` was the tested
L-GPC profile. On native Windows, use `py -3` and a `D:\...` destination. On Mac,
choose a local destination. Downloads are temporary on the destination drive.
For an existing local archive, replace `--profile ...` with
`--from-bundle /absolute/path/strokah-animation-dummy-v1.zip`.

The restore command downloads the pinned S3 version, checks the archive and all
member hashes, and refuses to overwrite different local files. Identical files
are left alone. It does not import the model into an engine.

## Animation contract

- One mesh, 4,216 weighted vertices, 48 groups.
- 63 bones: the original 62 plus `MCH_contact_heel.L`, added in the earlier
  bilateral symmetry repair. This release preserves the reviewed v6 rig exactly.
- `Strokah_Gameplay_WalkLoop_InPlace_3p3s_v1`: frames 1–199, 60 fps, 3.3 seconds
  (198 frame intervals). No Kimodo G1 retarget.
- Bilateral rest skeleton and geometry. Walking frames alternate and are not
  expected to be individually symmetric.
- Cockpit is 12% larger and 4 cm higher than v5, retaining its 40-degree tilt.
  Feet have 15-degree mirrored toe-out, 35% wider segments and flatter forefeet.
- The comparison-only 11.612883 cm offset of the original detailed design is
  absent from the released dummy.

Native L-GPC Blender front/side playback and fresh Blend/FBX structural readback
were verified. Engine import, physics, colliders and final-art acceptance are
pending. The geometry is a hitbox-layout reference; no engine colliders exist.

Blender's default FBX importer changes Connected flags (notably `shin.L`) and
suppresses a translation channel, causing approximately 1.17 cm maximum sampled
group-bounds error. Restoring source flags in temporary readback reduces the
error below 0.000001 m; that workaround did not modify the export. Source flags
are included in `evidence/blender-readback.json`. Treat the native Blend as the
animation authority until the chosen engine's import and playback are verified.

## Editing and versioning

Keep release 1 immutable. Copy the animation source into a new run or animation
working directory before editing. Keep the existing walk action intact and use
separately named actions/files for new clips. When changing the dummy's geometry
or rig, publish a new numbered bundle and commit its new version/hash manifest;
update this document and the restore default to select it. Never overwrite an
existing S3 key or silently replace the selected release.
