"""Fresh PhysX convex cooking for every rigid-body mesh in one pinned USD.

This diagnostic stage never plays the physics timeline and cannot accept a run.
Requires an existing target-preflight result and a fresh exact-operation usage
gate. Output is deterministic gzip; no per-mesh evidence is reused.
"""

import argparse
import gzip
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import threading
import traceback
from pathlib import Path

_write_lock = threading.RLock()


def available_memory(include_gpu=True):
    if platform.system() == "Windows":
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in
                ("total_physical", "available_physical", "total_pagefile",
                 "available_commit", "total_virtual", "available_virtual", "extended_virtual")]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise OSError("Windows memory query failed")
        result = {"available_physical_bytes": status.available_physical,
                  "available_commit_bytes": status.available_commit}
    else:
        fields = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            fields[key] = int(value.split()[0])*1024
        result = {"available_physical_bytes": fields["MemAvailable"]}
    if include_gpu:
        result["available_gpu_bytes"] = available_gpu_memory()
    return result


def available_gpu_memory():
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi and platform.system() == "Windows":
        nvidia_smi = str(Path(os.environ.get("SystemRoot", r"C:\Windows"))/"System32"/"nvidia-smi.exe")
    if not nvidia_smi:
        raise OSError("NVIDIA memory query is unavailable")
    free_mib = subprocess.check_output(
        [nvidia_smi, "--id=0", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
        text=True, timeout=15).strip()
    return int(free_mib)*2**20


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, data):
    with _write_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix+".tmp")
        temporary.write_text(json.dumps(data, indent=2, allow_nan=False)+"\n")
        temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("manifest", "preflight", "resource-preflight", "usd", "snapshot",
                 "usage-gate", "out", "receipt", "kit-log", "cache"):
        parser.add_argument("--"+name, required=True, type=Path)
    parser.add_argument("--usd-sha256", required=True)
    parser.add_argument("--minimum-free-memory-gib", type=float, default=1.)
    parser.add_argument("--mesh-start", type=int, default=0)
    parser.add_argument("--mesh-count", type=int)
    parser.add_argument("--experience", type=Path)
    args = parser.parse_args()
    if not 1 <= args.minimum_free_memory_gib <= 64:
        raise ValueError("Live memory guard must reserve at least 1 GiB")
    root = args.manifest.resolve(strict=True).parent
    manifest = json.loads(args.manifest.read_text())
    if Path(manifest["manifest"]).resolve() != args.manifest.resolve():
        raise ValueError("Target manifest location mismatch")
    for path in (args.out, args.receipt, args.kit_log):
        if not path.resolve().is_relative_to(root) or path.exists():
            raise ValueError("Require fresh outputs inside the target run")
    if platform.system() not in ("Windows", "Linux") or platform.node().casefold() != "l-gpc":
        raise ValueError("Cooking requires the supported L-GPC target")
    preflight = json.loads(args.preflight.read_text())
    if preflight.get("ready") is not True or Path(preflight["manifest"]).resolve() != args.manifest.resolve():
        raise ValueError("Missing successful target preflight for this run")
    baseline_hash = sha(args.usd)
    resources = json.loads(args.resource_preflight.read_text())
    experience_hash = sha(args.experience) if args.experience else None
    if (not resources["ready"] or not 0 <= time.time()-resources["observed_epoch"] < 120
            or resources["candidate_sha256"] != baseline_hash
            or resources["cook_script_sha256"] != sha(Path(__file__))
            or resources["startup_preflight_sha256"] != sha(args.preflight)
            or resources.get("experience_sha256") != experience_hash
            or resources["conflicting_runtime_processes"] != []
            or min(resources["memory"].values()) < args.minimum_free_memory_gib*2**30):
        raise ValueError("Fresh target resource preflight failed")
    snapshot = json.loads(args.snapshot.read_text())
    if baseline_hash != args.usd_sha256 or snapshot["usd_sha256"] != baseline_hash:
        raise ValueError("USD or snapshot hash mismatch")
    all_paths = [m["path"] for m in snapshot["meshes"]
                 if m["collision"] and m["path"].startswith("/World/Robot/")]
    if (not 0 <= args.mesh_start < len(all_paths)
            or args.mesh_count is not None and args.mesh_count <= 0):
        raise ValueError("Invalid cook batch range")
    end = len(all_paths) if args.mesh_count is None else min(
        len(all_paths), args.mesh_start+args.mesh_count)
    selected_paths = all_paths[args.mesh_start:end]
    expected = set(selected_paths)
    partial = len(expected) != len(all_paths)
    gate = json.loads(args.usage_gate.read_text())
    age = time.time()-gate["observed_epoch"]
    if (gate["candidate_sha256"] != baseline_hash or gate["operation"] != "cook-articulation"
            or not 0 <= age < 120 or not gate["remaining_percent"] > gate["minimum_remaining_percent"]
            or gate.get("conflicting_runtime_processes") != []
            or gate.get("selected_mesh_paths") != selected_paths):
        raise ValueError("Usage, operation, age or process-ownership gate failed")
    staged = root/"inputs"/("cook-"+baseline_hash+".usda")
    staged.parent.mkdir(parents=True, exist_ok=True)
    if staged.exists():
        if sha(staged) != baseline_hash:
            raise ValueError("Existing staged input hash mismatch")
    else:
        shutil.copyfile(args.usd, staged)
        staged.chmod(0o444)
    receipt = {
        "schema_version": 1, "kind": "physx_convex_cooking",
        "candidate_sha256": baseline_hash, "snapshot_sha256": sha(args.snapshot),
        "script_sha256": sha(Path(__file__)), "usage_gate_sha256": sha(args.usage_gate),
        "preflight_sha256": sha(args.preflight), "status": "starting",
        "resource_preflight_sha256": sha(args.resource_preflight),
        "physics_timeline_played": False, "physics_accepted": False,
        "viewport_updates_enabled": False,
        "partial_batch": partial, "selected_mesh_paths": selected_paths,
        "total_candidate_meshes": len(all_paths),
        "experience_sha256": experience_hash,
    }
    save(args.receipt, receipt)
    app = None
    guard_stop = threading.Event()
    gpu_sample = None

    def read_gpu_sample():
        return {"available_gpu_bytes": available_gpu_memory(),
                "observed_epoch": time.time(), "observed_monotonic": time.monotonic()}

    def gpu_sampler():
        nonlocal gpu_sample
        while not guard_stop.wait(5):
            try:
                gpu_sample = read_gpu_sample()
            except Exception as error:
                with _write_lock:
                    receipt["last_gpu_query_error"] = {
                        "error": repr(error), "observed_epoch": time.time()}

    def check_memory():
        gpu = gpu_sample
        if gpu is None or time.monotonic()-gpu["observed_monotonic"] > 30:
            raise RuntimeError("GPU memory observation is missing or more than 30 seconds old")
        sample = {**available_memory(include_gpu=False),
                  "available_gpu_bytes": gpu["available_gpu_bytes"],
                  "gpu_observed_epoch": gpu["observed_epoch"],
                  "observed_epoch": time.time()}
        receipt["latest_memory_sample"] = sample
        if min(value for key, value in sample.items() if key.endswith("_bytes")) < args.minimum_free_memory_gib*2**30:
            raise MemoryError("Live physical, commit or GPU memory fell below the configured guard")

    def memory_guard():
        while not guard_stop.wait(5):
            try:
                check_memory()
                save(args.receipt, receipt)
            except Exception as error:
                # Terminate only this cooker process. Do not stop other target jobs.
                with _write_lock:
                    receipt.update(status="failed", error=repr(error),
                                   aborted_by_live_memory_guard=True)
                    try:
                        save(args.receipt, receipt)
                    finally:
                        os._exit(75)

    try:
        receipt["memory_guard"] = {"minimum_free_gib": args.minimum_free_memory_gib,
                                   "sampling_interval_seconds": 5, "process_id": os.getpid(),
                                   "gpu_query_timeout_seconds": 15,
                                   "maximum_gpu_observation_age_seconds": 30}
        gpu_sample = read_gpu_sample()
        check_memory()
        save(args.receipt, receipt)
        threading.Thread(target=gpu_sampler, name="cook-gpu-memory-sampler", daemon=True).start()
        threading.Thread(target=memory_guard, name="cook-memory-guard", daemon=True).start()
        from isaacsim import SimulationApp
        app = SimulationApp({"headless": True, "disable_viewport_updates": True,
                             "width": 320, "height": 240,
                             "extra_args": ["--portable", "--portable-root="+str(args.cache),
                                            "--/log/file="+str(args.kit_log)]},
                            experience=str(args.experience) if args.experience else "")
        receipt["native_startup_completed"] = True
        save(args.receipt, receipt)
        import numpy as np
        from scipy.spatial import ConvexHull
        import omni.usd
        from pxr import PhysicsSchemaTools, UsdGeom, UsdPhysics, UsdUtils
        from omni.physx import get_physx_cooking_interface
        from omni.physx.bindings._physx import PhysxCollisionRepresentationResult

        omni.usd.get_context().open_stage(str(staged))
        for _ in range(10):
            app.update()
        stage = omni.usd.get_context().get_stage()
        stage_id = UsdUtils.StageCache.Get().GetId(stage).ToLongInt()
        cache = UsdGeom.XformCache()
        parts = []
        for prim in stage.Traverse():
            if str(prim.GetPath()) not in expected:
                continue
            body = prim.GetParent()
            while body and not body.HasAPI(UsdPhysics.RigidBodyAPI):
                body = body.GetParent()
            if not body:
                raise ValueError("Collider has no rigid-body owner")
            relative = cache.GetLocalToWorldTransform(prim)*cache.GetLocalToWorldTransform(body).GetInverse()
            if np.max(abs(np.asarray(relative)-np.eye(4))) > 1e-8:
                raise ValueError("Nonidentity mesh-to-body transform needs explicit cooker-frame calibration")
            limit = prim.GetAttribute("physxConvexDecompositionCollision:hullVertexLimit").Get()
            if not limit or not 8 <= limit <= 64:
                raise ValueError("Hull vertex setting must be explicitly in [8, 64]")
            hulls, statuses, callback_errors = [], [], []
            source_points = np.asarray(UsdGeom.Mesh(prim).GetPointsAttr().Get(), float)
            source_lo, source_hi = source_points.min(0), source_points.max(0)

            def received(status, convexes):
                statuses.append(str(status))
                if status != PhysxCollisionRepresentationResult.RESULT_VALID:
                    return
                try:
                    for index, hull in enumerate(convexes):
                        vertices = [list(map(float, vertex)) for vertex in hull.vertices]
                        raw_planes = [list(map(float, polygon.plane)) for polygon in hull.polygons]
                        points, raw = np.asarray(vertices), np.asarray(raw_planes)
                        if len(points) < 4 or not np.isfinite(points).all():
                            raise ValueError("Nonfinite or insufficient cooked vertices")
                        # Decomposition may expand beyond the source vertex AABB.
                        # Record it for the shape-budget audit instead of confusing
                        # approximation error with an unverified frame mismatch.
                        bbox_extension = float(max(0, np.max(source_lo-points.min(0)),
                                                   np.max(points.max(0)-source_hi)))
                        convex = ConvexHull(points)
                        if convex.volume <= 0:
                            raise ValueError("Cooked hull has no three-dimensional volume")
                        # The API documents polygon planes without a coordinate/sign
                        # convention. Vertex geometry independently defines this hull.
                        hulls.append({
                            "index": index, "vertices": vertices,
                            "planes": convex.equations.tolist(),
                            "plane_method": "Convex hull of PhysX-returned vertices",
                            "api_polygon_planes": raw_planes,
                            "api_plane_plus_d_max_residual": float(
                                np.max(points @ raw[:, :3].T+raw[:, 3])) if len(raw) else None,
                            "api_plane_minus_d_max_residual": float(
                                np.max(points @ raw[:, :3].T-raw[:, 3])) if len(raw) else None,
                            "convex_volume_m3": float(convex.volume),
                            "source_bbox_extension_m": bbox_extension,
                        })
                except Exception as error:
                    callback_errors.append(repr(error))

            deadline, retries = time.monotonic()+30, 0
            while True:
                get_physx_cooking_interface().request_convex_collision_representation(
                    stage_id=stage_id, collision_prim_id=PhysicsSchemaTools.sdfPathToInt(prim.GetPath()),
                    run_asynchronously=False, on_result=received)
                if (not hulls and not callback_errors
                        and statuses == [str(PhysxCollisionRepresentationResult.RESULT_ERROR_NOT_READY)]
                        and time.monotonic() < deadline):
                    retries += 1
                    statuses.clear()
                    app.update()
                    time.sleep(.05)
                    continue
                break
            if not hulls or callback_errors:
                raise RuntimeError(f"Invalid cook for {prim.GetPath()}: {statuses}; {callback_errors}")
            path = str(prim.GetPath())
            part = {"path": path, "body": str(body.GetPath()), "hulls": hulls,
                    "snapshot_collision_fingerprint": next(
                        m["collision_state_sha256"] for m in snapshot["meshes"] if m["path"] == path)}
            parts.append(part)
            receipt.update(status="cooking", completed_meshes=len(parts),
                           expected_meshes=len(expected), current_collider=path,
                           latest_collider_readiness_retries=retries)
            save(args.receipt, receipt)
            print("COOKED", len(parts), len(expected), path, len(hulls), flush=True)
        if {part["path"] for part in parts} != expected:
            raise ValueError("Not every expected collider was cooked")
        if sha(args.usd) != baseline_hash or sha(staged) != baseline_hash:
            raise ValueError("Input changed during cooking")
        data = {"schema_version": 1, "candidate_sha256": baseline_hash,
                "snapshot_sha256": sha(args.snapshot), "vertex_frame": "rigid-body-local",
                "identity_mesh_to_body_transform_verified": True, "meshes": parts,
                "partial_batch": partial, "selected_mesh_paths": selected_paths,
                "total_candidate_meshes": len(all_paths)}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_bytes(gzip.compress(json.dumps(data, sort_keys=True, allow_nan=False).encode(), mtime=0))
        receipt.update(status="completed", output_sha256=sha(args.out), mesh_count=len(parts),
                       hull_count=sum(len(part["hulls"]) for part in parts))
        save(args.receipt, receipt)
    except BaseException as error:
        receipt.update(status="failed", error=repr(error), traceback=traceback.format_exc())
        save(args.receipt, receipt)
        raise
    finally:
        guard_stop.set()
        if app:
            app.close(exit_code=0 if receipt["status"] == "completed" else 1)


if __name__ == "__main__":
    main()
