# Reproducible articulation evidence

These tools turn the source-derived Strokah experiments into reusable stages.
They do **not** constitute a completed automatic clearance-repair or walking
pipeline. Each output is an immutable candidate or an audit; only `isaac-run`
can supply the runtime acceptance evidence required by `run-complete`.

## Inputs

Keep these under a run directory, with their SHA-256 values:

- Immutable source GLB and explicit simplification/clearance/mass configuration.
- Simplified rigid GLB, with the same named nodes, parents, scene membership,
  joint identities and rest transforms.
- Source geometry JSON from Blender: source hash, bones, and mesh records with
  node index, name, world vertices/faces, owner, category and manifold status.
- A self-contained USD articulation candidate. The physics authoring choices
  (links, axes, frames, limits, materials, drives and masses) remain explicit
  in this asset; they are not facts inferred from the presence of a GLB rig.

The current geometry exporter uses meters and the explicit conversion
`(x, y, z) -> (x, -z, y)` from glTF Y-up to stage Z-up. The GLB decoder refuses
skinning, morph targets, unsupported primitive modes, external buffers and
ambiguous hierarchy instead of silently treating them as rigid geometry.

## Stages

Run all OpenUSD and Isaac operations on L-GPC. The offline snapshot and assembly
scripts import OpenUSD without creating `SimulationApp`. Before a Kit/GPU
launch, record a fresh account usage reading, the authorized remaining-usage
floor, exact candidate hash, and Windows process inventory. Honor the current
user instruction; do not bake an old run's floor into the tools.

### 1. Export and audit the candidate

With an OpenUSD Python interpreter:

```sh
python scripts/export-articulation-snapshot.py --usd candidate.usda --out snapshot.json
```

The snapshot includes composed joint frames and ranges, rigid masses/inertias,
mesh counts, world transforms, and collision fingerprints covering geometry,
ancestor transforms, physics attributes and material relationships. Fingerprints
are conservative: a changed inertial property can invalidate the fingerprint
even when it would not change cooking. They do not include the bytes of external
material assets, so they cannot authorize their reuse without a dependency seal.

With NumPy:

```sh
python -m pacific_gym.articulation_audit \
  --snapshot snapshot.json --config config.json \
  --source-geometry source-geometry.json --out requirement-audit.json
```

An optional `--motion receipt.json` checks the matching USD hash, reconstructs
target ordering from runtime names/DOF indices, detects limit violations, and
reports sustained contact-free intervals. It never counts programmed phases as
measured walking cycles. It requires the historical diagnostic receipt format;
the formal `isaac-run` acceptance schema is separate.

### 2. Reproduce source-volume mass estimates

With NumPy and SciPy:

```sh
python -m pacific_gym.solid_volume \
  --source-geometry source-geometry.json --config config.json \
  --snapshot snapshot.json --out volume-audit.json
```

This orients closed components consistently, finds connected shells, and
deduplicates only bijectively matched vertices and identical mapped triangle
topology. It measures the maximum matched-vertex distance, not a percentile.
Potentially overlapping nonduplicate components are unresolved and prevent a
mass proposal. Nonmanifold source parts are explicitly excluded from the stated
closed-volume estimator. Overlap between separate visual parts is not measured.

Mass allocation enforces the minimum for **every** link, including links with
small positive volume, then redistributes the remainder by volume. The snapshot
provides links that have no directly assigned source mesh. These are estimates;
this stage does not calculate exact solid inertias or change USD mass properties.

Closed-component records also expose outward-facing triangle indices for
reconstructing deduplicated source solids. Volume estimation and source-solid
reconstruction use the same connected components and orientation decisions.

### 3. Assemble simplified visuals with preserved physics

With OpenUSD, NumPy, and the package on `PYTHONPATH`:

```sh
python scripts/assemble-simplified-articulation.py \
  --usd baseline.usda --usd-sha256 EXPECTED_BASELINE_SHA \
  --source source.glb --visual-glb simplified.glb \
  --visual-sha256 EXPECTED_VISUAL_SHA \
  --config config.json --mapping source-geometry.json \
  --out assembled.usda --receipt assembly.json
```

The stage requires fresh output paths. It verifies the source hash, pinned input
hashes, complete rigid hierarchy, rest matrices and triangle budget. It replaces
only visual mesh geometry. Visual meshes also carrying static collision (such
as terrain) are kept intact. All other composed properties, including every
collider and joint, are compared before/after assembly and after USD
serialization. Serialized visual point error is measured separately.

Assembly preserves a candidate's existing physics flaws too. A new visual mesh
does not make the baseline's limits, colliders, mass estimates or gait valid.

### 4. Remeasure sampled visual shape error

Use `target-preflight`, then `blender-derive` with
`scripts/audit-rigid-derivative.py`. Declare source and simplified GLBs as the two
inputs, a new audit JSON under the run's `derivatives/` as the output, and pass
`--config` and `--package-root` as extra arguments.

The script measures deterministic vertex samples in both directions using
Blender BVHs. It enforces the configured source-to-derivative threshold and
closed-part component-volume-change threshold. The reverse direction is
reported separately. These samples do not certify every point on a surface.
Component-summed volume is a shape-change diagnostic, not the deduplicated
occupied-volume estimate used for mass.

### 5. Clearance and runtime remain independent gates

Before accepting a repair, establish all of the following:

1. Every configured joint family and sweep range is represented by the model.
2. The declared sampling/refinement procedure is actually executed; state which
   combinations remain untested.
3. Cumulative collider changes are measured from the immutable baseline, with
   explicit spatial as well as removed-volume limits.
4. Every reused cooked shape has compatible source geometry, transforms and
   cooking settings; do not inherit provenance from a points-only hash.
5. Actual runtime contact impulses use the measured physics-step duration.
6. Joint targets and observed states respect limits. Swing, touchdown, alternating
   support and forward travel are measured; elapsed sine phases are insufficient.
7. `isaac-run` validates the exact staged USD, GPU, structured receipt and video.

The current v14 assembly satisfies the visual triangle budget and preserves the
v13 physics properties. The original configuration still requires wider ranges
and hip yaw that this ten-DOF candidate does not provide. The historical 47-pose
screen covers narrower isolated samples, not that configuration. The old v13
motion targets exceeded hip limits, had no sustained contact-free swing, and
reported standing support force at approximately twice weight. It is diagnostic
history, not accepted walking.

## Configured-range hypothesis and offline support planning

`scripts/configure-strokah-ranges.py` creates a **new** candidate from the pinned
ten-DOF baseline. It authors the existing configuration's full ranges, adds two
hip-yaw DOFs at the existing hip-pitch world frames, and assigns each new empty
link the configured minimum mass with an explicitly estimated sphere inertia.
All masses are renormalized to the configured total. It preserves every source
visual/collider mesh and every original joint world rest frame. The original
hip/thigh collision exclusions are explicitly retained across each empty yaw
link. No additional physical pair is silently disabled.

