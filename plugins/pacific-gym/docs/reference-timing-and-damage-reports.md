# Source timing and damage-locomotion reports

These are prepared offline code paths. They do not launch Blender, Isaac or a model, and do not establish a FLUX match or physics acceptance. The intact walking analyzer and run-completion gate are unchanged.

## Reference keyframes: manifest version 2

The existing `extract-keyframes` command and standalone `scripts/extract-keyframes.py` retain their arguments. FFprobe supplies the selected video stream's integer frame PTS and rational time base. Extraction selects decoded presentation-frame indices explicitly, then checks FFmpeg `showinfo` timestamps against that selection. It does not use an FPS filter to invent timestamps or duplicate frames.

Each frame has a `source_frame` object:

| Field | Meaning |
|---|---|
| `video_sha256` | Hash of the original video bytes, verified before and after extraction |
| `stream_index` | Absolute video stream index selected by `v:0` |
| `decoded_frame_index` | Zero-based index in decoded presentation order, not packet order or PNG numbering |
| `pts` | Original integer presentation timestamp; negative and nonzero origins are retained |
| `time_base` | Exact `[numerator, denominator]` seconds per PTS tick |

The manifest repeats `source_stream_index`, `source_time_base`, and `timestamp_origin_pts`. The existing `timestamp_seconds` field is the elapsed time from the first source frame: `(pts - timestamp_origin_pts) * time_base`. It is a convenience float; the integer PTS and rational time base remain authoritative.

Sampling chooses the first available source frame at or after each tick anchored at the first PTS. Gaps skip unavailable ticks; high requested rates do not duplicate frames. This can differ from the old FPS-filter output. Autorotation is explicitly disabled so output dimensions match decoded source dimensions; no crop or resize is applied. The filter and extraction log are retained. A failed timestamp/count/dimension/hash check leaves no completed manifest; partial diagnostic output is preserved.

`load_references` validates and retains v2 identity. Candidate sidecars carry it as `reference_source_frame`; candidate readiness and cached judgment reuse require exact equality. A judgment's `pair.actual_target.source_frame` retains the identity. Existing v1 manifests still load, but do not acquire provenance retroactively. Re-extract into a fresh directory and republish candidates to migrate; do not rewrite historical manifests or judgment receipts.

Limitations: actual PTS is mandatory; missing or decreasing PTS, changing frame dimensions, changing filter time bases, or mismatched decode output fail closed. Decoding occurs in both FFprobe and FFmpeg and can be expensive for long sources. Tool versions must support `-fps_mode passthrough`, `-filter_script:v`, `-copyts`, `-noautorotate`, and the inspected `showinfo` output. `-filter_script` remains supported but is deprecated in the locally installed FFmpeg 8.1.1. This change was statically reviewed; no extraction was run for this task. Source identity does not itself certify full-video coverage, tracking quality or physical validity.

## Separate damage report

Run the standalone offline entry point with complete future damage evidence:

```sh
PYTHONPATH=plugins/pacific-gym python3 -B -m pacific_gym.damage_locomotion \
  --spec damage-spec.json --receipt native-damage-receipt.json \
  --trace damage-trace.json --candidate damaged.usda \
  --reference-manifest reference/manifest.json --out damage-report.json
```

The output must be new. Input hashes are checked again before writing. Unsupported/missing evidence raises an error instead of producing a passing report. All three JSON inputs below use integer `schema_version: 1`.

### Frozen specification (`kind: damage_locomotion_spec`)

Required top-level fields:

- `baseline_candidate_sha256`, `damaged_candidate_sha256`, `reference_manifest_sha256`.
- `event`: nonnegative integer `seed`, `kind` (`link_removed`, `joint_locked`, or `actuator_disabled`), and named `target`.
- `morphology`: `root_link`, named `links`, and named `joints`.
- `forward_axis`: a unit three-vector; `allowed_contact_links`: explicit remaining collidable links. Damage-specific support contacts can differ from intact foot contacts.
- `reference_channels`: named entries containing `link`, `joint` (name or null), `unit` (`m` or `rad`), and nonnegative `maximum_error`. Joint channels belong to the joint's child link. An unmasked root-position channel is required. Errors must come from separately calibrated measurements; image pixels are not metres.
- `criteria`: positive `minimum_post_damage_duration_s`, `minimum_forward_m`, `stable_window_s`, `maximum_sample_gap_s`; nonnegative `maximum_recovery_s`, `maximum_contact_slip_m_s`, `locked_angle_tolerance_rad`; `minimum_up_z` in [-1, 1], and `minimum_support_fraction` in (0, 1]. Choose and commit these from the intended baseline before collecting evidence. The reporter does not invent defaults or prove that baseline was accepted.

