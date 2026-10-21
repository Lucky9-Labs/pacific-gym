# Pacific Gym reset checkpoint — 2026-10-21

This checkpoint records the state of the two remaining Pacific Gym Codex tasks
before resetting the task/project context. The source and tooling changes are
committed in Git. Large run evidence remains in the ignored `.pacific-gym/`
directory and must be preserved if the checkout is cleaned.

## Current acceptance state

- The earlier L-GPC mesh/collider coordinator task (`01a0eac7-8209-72c1-adb4-db10ec159a04`)
  completed run `fe250507-3cc0-434c-958f-8821310d87e4` with `PASS_SCOPED`.
  The remote candidate USD SHA-256 is
  `8482ac219a6275e42827647f8eb301beb106ff053fe0a2787374b6d9eda830c7`; the
  final receipt SHA-256 is
  `5041cb8ffa6e7b2aa60b1dc6afc0feb3d4170a8ce90cfbd328f9933183197d82`.
  The result covers the specified cooked-hull pose set, dense surface sampling,
  12-second standing, and ten isolated lower-body excursions. It does not
  establish general locomotion, full joint-range safety, disturbance recovery,
  or dismemberment locomotion.
- The current motion work is not accepted. The deterministic position-target /
  quasi-static controller is not a learned locomotion policy. V43 fell during
  its first-step probe and did not complete a walking cycle. The recorded V42
  clip is a settling test, not walking. No current-candidate walking animation
  or FLUX match is established. Liquid remains unqualified.
- The pinned FLUX reference has a legacy timestamp offset of 0.208333 seconds
  in the 12 extracted reference PNGs; preserve the original manifest and use
  actual source PTS when making phase comparisons.
- The project goal remains incomplete: first establish a repeatable intact
  walk and reference comparison, then evaluate style and damage behavior.
  The project usage hard floor is 20% remaining.

## GitHub issue tracker

Open issues created from this checkpoint:

- [#17 Finish the held-out Liquid contact-control set before calibration](https://github.com/Lucky9-Labs/pacific-gym/issues/17)
- [#18 Rebuild FLUX reference timing from source presentation timestamps](https://github.com/Lucky9-Labs/pacific-gym/issues/18)
- [#19 Achieve a repeatable native Strokah walking cycle](https://github.com/Lucky9-Labs/pacific-gym/issues/19)
- [#20 Add native damage-event evidence and demonstrate post-damage locomotion](https://github.com/Lucky9-Labs/pacific-gym/issues/20)
- [#21 Preserve ignored run evidence before clearing the Pacific Gym checkout](https://github.com/Lucky9-Labs/pacific-gym/issues/21)

## Held-out Liquid contact controls

Task `01a0ec2f-0d55-7db0-92f1-91852a3fbd3d` prepared three local previews from
run `85658793-ad95-4b20-9d40-a88dd137b305`, under
`.pacific-gym/runs/85658793-ad95-4b20-9d40-a88dd137b305/comparisons/timed-physics-20260929T072138Z/physics/`.
The previews are evidence-preparation only; no answer key or Liquid calibration
run was completed.

| Frame | Contact evidence | Preview SHA-256 |
|---|---|---|
| 0000 | 235 N; 231–274 N over ±100 ms; sole clearance −0.037 mm | `7df1d6c7f762883c6ed4194a79ab6fcfcf29f42248cf81bdadd19fa50af87a90` |
| 0006 | 64–364 N over ±100 ms; sole clearance 0.045 mm | `6bec92589e4c2a9476aed794c5a61127fe2ee96ba5068353ccb6dffaa0359ab0` |
| 0036 | 0 N; sole clearance 36.788 mm; 3 unobstructed pixel rows after 2 px mask | `e578050f9131c9c73cf0db01dcec45c627476d06a217b5ec023f52e29ec4c762` |

The previews preserved the input hashes and model pixels. Three more controls
were not prepared; the original task stopped at 31% remaining under its former
30% floor. The later 20% floor supersedes that stop for any future continuation.

## Motion review evidence

Task `01a0f3cc-2d8a-7452-8b15-91e71f8aede6` has its source and offline pipeline
changes in this Git checkpoint. Detailed run evidence remains in
`.pacific-gym/runs/6937f326-b9f6-471e-baa6-7fbc682421ed/`.

| Evidence | SHA-256 |
|---|---|
| `research/policy-motion-review-v43-20261020/REPORT.md` | `fa0ce8f5e614e68a3520ed5dcf566f68939b4e20687c2f6e4d8553e1d867cdf0` |
| `research/policy-motion-review-v43-20261020/NEXT-STAGE.md` | `0b7075223ed121f81fce00f6fc885ddb788c3ea93970157691a590ca24891067` |
| `NEXT-MOTION-COMPARISON-ROUTE-20261020.md` | `46e2ef61928b8a2227394f3f88cda11e94e0143c9cc9076cf1a5516f8b35703a` |
| `FLOOR20-EVIDENCE-TRANSFER-CONNECTION.md` | `4d461fd4500a9b0c742c6bb5203ade1df0b7f38403ca9be03982d361a8a47cc2` |
| V42 settling GIF | `c7e6c39e1892252e007683921f1775eb457b67a8370f845d449b1ead5114dabc` |
| FLUX reference GIF | `52c757eb97f593c4f2571a3f51f69d932a041db4a1ed6a46ac3a14ea00fd22a7` |

The offline report and next-stage brief are planning/evidence artifacts; they
do not certify a native walk. Do not treat the V42 GIF as the current
candidate's walking proof.

## Local artifact handling

The two ignored run directories above occupied approximately 1.9 GiB and 3.4
GiB at checkpoint time. They are not included in Git. A normal `git reset
--hard` preserves them, but `git clean -fdx`, deleting `.pacific-gym/`, or
removing this checkout would erase them. Keep both directories until their
required receipts and source inputs have been transferred to durable storage.

## Resume point

1. Preserve both ignored run directories and this checkpoint commit.
2. Finish or explicitly retire the remaining held-out contact-control set;
   calibration requires a separate authorized Liquid run and sealed labels.
3. Continue from the V43 contact/root audit and FLUX timing correction. Require
   a successful native walk and a full-duration reference comparison before
   style-reward experiments.
4. Keep the mesh `PASS_SCOPED` separate from motion, FLUX-match, Liquid, and
   damage acceptance.