This prototype needs the `--usd`, `--usd-sha256`, `--config`, `--volume-audit`,
`--out` and `--receipt` arguments. Its output has 14 links and 12 DOFs. Covering
the configured ranges is a structural result; those ranges have not thereby
passed clearance.

`python -m pacific_gym.quasistatic_plan --snapshot ... --source-geometry ...
--out ...` searches bounded weight-transfer and 30 mm lift / 50 mm forward
swing poses for each support side. It uses original protected foot geometry,
authored joint frames/limits and the candidate's estimated mass/CoM properties.
Root poses are forward-kinematics results with an anchored stance foot, never
instructions to reset or support the root in simulation. It reports measured
support margins and pose residuals separately from optimizer termination.

A failed numerical search is not a proof that no feasible pose exists. A
successful search is also insufficient for walking: combined-pose collision,
torque, dynamics, a smooth contact-aware trajectory, and runtime evidence remain
required.

## Fresh cooking, spatial auditing, and bounded repair

`scripts/normalize-cooking-settings.py` changes explicitly authored convex hull
vertex limits above 64 to 64 in a new USD. `scripts/cook-articulation.py` then
requires a recent exact-candidate usage/process gate and successful target
preflight. It cooks every robot collider afresh without playing the timeline.
Returned convex vertices define the diagnostic hulls; the original API polygon
planes and their residuals are retained separately. A discrepancy between those
representations remains a runtime-validation concern.

`python -m pacific_gym.clearance_sweep` consumes the snapshot, cooked data, cook
receipt and configuration. Every passing hull pair has a verified separating
plane with at least the requested gap. Violations have a feasible point pair
below the gap or a verified intersection point. Indeterminate optimization
results do not pass. `--step 1` executes all configured isolated 1-degree
samples, including endpoints and rest; `--poses` accepts explicit combinations.
These are discrete geometric screens, not continuous or dynamic proofs.

`scripts/export-collider-geometry.py` exports authored mesh points, topology,
source-node identities, and both mesh/body world matrices. The Blender
`scripts/audit-collider-shape.py` measures sampled distances in both directions
against the immutable source surface. Its application of the configuration's
visual 6 mm distance threshold to colliders is explicitly **conservative**.
The separate declared collision-volume removal cap is 5%.

If that conservative spatial audit rejects a collider,
`scripts/restore-source-colliders.py` can create a new baseline using the exact
source solid. It deduplicates coincident closed shells, maps source world points
through the inverse mesh world matrix, and verifies serialized world positions.
It preserves all visuals, joint frames, masses, filters, and untouched colliders.
It deliberately invalidates prior clearance results.

`python -m pacific_gym.clearance_repair` turns verified failed hull pairs into
local Boolean alternatives. It inflates the opposing cooked convex hull by the
target gap plus an explicit margin, then transforms it into the edited body's
local frame. Equivalent relative poses are deduplicated. The resulting recipe
pins all inputs and carries immutable-source volume baselines.

Run `scripts/apply-clearance-repairs.py` through `blender-derive` with source
geometry, authored collider export, recipe and configuration as its four
inputs. Its two outputs are repaired geometry and an execution report.
Each local Boolean alternative must remain closed, retain positive volume,
and stay within the cumulative source-volume cap. Source distances are measured
in both directions. A collision-specific configured distance cap is enforced
when present; `--maximum-collider-source-distance-m` on the recipe generator
can add a stricter guard. The visual simplification threshold is not silently
treated as a declared collision requirement. Protected foot geometry is excluded.
Rejected alternatives and exhausted per-pair budgets are recorded. Applied
edits still require a fresh cook and full clearance sweep.

Use `clearance_sweep --all-witnesses` to collect every failing hull pair;
the default stops after the first failure in each mesh pair. Default intersection
counts are counts of those selected witnesses, not total intersections. In
particular, a nonintersecting first witness does not establish that the remainder
of the pair has no intersections. `--external-witnesses` saves each pose's full
witnesses to an immutable compressed file with a hash in the compact report.
The repair generator checks these hashes and pose/candidate identities.

`clearance_repair --group-pair-cutters` requires an all-witness sweep and creates
one collection Boolean alternative per edited side of each mesh pair. It covers
all observed cutters for that pair; it does not claim coverage of unsampled motion.
Each collection Boolean counts as one repair attempt. The candidate embeds the
cumulative per-pair attempt ledger, and collider export carries it into the next
recipe. A geometry set that already lost source volume cannot silently start a
new ledger. This replaces the earlier experimental batches' per-batch counters.

The Blender stage uses the Manifold Boolean solver by default after checking
both operands are closed. `--boolean-solver EXACT` is an explicit alternative.
Results are properly triangulated before surface-distance checks and export;
triangle fans do not correctly tessellate arbitrary concave Boolean faces.
Native Blender failures produce a separate wrapper failure receipt. A partial
stage report from a crashed process is not a completed repair.

`scripts/materialize-collider-repairs.py` converts a pinned repair result into
a fresh USD while checking all unedited properties and serialized topology.
It does not convert a partial repair into clearance or physics acceptance.

### Corrected baseline, October 20

The v16 full 1-degree screen passed 608/629 samples, but a later source-space
audit found four displaced/trimmed parts outside the conservative distance
guard. Most seriously, node 59 was about 0.75 m below its source position:
the older v6 builder passed body-local points to a world-space setter, which
applied the inverse body transform a second time. That invalidates using the
v16 clearance count to qualify source-aligned geometry.

The source-restored v17 candidate has hash
`a032dd334827f942e26a880ecc6613814106d943eb500d020396dda864aa132f`.
All 83 authored colliders pass the conservative sampled spatial audit; the
maximum source-to-collider sample distance is 4.67 micrometers. The repeated
Blender audit is byte-identical. Its fresh cook contains 6,140 hulls from 83
colliders. Its full isolated sweep passes **0/629** samples, with 1,892
under-target pair observations, including 660 intersections and no
indeterminate results. This candidate is a corrected search baseline only.

The Blender wrapper now names staging copies by content hash and verifies
existing copies instead of deleting read-only Windows files. Failed historical
wrapper/cook attempts remain in the run alongside successful retries.

The first local repair experiment used the additional 6 mm collision guard.
Its original n-gon BVH audit was superseded by proper triangulation; an Exact
solver retry crashed, while the Manifold retry completed. The successful
conservative batch applied 49 cuts to seven colliders, with each edited closed
volume independently qualified.

A separate batch used the declared 5% collision-volume cap without inventing a
collision-distance requirement from the visual threshold. It applied 67 local
cuts. Independent closed-component audits qualified all seven edited solids;
the largest cumulative source-volume removal was 2.12%, and the largest
source-to-collider sampled distance was 8.63 mm. Repeating the complete batch
produced byte-identical geometry and execution reports:

- Geometry SHA-256: `06353e1cfb4860d17caa861893a873923ee4d1680ec0d303f8ce09d78e6f9d78`
- Report SHA-256: `9b04cbf411ee28f385fafa9f0a9ed61e5fff2b40f32d16e829f41928e64200f0`
- Materialized v19 USD: `7b616602c8c76638216a40bac0b39ac6bdc7bd5949480d4ba1120ed2d8eb8ea9`

