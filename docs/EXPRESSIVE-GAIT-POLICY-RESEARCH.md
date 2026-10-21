# Expressive gait and perceived eeriness: research addendum

Date: 2026-10-20
Scope: targeted primary-source review of motion cues and policy structure for adding an intimidating or eerie quality while preserving the pinned FLUX motion. No training or GPU job was run.

## Recommendation

Keep the pinned FLUX clip as the reference target. First make a phase-conditioned, contact-aware reference-tracking policy work with style intensity set to zero. Then add one explicit style control and evaluate small, declared changes around that reference. Keep match, stability, and human eerie/intimidating ratings as separate outcomes.

Do not use “asymmetry,” “pathology,” “sadness,” or a VLM score as a proxy for horror. Published gait-perception results show that visible abnormality and eeriness are related but not equivalent, and the findings come from human or simplified lower-body walkers rather than Strokah.

## Relevant evidence

### A controllable style variable can sit above a physics tracker

The September 2026 EMoG preprint uses a continuous emotional-style code and phase/time features to generate a target gait trajectory; a separate reinforcement-learning motion tracker executes that trajectory. It combines trajectory reconstruction, command consistency, symmetry, and temporal smoothness losses. In its ablations, removing consistency or symmetry increased path drift. Its user study found stronger style recognition at stronger style intensity, but some styles remained easy to confuse with neutral. This is a useful architecture pattern for Strokah: separate reference/style generation from low-level physical execution and validate each layer. It is not evidence that EMoG’s style labels, data, G1 robot, or results transfer to Strokah; it has no “horror” category.

### Posture and a few joints carry emotional information

Roether et al. motion-captured expressive walks and tested animated walkers with observers. They found that speed alone was insufficient to distinguish several emotions; posture and joint-specific dynamics mattered. Elbow flexion strongly influenced perceived anger and fear intensity, while head inclination strongly influenced sadness. Their study also found meaningful leg-motion cues and left/right asymmetries. This supports testing a small set of interpretable style controls (torso/head pitch, elbow/arm articulation, timing) instead of a generic “make it scary” reward. It does not establish which cues will make a bulky mech frightening.

### Abnormality does not guarantee eeriness

Handžić and Reed systematically varied cadence, knee placement, step length, swing time, foot rollover, and knee damping in a 2-D passive dynamic walker. Observers did not distinguish ±25% cadence changes from normal in their impairment ratings; ±50% changes produced only slight uncanniness. Five-percent step-length or swing-time asymmetry was not perceived as impaired, while 13% was. The authors found uncanniness ratings generally weaker than impairment ratings and noted their lower-body-only model could understate uncanny effects. Treat these values as evidence to run perceptual sweeps, not as thresholds to copy into Strokah.

## Strokah policy design

1. **Reference baseline:** retarget the entire 6.04-second, 24-fps FLUX clip. Preserve its measured phase, posture, foot timing, arm pose, and root path. Train or control a phase-conditioned policy to track the motion while respecting measured joint limits and real contact.
2. **Separate style control:** add a scalar intensity `h ∈ [0,1]` and a small parameter vector for selected style offsets. Initially test one cue at a time: torso/head lag, restricted or delayed arm response, small swing-time asymmetry, and a deliberate weight-transfer pause. Keep all offsets within authored joint ranges.
3. **Constrained objective:** reward reference tracking, contact correctness, and survival first. Let a small style reward operate only while tracking and physics gates pass. Penalize falls, invalid states, self-collision, slip, penetration, torque spikes, and excessive action jerk. Do not let a style reward trade away the reference silently.
4. **Perceptual selection:** render matched candidates with identical camera, scale, lighting, and duration. Randomize and blind the clips. Ask raters separately for (a) match to the FLUX motion, (b) eerie/uncanny quality, and (c) intimidating presence. Include the unmodified FLUX-tracking baseline and neutral controls. Use Liquid only after calibration on known matched and mismatched ordered clips; keep its response advisory.
5. **Ablations:** compare baseline, each single cue, and a small set of combinations. Report tracking error, stance slip, falls, completion, style rating, and inter-rater agreement per candidate and seed. Select from a Pareto set rather than hiding motion-match loss inside a scalar score.