Present link entries contain exactly `state: present`, positive `mass_kg`, three-vector `com_m`, symmetric positive-definite 3x3 `inertia_kg_m2`, `geometry_sha256`, and boolean `collision_enabled`. Removed links contain only `state: removed`: retaining their old mass, inertia or colliders is rejected. Root removal/replacement is unsupported.

Joint entries contain `state`, `parent_link`, and `child_link`. Removed joints contain only those fields. Remaining joints also require `lower_rad`, `upper_rad`, and `max_effort_Nm`. Actuated joints require positive authority. `free` and `locked` joints require zero actuator authority; locked joints additionally provide `locked_position_rad`. These states are distinct. Constraint reaction forces must not be reported as actuator effort.

Channels on removed links or removed/locked/free joints are explicitly listed as masked, with reasons. Every retained channel must be measured at every post-damage step. Arbitrary masking of surviving actuated channels is not supported. The declared channel set is not independently proven to cover the entire reference.

### Native receipt (`kind: native_gpu_damage_locomotion`)

Required fields:

- `spec_sha256`, `trace_sha256`, and the specification's three lineage hashes.
- `observed_morphology`: complete native read-back matching the frozen physical descriptor; `native_link_names`, `native_joint_names`, and `native_root_link`: actual remaining topology.
- `damage_event`: specification event fields plus `time_s` and `reference_phase` in [0, 1). The event must occur at a recorded interior physics step after forward motion has started. Seed and phase are recorded provenance, not proof of a randomization distribution.
- `host`, `gpu_device`, `status`, `root_kinematic`, `self_collision_enabled`, and positive `gravity_m_s2`. Passing reported criteria require L-GPC, a named GPU, completed status, a free root and self-collision enabled.

### Trace (`kind: damage_locomotion_trace`)

Required top-level fields are `spec_sha256`, `candidate_sha256` (damaged candidate), `root_link`, positive `dt_s`, and `samples`. Samples must include at least two before and two after damage, with consecutive integer `step` values and continuous `time_s` at the declared physics interval.

Every sample supplies `root_position_m`, `up_z`, `morphology_state` (`intact` before the event; `damaged` from the event onward), boolean `fall_detected`, `invalid_physics_state`, `external_support_applied`, and nonnegative integer `root_resets` and `forbidden_collision_count`.

Every post-damage sample additionally supplies:

- `joint_position_rad` and `actuator_effort_Nm`, keyed by every remaining joint, including failed joints.
- `reference_errors`, keyed by every retained reference channel.
- `contacts`, keyed by actual contacting links, with `force_N` (three-vector, Z-up) and nonnegative `slip_speed_m_s` for each.

The report computes post-event forward displacement, duration, contact slip, limit/effort excess, locked-joint error and maximum retained-channel errors. Recovery is the time when the first continuously stable support window reaches `stable_window_s`, measured conservatively at the window's confirmation endpoint. Support uses the remaining body's mass, gravity, allowed ground forces and the declared posture/slip bounds. It requires no falls, invalid states, forbidden collisions, resets or external support throughout the supplied run.

### Interpretation and limitations

`damage_locomotion_criteria_pass` describes only the supplied measurements against this distinct frozen specification. `damage_locomotion_accepted`, `walking_accepted`, `physics_accepted`, `flux_match`, and `native_provenance_verified` remain false. The reporter cannot promote a run or relax `run.verified_isaac_check`, `analyze-walking-motion.py`, or a frozen intact walking gate.

No native damage-event producer or read-back exporter is implemented here. Current intact receipts/traces lack this contract and are rejected. Hash consistency and a reported host name are not execution attestation. Structural severance, recooking, actuator failure injection, native visual proof, independent reference measurement and repeated damage trials remain future native work. No tests, media extraction, Blender/Isaac runs, Liquid calls, model downloads, or capacity polls were performed for this implementation task; available validation is static syntax and diff review only.