V19 preserves all visual geometry, joints, masses, filters and unedited
colliders. Its fresh cook completed with 83 colliders and 6,141 hulls. The full
1-degree sweep still passes **0/629** poses: 1,887 under-target observations,
41 intersecting first witnesses, and no indeterminate cases. These are not total
intersection counts. The three first rest witnesses have gaps of 1.359, 1.477,
and 0.169 mm, but later hulls in those pairs still intersect.

The recipe also records unattempted cases beyond its per-pair iteration budget.
Refinement must use the exact base candidate's cooked witnesses and cumulative
immutable-source shape budgets. Convex approximation may refill small Boolean
reliefs; do not infer cooked clearance from the Blender result.

### Complete rest witnesses and grouped branch

The expanded rest scan records 851 failing hull pairs / 444 intersections in
v17, and 878 failing hull pairs / 438 intersections in v19. Both have zero
indeterminate hull pairs. The earlier first-witness counts concealed most of
this contact region.

V20 branches from source-restored v17 and groups the complete observed rest
cutters into three pair operations. All three found a shape-qualified alternative:
node 33 loses 1.017%, node 155 loses 4.673%, and node 59 loses 0.046% of immutable
source volume. An independent closed-component audit qualifies all three.
The materialized USD hash is
`824de46d191117ab7a546fd144e90c9d26877653a83298e5660aa7d500328d1f`.
Its embedded ledger records one attempt for each pair. Visuals, joints, masses,
filters and other colliders are preserved. Fresh cooking and motion screening
are still required; a rest-only repair is not walking acceptance.

V20's fresh full-range screen passes 0/629 poses. At rest it has 527 failing
hull pairs and 13 intersections. A controlled v21 comparison enables only
`physxConvexDecompositionCollision:shrinkWrap` on the five rest-problem meshes;
all other USD content is preserved. The fresh cook changes exactly those five
hull arrays, with the other 78 identical. Rest failures fall to 43, with no
intersections; the full screen still passes 0/629. Across its poses there are
78,681 failing hull observations, 19,990 intersecting observations, and six
indeterminate near-threshold cases. These are repeated observations, not unique
contacts.

The full-motion grouped batch on v21 accepts four pair operations and rejects
both left-hip pairs. It applies collections in batches of at most 128 cutters,
rejecting as soon as the cumulative source-volume cap is exceeded. V22 preserves
the accepted partial work, but it was not recooked or accepted.

### Source-overlap bound and ownership hypothesis

`scripts/measure-solid-overlap.py`, run through `blender-derive`, measures actual
closed-solid intersections separately from cooked hulls. At `hip_roll_L=-12`,
immutable source parts 155 and 34 overlap by 22.473690 cubic centimeters, while
their combined 5% removal allowance is 19.470896 cubic centimeters. Manifold and
Exact solvers with reversed operands agree within 4.98e-13 cubic meters. Under
the current body assignments and motion, subtractive repair cannot eliminate
even the overlap within the cap; a positive 2 mm gap requires still more removal.
This bound does not rule out reauthoring body assignments or inferred joint
frames.

V23 is an explicit ownership hypothesis: move both visual and collider 155 from
pelvis to existing hipL, analogous to the right mount's hipR assignment. It
preserves source GLB bytes, all world rest vertices, joint frames, existing
collision relationships, and all other mesh properties. Existing hip/thigh
mechanical-neighbor exclusions now apply to the reassigned mount; this effect
must be disclosed when interpreting clearance counts.

`scripts/reassign-rigid-part.py` implements the hypothesis with a pinned input.
It transfers the source-volume-weighted estimate of the part's mass from the
prior body and recomputes both affected centers/inertias from consistently
oriented closed source solids. Mass remains an estimate; cross-part union is
not measured. V23 transfers 0.536768 kg, preserves total 58.8 kg, and has zero
measured world-rest vertex error. Its USD hash is
`685e072eb9e9d8aadf949b71ea217972e4af12cbc6f029c959624499bb610767`.
It requires its own fresh cook, full-range clearance, and runtime validation.

The cooker also has a live 1 GiB minimum physical/commit headroom guard. It
checks every five seconds and terminates only its own process on a low-memory
reading or guard-query failure, saving a failed receipt. A healthy guard or
completed cook never constitutes physics acceptance.


### V25: residual hip-roll cases

V24 applies five bounded pair repairs to V23. V25 additionally enables
shrinkWrap for torso 138/139 without changing other USD properties. Its fresh
83-mesh / 6140-hull cook passes 611/629 isolated poses at 1-degree resolution:
18 hip-roll poses fail, with 146 violating hull-pair observations and no
indeterminate distances. Rest passes. These are discrete geometric results,
not continuous clearance or dynamic acceptance.

The next grouped recipe uses all exact V25 witnesses. Its Manifold evaluation
accepts four of five operations; torso134 / hipL155 remains unresolved because
the torso result is non-manifold and the hip alternative removes 5.5464% of
source volume. An Exact evaluation of the same recipe is retained separately;
never accept a non-manifold result or increase the source cap to force it.

The quasi-static planner now records the complete requested swing transform
and accepts an explicit inward swing target. This changes a planning target,
not joint frames, limits, ownership, mass or collision filters. Every proposal
still needs all hard balance/placement constraints, combined-pose collision
screening, and native physical validation. Failed optimization is not proof
that all possible gait trajectories are infeasible.


### Numerical Boolean cleanup and bounded native cooking

V26 resolves all five V25 repair groups using a 0.1micrometer merge/dissolve
cleanup, with no hole filling. A separate 1micrometer bidirectional sampled
surface-change guard remains in force. Float32 BVH selection initially gave
false nonzero self-distances. The final measurement uses float64 triangle
distances and conservative float64 triangle-box candidates when BVH queries
miss skinny/degenerate faces. Four actual source-qualified meshes compare to
themselves below 7.1e-18m; parallel 1micrometer/1millimeter controls are exact.
Every vertex and face center is checked. These samples do not constitute a
continuous Hausdorff proof. Selected cleanup changes are at most 5.11e-8m;
independent volume/topology audits find four single closed triangle solids.

Fresh native cooking requires an exact candidate/snapshot, recent usage gate,
and a read-only resource preflight less than120 seconds old. The resource
receipt binds the current cooker and native interpreter hashes to the prior
successful native startup probe; it does not pretend to repeat SimulationApp
startup. Host physical/commit checks run every5 seconds independently of a
GPU query with15-second timeout and30-second maximum observation age. All
three memory categories must retain1GiB. Failed receipts remain failed.

V26's first attempt hit the GPU query timeout; retry R2 stopped at63/83 meshes
when host commit headroom fell below1GiB. Subsequent cooking disables viewport
updates and limits each fresh Kit process to20 meshes. Each batch has distinct
resource/usage/cook receipts and exact selected paths. `merge-cook-batches.py`
rejects missing or duplicate colliders, changed candidate/snapshot/fingerprints,
different cooker versions, failed batches, or bad output hashes. Only the
complete83-collider aggregate may enter the full clearance sweep. Cooking
never plays the physics timeline and cannot establish physical acceptance.