This is a research-grounded experiment design, not a validated horror reward or a claim that the current Strokah rig can execute these motions. The geometry, joint-limit, and contact gates still precede policy training.

## Primary sources

- Lu et al. (2026), [EMoG: Emotion-Modulated Gait Generation for Expressive Humanoid Locomotion](https://arxiv.org/abs/2609.14432), arXiv preprint posted 2026-09-13.
- Roether et al. (2009), [Critical features for the perception of emotion from gait](https://doi.org/10.1167/9.6.15), *Journal of Vision* 9(6):15.
- Handžić and Reed (2015), [Perception of gait patterns that deviate from normal and symmetric biped locomotion](https://doi.org/10.3389/fpsyg.2015.00199), *Frontiers in Psychology* 6:199.

## Policy implications for the next Strokah iteration

The current repository has reference-generation, keyframe judging, geometry, and Isaac-readiness workflows, but no locomotion policy trainer or task-specific learned gait policy. Treat the following as an implementation direction, not as a capability that already exists.

### Use a pinned-reference tracker as the locomotion base

For a single supplied FLUX walk, use a motion-tracking objective before an adversarial style prior. DeepMimic demonstrates a physics-based controller trained from a reference clip with a task reward alongside imitation; this structure fits the need to preserve a particular walk. A residual controller can keep the reference trajectory as the nominal motion and learn corrections for dynamics and contacts. RuN (2025) and I-CTRL (2024) are recent primary examples of separating a motion prior or retargeted reference from residual physical corrections. They use different robots and do not prove Strokah will train successfully, but they provide a more direct starting pattern than asking a policy to invent a walk from scalar forward-speed rewards.

Treat the reference as timestamped whole-body targets: root pose/path, joint pose, feet, and phase. Compute matching on held-out frames and on a continuous roll-out; do not judge only selected stills. If a policy adapts pose online, log both its residual from the reference and its physical outcome. Keep style intensity `h=0` as a reproducible baseline.

### Use motion priors only as a bounded style term

AMP learns a discriminator reward from a set of example motions and combines it with task rewards. That is useful if Strokah later has a curated dataset of the desired menacing movement. A discriminator trained on heterogeneous clips can reward membership in the broad dataset while departing from the exact FLUX walk, so it should be a low-weight style regularizer or a separately evaluated policy branch. Do not use it as the motion-match criterion.

Use an objective with explicit logged components rather than an opaque combined score:

```text
r_track = weighted pose + root-trajectory + foot-path + contact-phase agreement
r_style = discriminator/style reward or a small explicit style-control target
r_task  = forward progress and completion
constraints = no fall/invalid state, authored joint limits, bounded slip,
              no forbidden self-collision, and bounded torques/actions
```

The constraints should remain gates in reporting and candidate selection. A large `r_style` must not buy a fall, bad contact, or silent tracking loss. Preserve per-term traces so it is clear which objective changed the gait.

### Test intimidation as a perception outcome

Human gait studies report that anger is often read from faster cadence, longer strides, and stronger arm motion; posture and selected joints also carry emotion information. These findings suggest measurable candidate controls, but they do not show that the same cues make a large armored mech intimidating. In particular, deliberately irregular timing, freezes, head tracking, or slow heavy steps are design hypotheses for this character, not established horror rewards.

Run a small, isolated design sweep around the baseline: cadence/stride amplitude; upper-body and head orientation; arm restriction or lag; and one controlled pause/acceleration pattern. Change one control at a time first, then test only the best combinations. Keep each variant within the declared joint and collision limits. Randomize clip order and camera-match the renders. Ask people to rate motion-reference match, intimidation, eeriness, and physical plausibility as separate questions. Include neutral and original-reference controls. Select from the Pareto set instead of collapsing these outcomes into one score.

### Calibrate Liquid before trusting its match report

The existing Liquid calibration history includes three failed controls. Before using it to claim a match, test it on a blinded, fixed set containing the exact reference, a known close temporal retiming, a wrong-phase walk, and clearly mismatched motions, rendered with the same framing and duration. Require per-clip timestamps or ordered sequence comparison and save the prompt, model/version, raw responses, and input hashes. If it fails the controls again, keep it advisory and use human ratings for selection. A positive response on uncalibrated images is not evidence that the complete walk matches.

## Additional primary sources

- Peng et al. (2018), [DeepMimic: Example-Guided Deep Reinforcement Learning of Physics-Based Character Skills](https://arxiv.org/abs/1804.02717). Reference imitation combined with task rewards for physics-based skills.
- Peng et al. (2021), [AMP: Adversarial Motion Priors for Stylized Physics-Based Character Control](https://xbpeng.github.io/projects/AMP/index.html). A discriminator-derived motion-style reward from example clips, combined with task objectives.
- Yan et al. (2024), [I-CTRL: Imitation to Control Humanoid Robots Through Constrained Reinforcement Learning](https://arxiv.org/abs/2405.08726). Bounded residual refinement of retargeted reference motion across several simulated robots.
- Li et al. (2025), [RuN: Residual Policy for Natural Humanoid Locomotion](https://arxiv.org/abs/2509.20696). A motion prior with a residual RL controller for physical corrections.
- Halovic et al. (2017), [Not all is noticed: Kinematic cues of emotion-specific gait](https://doi.org/10.1016/j.actpsy.2017.10.010). Reports pace, cadence, stride length, and arm swing as cues in human emotion-recognition tasks.
- Roether et al. (2009), [Critical features for the perception of emotion from gait](https://doi.org/10.1167/9.6.15). Observer-tested posture and joint-specific motion cues for human gait perception.

This addendum narrows the recommended first experiment: achieve the exact-reference tracker baseline with style weight zero; then add controlled expressive offsets and measure intimidation separately. It does not define a validated “horror” reward, and the available geometry/contact evidence still blocks an honest locomotion-policy acceptance run.

## Damage and limb-loss adaptation

This follow-up adds evidence about locomotion after structural damage. The best direct amputation result is still from a very different machine and must not be treated as proof that Strokah can do the same.

### What the primary studies establish

- Yu et al. (PNAS, 2026) trained separate expert controllers for an intact modular quadruped and several amputated morphologies, then distilled their sensorimotor sequences into one amputation-agnostic controller. The policy was not told module connectivity and had to infer damage from the sensory effects of its actions. On the physical modular robot it continued moving with a hindlimb removed, both hindlimbs removed, and all but one module removed; tests included cut locations not used in training. This is the strongest direct precedent for recovery through limb loss. Its modules were designed to remain functional when separated, and its objective was locomotion, not tracking a supplied FLUX performance or preserving character expression. [Paper](https://pmc.ncbi.nlm.nih.gov/articles/PMC12974426/).
- Matsuura and Sato (Artificial Life and Robotics, 2026) trained one simulation policy with the failed-joint identity as an input, covering a single sudden free-swinging actuator failure at any of 12 joints and random failure times. It maintained locomotion in their simulation experiments. This supports explicit failure conditioning for actuator faults; it is not evidence for detached links or a physical robot demonstration. [Nagoya Institute of Technology record](https://pure.nitech.ac.jp/en/publications/learning-a-unified-fault-tolerant-gait-policy-for-quadruped-robot/).
- Li et al. (2026 preprint) pair an embodiment-randomized quadruped controller with an online adaptation module that estimates embodiment changes from a short interaction history. Their real Go2 results include a locked leg and a 5 kg payload. The tested changes were joint-range constraints and trunk mass, not amputation. [arXiv paper](https://arxiv.org/abs/2608.01506).
- Kumar et al. (RMA, 2021) likewise infer latent physical conditions from recent state/action history and adapt a base policy to terrain and payload changes on an A1 quadruped. The paper explicitly reports failures under sufficiently large perturbations; it does not establish limb-loss recovery. [Paper](https://arxiv.org/abs/2107.04034).

### Recommended Strokah architecture

1. **Keep the intact reference policy as the anchor.** First qualify the whole pinned FLUX walk with style intensity zero. Retain its phase, torso/root trajectory, foot-contact schedule, arm pose and timing as the intact reference.
2. **Make damage a real, versioned morphology state.** At minimum record which links remain, which joints are active, locked or free-swinging, current actuator limits, contact availability, and the candidate’s measured mass/centre of mass. Structural severance must update geometry, collision, mass and inertia before control resumes. Do not represent a missing limb as a visual-only flag while its old colliders or mass remain in simulation.
3. **Train for both known and unknown damage.** Use a morphology-conditioned tracker with an explicit damage descriptor as the primary control path. Train expert tracking controllers for representative remaining morphologies, then distill or share their useful sensorimotor behavior in a single policy. Add a short-history adaptation encoder as a separate path for unannounced or uncertain damage. Compare explicit-state, history-only and combined conditioning; the modular-robot paper shows that history-only inference can work in its own platform, while the joint-failure study supports explicit fault input for a different fault class.
4. **Preserve reachable reference features and report losses honestly.** Track the supplied phase, root path, torso and surviving-joint motion. Mask only targets that are physically impossible because the corresponding body part is absent or disabled; report those missing channels separately. Learn bounded residual corrections for load transfer, new contacts and balance. Do not call an amputated performance an exact full-body match.
5. **Keep intimidation as a separate control.** After the damaged reference tracker is stable, add one style intensity parameter and test changes in torso/head lag, surviving-arm response, swing timing and pauses. Style reward stays subordinate to survival, valid contacts, no forbidden collision and authored joint/actuator limits. Score match, intimidation, eeriness and physical plausibility independently.

Suggested policy inputs are reference phase and future reference samples; root/body state; joint positions and velocities; foot/contact state; previous actions; a per-link presence mask; a per-joint actuator-state and limit vector; and mass/CoM or an inferred embodiment latent. Actions should target only actuated degrees of freedom and include a bounded residual around the reference target. A locked joint should be modeled as constrained motion; a free-swinging failure should have zero actuator authority; a removed link should contribute neither collision geometry nor mass. These are different transition models and should be trained and measured separately.

### Evaluation gates

Evaluate intact walking and damage recovery as separate acceptance tracks. For each damage class and random failure phase, log: fall/invalid-state rate; survival and completion; forward distance and speed; time to regain stable support; contact schedule and stance slip; joint-limit and actuator-limit excess; collision/penetration; torque and action-rate peaks; and reference error on the surviving body channels. Include unseen cut points and at least one damage event during each reference phase. Compare against the intact tracker and a no-adaptation control. State sample counts, seeds and confidence intervals.

For the first damaged run, require zero invalid physics states, zero forbidden self-collisions, zero limit/torque violations, no external root resets, and continued forward progress after a randomized mid-walk damage event. Set numeric survival, distance and recovery-time targets before training from the project’s intact baseline; do not copy the other robots’ rates or speeds. Only after those hard gates pass should a blinded perceptual comparison test whether the damaged motion reads as intimidating while remaining recognizably related to the FLUX reference.

This evidence strengthens the architecture recommendation but leaves the project gates unchanged: Strokah still needs a clear collision/terrain path, measured intact walking, damage-specific runtime demonstrations, and a calibrated Liquid comparison before any motion-match claim.
