"""Capture rendered native states without advancing the physics timeline.

Construct only after a rendering-enabled SimulationApp and stage exist. This
module is prepared infrastructure; a saved capture manifest is required evidence.

API references:
https://docs.isaacsim.omniverse.nvidia.com/latest/py/source/extensions/isaacsim.core.rendering_manager/docs/index.html
https://docs.omniverse.nvidia.com/kit/docs/omni_physics/108.0/extensions/runtime/source/omni.physx/docs/api/python.html
"""

import hashlib
import json
import struct
import zlib
from pathlib import Path

import numpy as np


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _png(rgb):
    """Losslessly encode the native uint8 RGB buffer without image dependencies."""
    height, width, _ = rgb.shape

    def chunk(kind, payload):
        return (struct.pack(">I", len(payload))+kind+payload
                +struct.pack(">I", zlib.crc32(kind+payload) & 0xffffffff))

    scanlines = b"".join(b"\0"+row.tobytes() for row in rgb)
    return (b"\x89PNG\r\n\x1a\n"
            +chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            +chunk(b"IDAT", zlib.compress(scanlines, 3))+chunk(b"IEND", b""))


def _rotation(q):
    x, y, z, w = q
    return np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                     [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                     [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])


class NativeCapture:
    def __init__(self, stage, output, candidate_sha256, *, eye, target,
                 width=640, height=640):
        import omni.replicator.core as rep
        import omni.timeline
        import carb.settings
        from isaacsim.core.rendering_manager import RenderingManager
        import isaacsim.core.experimental.utils.stage as stage_utils
        from omni.physx import get_physx_interface
        from pxr import Gf, Usd, UsdGeom, UsdLux

        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=False)
        self.stage, self.render = stage, RenderingManager.render
        self.orchestrator, self.settings = rep.orchestrator, carb.settings.get_settings()
        self.orchestrator.set_capture_on_play(False)
        self.stage_utils = stage_utils
        self.physics = get_physx_interface()
        self.timeline = omni.timeline.get_timeline_interface()
        self.UsdGeom = UsdGeom
        self.width, self.height = width, height
        self.frames = []
        if self.timeline.is_playing():
            raise ValueError("Native capture requires the manually stepped, paused timeline")
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            camera = UsdGeom.Camera.Define(stage, "/PacificGymProof/Camera")
            camera.CreateFocalLengthAttr(43.)
            camera.CreateHorizontalApertureAttr(36.)
            camera.CreateVerticalApertureAttr(36.*height/width)
            camera.CreateClippingRangeAttr(Gf.Vec2f(.01, 100.))
            view = Gf.Matrix4d().SetLookAt(Gf.Vec3d(*eye), Gf.Vec3d(*target), Gf.Vec3d(0, 0, 1))
            UsdGeom.Xformable(camera).AddTransformOp().Set(view.GetInverse())
            dome = UsdLux.DomeLight.Define(stage, "/PacificGymProof/Dome")
            dome.CreateIntensityAttr(500.)
            sun = UsdLux.DistantLight.Define(stage, "/PacificGymProof/Sun")
            sun.CreateIntensityAttr(1000.)
            sun.CreateAngleAttr(8.)
            UsdGeom.Xformable(sun).AddRotateXYZOp().Set(Gf.Vec3f(-30., -35., 0.))
        self.product = rep.create.render_product(str(camera.GetPath()), (width, height))
        self.rgb = rep.AnnotatorRegistry.get_annotator("rgb")
        self.rgb.attach([self.product])
        self.metadata = {
            "candidate_sha256": candidate_sha256, "camera_eye_m": list(eye),
            "camera_target_m": list(target), "resolution": [width, height],
            "script_sha256": _sha(Path(__file__).read_bytes()),
            "render_api": "omni.replicator.core.orchestrator.step",
            "render_delta_time_s": 0., "render_wait_for_completion": True,
            "timeline_played": False,
            "render_clock_source": "Native manual-step time in Fabric ExternalSimulationTime",
            "runtime_root_resets": 0, "physics_accepted": False, "walking_accepted": False,
        }
        # Warm the rendering graph before physics attachment.
        for _ in range(8):
            self.render()
        self._capture_frame()
        if self.timeline.is_playing():
            raise ValueError("Renderer unexpectedly started the timeline")
        warm_pixels = np.asarray(self.rgb.get_data())
        if warm_pixels.shape not in ((height, width, 3), (height, width, 4)):
            raise ValueError(f"Renderer failed before physics attachment: RGB shape {warm_pixels.shape}")

    def _capture_frame(self):
        # The explicit zero-time step schedules the annotator, which a plain
        # rendering update does not do on a stopped timeline. Keep simulation
        # updates disabled for every internal app update.
        previous = self.settings.get("/app/player/playSimulations")
        self.settings.set("/app/player/playSimulations", False)
        try:
            self.orchestrator.step(delta_time=0., pause_timeline=True, wait_for_render=True)
        finally:
            self.settings.set("/app/player/playSimulations", previous)

    def capture(self, step, time_s, link_paths, link_poses, joint_positions, read_state):
        """read_state returns link poses, q, root velocity and q velocity."""
        if self.timeline.is_playing():
            raise ValueError("Timeline must remain paused")
        poses, joints = np.array(link_poses, copy=True), np.array(joint_positions, copy=True)
        before = tuple(np.array(value, copy=True) for value in read_state())
        if (poses.shape != (len(link_paths), 7) or not np.isfinite(poses).all()
                or not np.isfinite(joints).all()):
            raise ValueError("Capture requires complete finite native state")
        self.physics.update_transformations(False, True, False)
        cache = self.UsdGeom.XformCache()
        maximum_position_error = 0.
        for path, pose in zip(link_paths, poses):
            matrix = np.asarray(cache.GetLocalToWorldTransform(self.stage.GetPrimAtPath(path))).T
            error = float(np.linalg.norm(matrix[:3, 3]-pose[:3]))
            maximum_position_error = max(maximum_position_error, error)
            if error > 2e-5 or not np.allclose(matrix[:3, :3], _rotation(pose[3:]), atol=2e-5, rtol=0):
                raise ValueError("Rendered USD transform differs from native physics: "+path)
        # RenderingManager seeds this renderer clock when SimulationManager is
        # absent. Keep it aligned with our explicitly stepped native simulation.
        fabric_stage = self.stage_utils.get_current_stage(backend="fabric")
        clock = fabric_stage.GetPrimAtPath("/ExternalSimulationTime").GetAttribute("omni:time")
        if not clock:
            raise ValueError("The native renderer clock is unavailable")
        clock.Set(float(time_s))
        self._capture_frame()
        after = read_state()
        if (self.timeline.is_playing() or len(before) != 4 or len(after) != 4
                or not np.array_equal(poses, before[0])
                or not np.array_equal(joints, before[1])
                or any(not np.array_equal(a, b) for a, b in zip(before, after))):
            raise ValueError("Rendering changed native physics state")
        pixels = np.asarray(self.rgb.get_data())
        if pixels.shape not in ((self.height, self.width, 3), (self.height, self.width, 4)):
            raise ValueError(f"Native RGB annotator returned incomplete shape {pixels.shape}")
        if pixels.dtype != np.uint8:
            raise ValueError("Unexpected native RGB pixel type")
        data = _png(np.ascontiguousarray(pixels[:, :, :3]))
        path = self.output/f"frame-{len(self.frames):06d}.png"
        path.write_bytes(data)
        self.frames.append({
            "path": path.name, "sha256": _sha(data), "physics_step": int(step),
            "simulation_time_s": float(time_s),
            "native_link_poses_sha256": _sha(np.asarray(poses, dtype="<f4").tobytes()),
            "native_joint_positions_sha256": _sha(np.asarray(joints, dtype="<f4").tobytes()),
            "maximum_usd_native_position_error_m": maximum_position_error,
            "native_state_unchanged_by_render": True,
        })

    def finish(self):
        if not self.frames:
            raise ValueError("No native frames were captured")
        path = self.output/"capture.json"
        path.write_text(json.dumps({**self.metadata, "frames": self.frames,
                                   "native_simulation_render_capture": True},
                                  indent=2, allow_nan=False)+"\n")
        self.rgb.detach([self.product])
        return path