The default application still exhausted commit headroom during one batch's
startup. A pinned minimal Kit experience now loads the USD and PhysX cooking
dependencies, including the Isaac manual run loop and physics stage-update
backend. Rendering and the Fabric scene delegate are disabled. Forty colliders
cooked by both applications have identical hull arrays and compressed data.
Each fresh batch also pins the experience hash. All five minimal batches
complete; V26's full sweep passes 611/629 poses with 91 violating observations.

### Cooking fidelity trials

V27 raises the maximum hull count from 64 to 256 for parts 134, 138, 139 and
59, preserving geometry and all other USD properties. Its complete 83-mesh
cook contains 6910 hulls; exactly those four hull arrays change. The full
629-pose screen passes 621 poses, with 58 violating observations and no
indeterminate results. All remaining failures involve torso part 134.

V28 raises only part 134's voxel resolution from 200000 to 1000000. Exactly
one hull array changes, and the complete cook contains 6908 hulls. The full
screen again passes 621/629, with 57 violating observations and no
indeterminate results. Failed poses are left hip roll -12 and -11 degrees and
right hip roll 7 through 12 degrees. Higher cooking resolution does not
guarantee monotonic improvement. Both trials still fail the 2 mm requirement;
neither authorizes a physics run.

The next repair recipe uses every V28 witness, grouped into three mesh pairs,
with 1 mm of explicit cutter margin beyond the 2 mm target. The cumulative
5% source-volume cap, closed-solid requirement, protected foot geometry,
iteration ledger and numerical cleanup guards remain unchanged.

V29 applies those three operations to torso 134, with cumulative source-volume
removal 2.8636%. Independent inspection confirms one closed triangle solid.
Its fresh native hulls regress to 619/629 passing poses. V30 changes only that
part's decomposition errorPercentage from 0.5 to 0.05 and maximum hull count
from 256 to 1024. Its complete cook has 7678 hulls, and the full screen passes
626/629 poses: left hip roll -12 and right hip roll 11 and 12 degrees fail.
There are 23 violating observations, no indeterminate results.

The recipe generator accepts repeatable `--prefer-edit-node` arguments for
diagnosed cases where repeated decomposition of one mesh fails to converge.
The pinned recipe records this preference. Blender selects preferred nodes
only among alternatives that already passed every shape gate, then minimizes
incremental source-volume removal. Nonpreferred qualified alternatives remain
available. The default preserves minimum-removal selection.

The V30 recipe prefers hip nodes 155 and 59. Both pass, with cumulative source
removal 4.8005% and 1.2706%. The torso alternatives are rejected for a
nonmanifold result or no material removal. V31 materializes only these hip
changes and retains V30's torso. Fresh cooking and clearance remain required.

### Full isolated pass and combined-pose repair

V31 passes 628/629 isolated poses. One further right-hip repair produces V32,
which passes all 629 with no violations or indeterminate results. This does
not imply combined-pose clearance: two of four balance/lift poses still fail
around pelvis part 154 and thigh parts 33/55.

The planner now reports the initial fit and final constrained solver separately.
It selects a successful feasible constrained solution preferentially and requires
both solver success and hard constraints for `bounded_kinematic_pose_found`.
All four final V32 poses succeed. Sampling the transitions between rest,
reference, weight-transfer and lifted poses at no more than 1 degree per joint
produces 173 collision checks. V32 passes 145; V33 repairs thighs 33/55 and
passes 155. Intermediate balance and timing are not qualified by this screen.

V34 changes only thigh 55's shrinkWrap setting to true. Exactly one native hull
array changes; 82 remain identical. V34 passes **629/629 isolated poses and
173/173 combined samples**, with zero violations or indeterminate results.
Its USD hash is
`a7010cddae49d607eba6c1a9ff82b99213b5fc239f7b194742f337cba12a68c5`.
The prepared V33 pelvis cut remains an unselected alternative.

### Native gravity diagnostic

`scripts/validate-articulation-settle.py` binds the successful geometry reports,
converged plan, transition poses, native cook receipt, candidate, snapshot,
experience and fresh usage/resource gates. It steps the authored 120 Hz GPU
scene explicitly, leaving gravity, free root and self collision enabled.
The first real step initializes the native articulation; this bootstrap step
is recorded rather than hidden. No runtime root reset or support is applied.
Contact impulses and forces are read with divisors 1 and the exact physics dt,
and their scaling is checked independently. This diagnostic cannot accept
walking and does not produce the final visual proof.

Initial adapter attempts exposed a float32 gravity comparison error and a
tensor initialization-order issue; both are preserved as failed receipts.
The first actual V34 physics step also reported insufficient
`gpuFoundLostAggregatePairsCapacity`, which could miss interactions. That run
is rejected. `scripts/configure-physics-capacity.py` authors only this unsigned
scene property in a new candidate, verifies all other USD content is unchanged,
and requires native revalidation. V35 uses capacity 8192 versus the
runtime's reported need of 3271. Geometry limits and memory reserves remain
unchanged.

V35's fresh complete cook retains 83 meshes and 7678 hulls. Both clearance
screens pass again: 629 isolated poses and 173 combined samples. The candidate
hash is `796ed5d2a808ba50d64847e730d01cd0e5a7a30f09d541ba6eac6a4bc34ed2e8`.

The first three-second settle completed without a fall but failed its final
support-force criterion. A second run measured articulation sleep explicitly:
146 samples were asleep, and every body-ground force report became zero.
Sleeping tensor velocities were not treated as stability evidence. Both failed
receipts remain intact.

The third run uses explicit `--keep-awake`: it calls the native wake-up API
before each observed physics step, without setting body pose, velocity or
external force. All 360 steps complete, with 359 wake-up calls, no sleeping
samples, no fall, no joint-limit excess and all 7678 shapes present. The final
0.5-second median foot support is 99.7377% of body weight, within the original
90–110% interval. Maximum impulse-to-force scaling error is 0.0000611 N.
This passes the awake contact calibration only; physics and walking acceptance
remain false.

The same adapter supports an explicit `--motion-schedule` diagnostic. It
requires the successful awake settle receipt and the exact cooked hull file.
Each timed knot references an adjacent pose in a prefix of the pinned,
collision-screened sequence. Native actuation changes existing joint-drive
position targets only. Samples include commanded and actual joint positions,
foot contact impulses, sleep state, link transforms and foot heights computed
from native cooked hulls. A completed probe does not establish walking, dynamic
balance throughout the path, or conformance to the pinned motion reference.

### First loaded lift and drive calibration

V35 motion R2 executes the slow screened prefix after a 1.5-second settle:
narrow stance to 3 seconds, left weight transfer to 5 seconds, right lift to
7 seconds, then a hold. It falls at 7.775 seconds. No supported-airborne interval
passes the diagnostic criteria. No nonfoot ground force is reported.

