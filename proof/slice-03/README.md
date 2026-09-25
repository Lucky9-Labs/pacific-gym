# FLUX 3 gait reference: local preflight

![Source contact sheet](source-contact-sheet.png)

The two source GLBs came from the exact S3 versions pinned in
`plugins/pacific-gym/fixtures/strokah-source.json`. The static visual has no
skin or animation. The separate rig reference has 62 joints and no animation.
Blender 5.2.1 LTS rendered five views of each without changing either source.
`Terrain_Walk_Test` and `Icosphere` were hidden for rig **renders only** so
the body fills the frame. The render receipts contain each image's hash.

The [exact request](request.json) uses the visual three-quarter render as the
opening frame of a five-second FLUX 3 Video `i2v` draft at `hd`, 1:1, silent.
The prompt asks for an alternating, weighty forward walk and visible planted
feet. Other rendered views support local identity review; the current BFL API
uses supplied `keyframes` as frames in the generated video, so feeding the
other camera angles would force a view change rather than serve as independent
identity references. The endpoint's `latest` version is not a fixed model
revision, and the actual model revision cannot be established without a run.

Run `sh scripts/accept-flux3-gait.sh` for the local hash and request preflight.
With an authorized BFL API key in `BFL_API_KEY`, run
`sh scripts/accept-flux3-gait.sh --live` to submit once, poll or resume that
job, and save the returned MP4 under `.pacific-gym/flux3/`. The submission ID
is stored so rerunning the command does not create another billable job.
On macOS, immediately after copying the key, run
`sh scripts/accept-flux3-gait.sh --live-from-clipboard` to use it without
placing the key in a shell command or environment variable.

The local preflight passed on 2026-09-25. After the key was copied to the
clipboard, BFL accepted one draft submission. The client then rejected BFL's
returned polling host before it saved the job ID, so that result cannot be
retrieved. The client now saves the submission before validating that the
polling URL is HTTPS. The clipboard key cleared before a replacement run.
No video was downloaded; identity preservation and foot contacts in generated
motion remain untested. The sheet above is source render proof only.

API contract: [BFL Video docs](https://docs.bfl.ai/flux_3/flux3_video),
[FLUX 3 endpoint](https://docs.bfl.ai/api-reference/utility/generate-a-video-with-flux-3).