Reconstructing the measured link transforms from measured joint angles and
the measured left-foot pose agrees with native positions to better than
0.4 micrometre at the inspected timestamps. The kinematic convention is
consistent. The loaded left foot-roll drive misses its command by up to
5.125 degrees. At 6.5 seconds, joint tracking error shifts the estimated CoM
38.4 mm inward relative to commanded angles at the same measured foot pose.
The nominal planner's support margin is only 5 mm.

The source terrain is a triangulated bump, with height ranging from zero to
80 mm. The adapter's hull-height reference is the highest terrain point,
not the local surface. Negative values against that reference do not establish
penetration. `analyze-motion-probe.py` now requires an explicit startup exclusion,
labels this conservative height bound, and reports native contact separations
separately. The initial 44 mm drop is not counted as a swing. The failed result
is preserved, including its original analysis and corrected post-settle report.

V36 is a controlled drive-gain trial. `configure-joint-drives.py` changes only
the two foot-roll joints' stiffness from 1800 to 9000 and damping from 180 to
400. Torque caps, ranges, geometry, mass and every other USD property are
preserved and verified after serialization. Its hash is
`4540d7a241cac29670122c683c23ca1132d4a54ccdcafadbdff6c843be945951`.
Fresh cooking, clearance, awake calibration and motion validation are required;
the gain change alone is not a pass.

V36's fresh native cook produces the same 83 hull arrays as V35, with 7678
shapes. Its isolated and original combined screens pass 629/629 and 173/173.
The awake calibration passes, but motion R1 falls at 7.4 seconds, earlier than
V35. Maximum loaded foot-roll error falls to 3.147 degrees while ankle-pitch
error rises. No supported airborne interval passes. This trial executes only
indices 0–50 of the original 173-pose path; it never executes the separately
proposed six-step path. The scope audit preserves those distinct input hashes.
Native drive readback confirms that authored angular gains use degrees:
USD stiffness 1800 is approximately 103132.4 Nm/rad in native tensors. Projected
joint forces are not treated as a separate implicit motor-effort measurement.

### Local terrain planning

`pacific_gym.terrain.TriangleTerrain` binds the source and candidate's exact
world-space terrain triangles. Their canonical geometry hashes must match.
It rejects overlapping triangle interiors, queries local triangle heights,
and clips a support footprint against each intersecting triangle to certify
that its selected plane lies above the entire terrain under that footprint.
The planner uses this conservative plane for swing clearance and keeps the
existing 5 mm support margin, 3 mm position tolerance, joint limits and root-up
constraint. Foot placement does not change protected geometry.

The legacy support polygons use source vertices within 2 mm of the local
surface. These are planning approximations, not measured native contact patches.
Native cooked feet have slightly nonflat soles; broad convex support faces may
join separated contact points. Runtime contact and stability evidence remains
necessary. Endpoint feasibility and discrete self-clearance do not prove balance
or terrain clearance between endpoints.

R5 searches landing-plane choices with a lookahead to the next weight transfer.
It finds six steps and a terminal transfer: 19 feasible endpoint poses and
0.1083 m predicted root displacement. Its centered narrow stance requires an
explicit, recorded initial configuration before physics. No runtime root reset
or dynamic acceptance is implied. The exact V35 hull sweep passes 349/352
samples; the three failures involve pelvis part 154 and left thigh part 33
around the first transfer. Failed paths and witness sidecars remain immutable.

R6 adds a witness-directed separating-plane constraint to only the first
weight pose, retaining all existing kinematic constraints. The solver succeeds
with 2.1 mm separation of the identified hull pair. A fresh full-path sweep is
required before any runtime probe; this local constraint alone does not certify
other hulls or intermediate states.

R6's full sweep passes 354/354. Its initialized centered pose also passes a
360-step awake native settle. Initialization is authored in the USD session
layer before physics attachment: root pose, zero root/joint velocities, joint
positions and matching drive targets. Source joint frames independently verify
the supplied FK matrices. After the first gravity step, native joint positions
agree within 0.000000502 degrees and body positions within 0.6813 mm. The source
USD remains immutable and no runtime root reset occurs.

A separate interpolation diagnostic finds up to 10.5 mm of vertex-terrain
penetration during later R6 stance switches. The full six-step sequence remains
unqualified. R7 tightens foot-frame tolerances to 0.25 mm position and 0.0005
rotation-matrix component error. All 19 endpoints solve, while the worst
interpolated vertex-terrain gap improves to -2.663 mm. Its full collision sweep
passes 388/396 with 32 hull-pair failures around the first weight transfer.
R8 attempts witness-constrained correction at those tighter foot tolerances,
but fails feasibility. Both failures are preserved.

R9 explores a smaller starting foot separation without changing source geometry,
joint ranges, balance limits or the pinned visual reference. Moving each foot
inward by 5 or 10 mm yields a feasible neutral pose and first transfer satisfying
the eight witness-pair constraints. A 15 mm inset fails. R10 searches the rest
of the local-terrain sequence with the 5 mm inset. These are controller planning
seeds; they do not replace the FLUX motion target or prove reference conformity.

R10 produces 19 feasible endpoints and passes 383/383 discrete self-collision
samples, with 0.1147 m predicted forward displacement. Its source-vertex support
approximation remains unqualified for runtime balance.

The bounded R6 native motion completes 1200 steps without falling, but the right
foot never establishes a supported airborne interval. Loaded left foot-roll
tracking error reaches 4.978 degrees. This is a failed lift, despite the diagnostic
motion completion flag. It executes only the initial transfer and lift.

### Exact cooked-foot contact planning

`Model.use_cooked_feet` binds the native cooked hull hash before terrain placement.
`TriangleTerrain.place_cooked_foot` enumerates lower support planes of the union
of those hull vertices. It maps a selected sole normal onto a terrain support
plane and constructs the potential support polygon from vertices within 0.1
micrometres of the exact terrain. These are geometrically possible contacts,
not measured native contacts. The entire footprint still requires a conservative
terrain-plane certificate. Protected foot geometry is unchanged.

R11 shows that choosing the largest sole contact area does not ensure reachable
balance. R12 finds a feasible first transfer and lift using a medial sole facet.
R13 selects medial facets for both feet at an exploratory starting translation
of +0.70 m X and +0.32 m Y relative to the centered narrow stance, on the existing
terrain. Its first transfer, 30 mm lift with 30 mm forward travel, and touchdown
are feasible. The next opposite-stance transfer fails with a -17.1 mm support
margin and 38.9 mm swing position error. Repeatable walking remains unproven.

R14 screens the feasible first-step subset at at most one-degree joint increments:
41/41 self-collision samples pass. R15 checks cooked foot vertices against exact
terrain heights across the same transition. Swing clearance reaches 29.80 mm;
the neutral right foot has a -1.713 micrometre numerical gap. Neither report
certifies continuous motion or native contact. R16 extracts only the feasible
neutral and three first-step poses, binds the extracted plan to a fresh 41/41
screen, and prepares an initial pose with approximately 2 mm vertical clearance
before physics attachment. The failed opposite transfer remains in R13.

The native adapter now supports reading position targets back from PhysX and
recording named body-pair self-contact forces and separations during motion.
These observations diagnose target delivery and competing contacts; projected
joint forces still do not separately measure implicit drive effort.

R16's native probe completes 1560 steps without a fall, invalid state, joint-limit
excess, root reset or external support. Position-target readback differs from
commands by at most 1.49e-8 radians, and no named self-contact is reported.
Nevertheless, the first step fails: the right foot reaches only 1.918 mm of
minimum local cooked-vertex clearance, with no interval meeting the 5 mm lift
criterion. The stance foot rocks during the attempted lift. At 9 seconds the
native root differs from commanded fixed-stance FK by about 56 mm; joint tracking
accounts for only part of this difference. Foot-origin displacement alone is not
evidence of contact slip. `analyze-local-terrain-motion.py` binds the native
receipt, trace, exact cooked vertices, snapshot, source and plan to this analysis.

R24 finds two feasible weight transfers with 18.5 cm foot separation, 15-degree
inward headings and a 20 mm initial left-foot forward stagger on an existing
flat terrain triangle. R25 then finds six steps and a terminal transfer, but only
94.7 mm predicted forward travel. R26 rejects the entire path: 0/361 discrete
samples meet 2 mm self-clearance. Failures include foot/foot and pelvis/thigh
pairs. Both positive subthreshold separations and zero-distance hull witnesses
occur; these must not all be described as interpenetrations. The sampled terrain
gap also reaches -0.362 mm. No R25/R26 physics run is qualified.

Subsequent layout searches apply conservative projected cooked-foot separating
plane certificates before kinematic optimization. R28's symmetric and R29's
asymmetric layouts do not yield both weight transfers under the existing
constraints. The feet remain protected geometry. A coupled stance search varies
foot layout and both sets of joint positions together while preserving the
5 mm support margin, strict foot pose tolerances, joint limits and a 3 mm
projected foot separation reserve. Any feasible result still needs exact full-body
and transition screening.

The next native adapter revision additionally compares every native center of
mass with the planning snapshot and records normal-impulse-weighted foot contact
centres. These additions have not yet run in physics; R16 used the earlier frozen
adapter. The prepared solver-count authoring script has not been used to create
a derivative.

### Coupled periodic stance and pelvis repair

R30 jointly optimizes the foot layout and both weight-transfer joint states,
finding a feasible pair with a 3.65 mm projected foot-separation certificate.
R31 extends the constraints to alternating left-leading and right-leading foot
positions. R32 produces ten steps and a terminal transfer, with 116.2 mm
predicted forward travel. The exact V35 path screen R33 passes 269/621 samples:
all 1,109 failures are pelvis/thigh pairs, with no foot/foot failures.

The R33 support-mode audit uses both feet's potential contact polygons during
weight transfer and the stance polygon during swing. Its minimum selected
support margin is 5 mm. Negative margins to only the destination foot during
double support do not, by themselves, show loss of support. The separate
interpolated terrain gap of -0.372 mm remains a path defect.

The native Blender repair for the complete R33 witness set changes only pelvis
collider node 154. Its cumulative removed source volume is 0.0326857%, within the
5% cap. V37 materializes that repair with all visual meshes, masses, joint
frames/ranges, filters and other colliders preserved:
`80e567fc680de062cea2f6c104f6cfe2f57f2169f1ef5932f2a0f6b748e3fc14`.
Fresh native cooking yields 83 meshes / 7678 hulls. The isolated screen passes
629/629, but the rebound 621-state path passes only 264/621. The source Boolean
repair therefore does not establish native clearance.

V38 changes only the repaired pelvis's decomposition settings: shrink wrap on,
256 maximum hulls, one million voxels, 0.1% error. Its geometry and every other
USD property remain unchanged:
`8ae5806055745eed1094c5bf647117c009ccdadd8a28f85c56c80faf01d5064c`.
Its fresh native cooking yields 83 meshes / 7,870 hulls. The isolated screen
passes 629/629. The R39 periodic path passes 886/1,236 states; all 479 failing
witnesses are positive, sub-2 mm pelvis/thigh separations, with no indeterminate
results or zero-distance witnesses. V38 is not qualified for a physics run.

R34 tightens endpoint foot-position tolerance to 10 micrometres and orientation
matrix tolerance to 0.00002. R35's ten-step endpoint plan is feasible. R36's
intermediate projection stops with an unsuccessful optimizer termination despite
a feasible numerical residual; R38 passes that point but later fails at a
left-foot touchdown. Neither incomplete trajectory is accepted. R39 extracts
only a fully converged two-step prefix, closes it with the already-converged
left-weight state, and verifies all repeated states after translation. Its
1,236 states pass the original joint/support/root constraints and the tighter
foot constraints; predicted travel is 115.9 mm. This is sampled kinematic
evidence, not continuous or native walking acceptance.

`rebind-collider-only-plan.py` permits plan reuse only with audited lineage,
identical bodies/joints/masses/limits, identical native cooked feet, and identical
world-space terrain. It recomputes placement digests and requires a fresh
full-body sweep. Audited non-foot cooking-setting changes may follow the
materialized repair in the lineage.

R39's additional quarter-interval audit evaluates 6,175 samples. Its minimum
selected support margin is 5 mm and minimum root-up Z is 0.95000049. The minimum
local terrain gap is -14.89 micrometres, so this is still sampled evidence and
does not establish continuous nonpenetration. `prepare-quasistatic-initial-pose.py`
requires the exact frozen planning modules, candidate and input hashes. It
prepares zero velocities and a disclosed vertical clearance before simulation
attachment; it performs no runtime resets.

A further Blender repair using the full V38/R39 witness set selects only pelvis
node 154. Cumulative removed source volume becomes 0.0369373%, below the original
5% per-part cap. Other collider geometry and the protected feet remain unchanged.
V39 freshly cooks 83 meshes / 7,870 hulls and passes 629/629 isolated poses.
The same full R39 path passes 941/1,236 states. Its 473 failing witnesses remain
positive sub-2 mm pelvis/thigh separations, with zero indeterminate results.
V39 is not qualified for physics.

V40 retains V39 geometry and increases only the pelvis decomposition limit from
256 to 1,024 hulls and reduces its error setting from 0.1% to 0.05%. Its exact
candidate is `e46adbf8e3138d3738f8e5aad871eb8ae1393d6c72065877f8fc87ef2ab18954`.
V40 freshly cooks 83 meshes / 8,638 hulls and passes 629/629 isolated poses.
Its R39 path passes 989/1,236; all 533 failures are positive sub-2 mm
pelvis/thigh witnesses. At the worst witness for each pair, exact nearest-source
triangle queries put the opposing point about 3 mm from the repaired pelvis,
while the native pelvis witness is 1.78–2.18 mm from that authored surface.
These two point queries support a cooking-approximation diagnosis; they are not
a whole-mesh source clearance certificate.

The repair planner now allows an explicit cutter reserve up to twice the
configured target clearance, while retaining the same Blender volume, protected
geometry and iteration gates. This changes the repair reserve, not the acceptance
clearance. V41 uses a 3 mm reserve beyond the 2 mm target to accommodate the
measured cooking discrepancy. Only pelvis node 154 is selected, with 0.118674%
cumulative removed source volume. Candidate:
`9f629ceb70e286cc5a2fda50b44537ad22dd43717d836d44d16b01d8ec0512e7`.
V41 freshly cooks 83 meshes / 8,638 hulls, passes 629/629 isolated poses and
1,236/1,236 periodic path states. A prepared 22-second
first-step probe uses three seconds of initial hold, six seconds per transfer,
lift and touchdown, then one second of final hold. It cannot launch without both
clearance screens and a fresh awake settle passing.

V41's settle launch rejects a PhysX aggregate-pair capacity error during its one
unobserved bootstrap step, before the measured settle loop. PhysX requests 8,555
entries against the authored 8,192. About 11 GiB GPU memory remains free; this is
not a GPU OOM or an observed gait failure. V42 changes only that scene capacity
to 16,384, preserving all other USD content:
`1fa01dc06cc88e69cadb4882b04fb3a733ba34acc2fdb599cd302afb4d78caf2`.
Fresh cooking, screening and settle are required. The plan-rebinding helper now
accepts this explicit, audited buffer-increase receipt; it still requires exact
body/joint/foot/terrain equality and fresh full-body clearance.

### V42 native contact failure and controller diagnostics

V42 freshly passes 629/629 isolated and 1,236/1,236 periodic poses, then passes
360 native awake settle steps. The median final support/weight ratio is
0.99731867, minimum root-up Z is 0.99188250, and the native shape count is 8,638.
This average-force calibration does not establish quiet or robust foot contact.

The 22-second R39 first-step motion stops on the fall guard after 1,534 steps
(12.78333 seconds), during lift. There is no invalid physics state and no
reported named self-contact. Trace SHA:
`e836a7029da01bf5c0004519fcb4802cc70bf64630028601f0ebf9d93fd216bc`.
Neither foot achieves a supported airborne interval. The right foot's maximum
minimum cooked-vertex gap is 1.732 mm, below the 5 mm criterion.

Root-aligned forward kinematics from actual joint positions matches native foot
positions within 0.5 micrometres at the audited times. The target readback error
is below 1.5e-8 rad. These observations do not support a left/right mapping
explanation. At 11 seconds the root-error decomposition is about 0.8 mm from
joint tracking versus 63 mm from loss of the assumed stance anchor.

At 9 seconds the native CoM is about 10.3 mm from the planned CoM. The intended
left support margin is approximately 5 mm, while the measured loaded polygon
margin is -26.1 mm. At 9.5 seconds it briefly becomes +0.162 mm; later loaded
polygons often have fewer than three points, so their margins are undefined.
The native sole normal tilt grows from about 0.16 degrees at 9 seconds to
2.26 degrees at 10 seconds. Potential geometric support polygons must not be
substituted for these measured loaded patches.

Offline fixed-layout, coupled-layout and bent-knee searches for 10–15 mm
support reserves have not found a feasible replacement weight transfer.
An isolated 8 mm lift endpoint converged, but that does not qualify a path.
Failed local solves are not global infeasibility proofs.

The stationary 2–3 second hold also contains substantial force and reported
joint-velocity oscillation. Right foot-roll reported velocity RMS is
0.528 rad/s, while the backward difference of its positions is 0.00209 rad/s.
Reconstructed ideal PD demand often exceeds the 300 Nm cap, but it is not a
measurement of implicit drive torque or actual saturation. A separate
damping-only diagnostic can now scale the existing damping in the prephysics
session layer, preserving stiffness, torque caps, limits and geometry. Its
configuration is recorded and checked against native per-radian readback;
motion requires a successful calibration with the same damping scale.
The R48 hold validates scale 0.02 for 360 awake steps. During its 2–3 second
stationary window, left/right force standard deviations fall from
31.40%/33.47% of body weight to 0.266%/0.275%. Root X range falls from
3.52 mm to 0.015 mm. Stiffness, 300 Nm caps, limits and geometry are unchanged.
These are measured positions and contact forces; the ideal PD reconstruction
is not used as evidence of actual drive saturation.

R49 completes a ten-second transfer-only diagnostic without falling. During
the final 9–10 second hold, measured left loaded-polygon CoM margin remains
5.081–5.251 mm. Foot-origin horizontal displacement stays below 0.136 mm.
Minimum root-up Z is 0.94990635, below the required 0.95; no walking acceptance
is claimed. The frozen adapter's `motion_probe_pass` checked completion and
support but omitted this uprightness threshold. The current adapter explicitly
requires and records `uprightness_pass`.

The current static gate also requires the last half-second root range to stay
within 1 mm on each axis and each foot's vertical-force standard deviation
within 5% of body weight. Subsequent motion requires a matching calibration
that passed this quiet-contact gate.

### Native capture preparation

`native_capture.py` uses explicit zero-time Replicator captures. It checks USD
link transforms against native physics, then requires link poses, joint
positions, root velocities and joint velocities to remain exactly unchanged
by rendering. Capture must initialize successfully before physics attachment.
Per-frame hashes and a manifest are required; infrastructure alone is not proof.

R40/R41 produced no RGB frames. The rendering experience incorrectly supplied a
boolean renderer name and omitted the synthetic-data history setting. The
corrected configuration uses renderer `"rtx"` and three history frames.
R42 then hit the existing 1 GiB memory-reserve guard before physics startup.
The triggering sample was not recorded by that frozen adapter; its last
successful sample had 2.24 GiB available commit. The generic guard now records
the breached fields and sample before aborting.

R47 passes a two-second native static calibration using a two-thread renderer
without an unused initial stage, while preserving the memory guard. All 24
640×640 frames preserve the native state exactly; MP4/GIF encoding, manifest
hash checks and MP4 decoding pass. This is static native capture only.
R50 passes a three-second capture with the lower damping and the quiet-contact
gate. All 36 frames preserve the native state. The final half-second root
ranges are below 10 micrometres per axis; left/right force standard deviations
are 0.182%/0.205% of body weight. MP4/GIF hashes and MP4 decoding pass.
Native walking video and final `isaac-run` acceptance remain outstanding.

### Uprightness reserve after damping recovery

R51 fixed-layout searches do not find a weight pose with root-up 0.951 or
0.952, although the lift endpoint remains feasible. R52 coupled-layout searches
converge at 0.952 and 0.955 with the original 5 mm support margin and exact
six-dimensional foot closure. The selected 0.955 layout uses width
0.1917043845 m, left heading −20.114323 degrees, right heading 9.893273
degrees and 15 mm stagger. Foot meshes, joint limits and terrain are unchanged.

R53's minimum-angle weight refinement moves away from a feasible seed and
fails. R54 preserves the converged weight poses using a nearest-reference
objective and finds all ten step endpoints, with 0.11584 m predicted root
travel. R56 projects the first two steps into 275 dense constrained transitions;
failed numerical solves may restart from their last iterate, retaining the
same constraints and recording retry history. This is offline kinematic work.
R57 expands that template to 1,224 states, all of which pass fresh full-body
clearance. Its 6,115 interpolation samples retain root-up at least 0.955 and
support margin 5 mm (within existing numeric tolerance). Minimum sampled
terrain gap is −14.86 micrometres. This remains a sampled path check.

R57 and R58 captured calibration attempts stop before physics because available
Windows commit falls below the unchanged 1 GiB reserve during renderer
startup. Reducing general CPU workers from two to one does not resolve it.
R59 uses physics-only calibration. R60 applies app-local
`/rtx/shaderDb/concurrency=1` and
`/rtx-transient/psoDb/serializeAllCompiles=true`, settings observed in the
installed GPU foundation binary. Runtime readback confirms both settings, but
capture still exhausts the available commit reserve. No unrelated process is terminated.

`analyze-walking-motion.py` binds native samples to geometry, plan and schedule.
It measures alternating supported airborne spans, loaded landings and forward
foot travel; phase timing only chooses measurement windows. It additionally
requires three cycles, 0.1 m native root travel, uprightness, awake/free-root
physics and no external support. It does not promote a run: captured video and
the final `isaac-run` gate remain required. R49's transfer-only trace correctly
reports zero cycles and fails these criteria.

R59's physics-only calibration passes 360 steps, both quiet-contact and
uprightness gates, with minimum root-up 0.99297965. Its first-step motion probe
falls after 1,505 steps / 12.5417 seconds during lift. Neither foot has a
supported airborne interval; the right foot's maximum minimum local vertex
gap is only 0.403 mm. R60 reads back the requested shader
settings, but still stops before physics at 0.580 GiB available commit.
The installed pagefile is 2 GiB; available commit at R60 startup is 7.60 GiB.
The resource preflight now supports a stricter startup commit requirement
without weakening its 1 GiB floor or the runtime guard.

The prepared `isaac-walking-scenario.py` adapter requires a new native run.
It then invokes the measured-step evaluator, verifies every captured frame
against the native state hashes, encodes and decodes the MP4, and writes the
receipt consumed by `isaac-run`. Its frozen configuration pins the native
adapter, analysis code, planning modules and encoder. This adapter has only
been syntax checked; no successful full walking run has exercised it.

The native validator's optional `--repeated-cycle-probe` allows at most 240
seconds only with native capture and a screened schedule containing at least
six alternating completed step targets. Default motion diagnostics retain their
30-second limit, and static diagnostics retain their 10-second limit.
Neither target count nor elapsed time establishes walking;
the measured-step and video gates remain separate.

### Lift tracking and timing diagnostics

R61 inventories larger support facets on the existing cooked feet. R62's six
coupled searches at 5, 10 and 15 mm support reserve find no feasible solution.
This is a failed local search, not proof that all placements are infeasible.

R63 checks R59's actual joints against native foot transforms after aligning
the computed root to the measured root. Position agreement is below one
micrometre. At 11 seconds, joint tracking contributes only −0.050 mm of the
right-foot vertical error, while loss of the fixed stance contributes
−9.624 mm. The commanded lift reaches the correct foot and world axis;
the free body rocks and drifts instead of preserving the planned stance.

The native schedule optionally supports bounded quintic rest-to-rest phase
timing. It preserves the ordered, adjacent screened joint path and records
the evaluated path time in every motion sample. The walking analyzer maps
phase knots back to physical time when selecting measurement windows.
R64 stopped after startup because the first implementation reused a root
position variable in the timing loop. R65 fixes that variable collision and
runs the same first-step path with smooth timing and a one-second hold after
weight transfer. The live memory guard aborts R65 before lift: its last saved
observation is step 1,081 / 9.0083 seconds, root-up 0.9549283, and the breached
Windows commit sample is 499,212,288 bytes (0.465 GiB). This does not test the
timing hypothesis through lift. Its full trace was not saved before the hard
guard exit.

R66 reconstructs the required ground wrench for the prescribed fixed-foot
path using native mass/inertia readback and finite-difference accelerations.
The original timing has one lift sample whose required center of pressure
falls outside the support polygon; the smoothed timing has none and retains
at least 4.65 mm margin. This calculation does not establish contact-force
allocation, motor feasibility or native balance. It does not prove the cause
of R59's fall.

To preserve future aborted diagnostics, the native validator now writes
separate atomic gzip checkpoints every 60 observed steps. Each completed
chunk is hash-bound in the receipt. The hard memory exit remains immediate;
incomplete temporary files are not listed as evidence. The new
`recover-motion-checkpoints.py` reconstructs only the contiguous, listed
prefix of a failed run and labels it incomplete. These additions have been
syntax checked but have not yet been exercised by a new native run.

Native robot-shape friction, restitution, contact-offset and rest-offset
readbacks are also prepared for the next frozen adapter. They do not infer
the terrain material. Field ordering follows the
[Omni Physics tensor API](https://docs.omniverse.nvidia.com/kit/docs/omni_physics/107.3/extensions/runtime/source/omni.physics.tensors/docs/api/python.html#omni.physics.tensors.impl.api.ArticulationView.get_material_properties).

R67 broadens the exploratory placement bounds; R68 additionally checks every
polygon separating axis instead of a fixed direction. Local solves approach
5.956 and 6.021 mm coupled support margins, respectively, but return
unsuccessful optimizer statuses and are not qualified candidates.
R69 instead holds the R57 placement fixed and maximizes support through the
first lift. Seven exact-foot-pose solves converge: the local support optimum
rises from 5.028 mm at ground contact to 8.438 mm at a 30 mm lift. R70 uses
this evidence to prepare a denser first-step path with its required margin
rising from 5 to 8.3 mm. All 279 poses converge, with maximum adjacent joint
change 0.499014 degrees. Five-fraction interpolation checks cover 1,390
samples: root-up stays at least 0.955 within numerical tolerance, local foot
gap stays nonnegative within floating-point precision, and required support
margin slack is no worse than −0.060 micrometres.

R70's fresh body-clearance screen passes only 61/279 poses. All 1,406 failing
hull-pair observations concern pelvis node 154 against right-thigh node 55.
The grouped repair recipe prefers pelvis node 154, contains 218 distinct
cutters for that alternative, and keeps the 2 mm clearance target with a
declared 3 mm cooking reserve. It preserves the cumulative source ledger.

R71's non-mutating convex-envelope estimate gives an upper estimated
cumulative removal fraction of 0.00524796 for the pelvis: **0.525%**, compared
with the configured fraction 0.05: **5%**. The thigh alternative is estimated
at 0.630%. Both estimates are below the cap, but neither replaces the actual
Blender volume, topology and source-distance checks. No geometry has been
changed by these estimates.

R72 stages that exact recipe and the previously verified repair script on
L-GPC. Its startup probe requires at least 4 GiB available commit and the
usual 1 GiB physical/GPU reserves before Blender can start. The repair has
not run: the latest observed L-GPC commit headroom is only 1.28 GiB. Fresh
usage above the human 20% floor, actual shape qualification, new native
cooking and both clearance sweeps are required before a resulting candidate
may enter native motion validation.
