"""Free-root gravity-settle diagnostic for a clearance-qualified articulation.

This diagnostic manually steps native GPU physics and cannot accept walking.
Requires an existing target-preflight result and a fresh exact-operation usage
gate. Output is deterministic gzip; no per-mesh evidence is reused.
"""

import argparse
import gzip
import hashlib
import json
import math
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


def contact_hull_xy(points):
    """Convex hull of measured loaded contact points, without a SciPy dependency."""
    points = sorted({(float(p[0]), float(p[1])) for p in points})
    if len(points) <= 2:
        return [list(p) for p in points]
    def cross(a, b, c):
        return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    lower, upper = [], []
    for p in points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(points):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return [list(p) for p in lower[:-1]+upper[:-1]]


def contact_margin(polygon, point):
    if len(polygon) < 3:
        return None
    margins = []
    for a, b in zip(polygon, polygon[1:]+polygon[:1]):
        dx, dy = b[0]-a[0], b[1]-a[1]
        margins.append((-dy*(point[0]-a[0])+dx*(point[1]-a[1]))/math.hypot(dx, dy))
    return min(margins)


def save(path, data):
    with _write_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix+".tmp")
        temporary.write_text(json.dumps(data, indent=2, allow_nan=False)+"\n")
        temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('manifest','preflight','resource-preflight','usd','snapshot','usage-gate',
                 'isolated-sweep','combined-sweep','transition-poses','planned-poses','cook-receipt',
                 'experience','receipt','out','kit-log','cache'):
        parser.add_argument('--'+name, required=True, type=Path)
    parser.add_argument('--usd-sha256', required=True)
    parser.add_argument('--seconds', type=float, default=3.)
    parser.add_argument('--keep-awake', action='store_true',
                        help='Wake the articulation before each observed step for contact calibration')
    parser.add_argument('--motion-schedule', type=Path,
                        help='Timed prefix of the exact collision-screened pose sequence')
    parser.add_argument('--repeated-cycle-probe', action='store_true',
                        help='Allow up to 240 seconds for a captured, screened alternating-cycle probe')
    parser.add_argument('--cooked', type=Path,
                        help='Exact native cooked hulls, required for motion foot-height measurements')
    parser.add_argument('--settle-receipt', type=Path,
                        help='Successful awake calibration for this exact candidate before motion')
    parser.add_argument('--initial-pose', type=Path,
                        help='Hash-bound screened configuration authored before physics starts')
    parser.add_argument('--capture-dir', type=Path,
                        help='Fresh directory for native rendered physics frames')
    parser.add_argument('--capture-module', type=Path,
                        help='Frozen native_capture module, recorded by SHA-256')
    parser.add_argument('--capture-fps', type=int, default=12)
    parser.add_argument('--capture-cpu-threads', type=int, choices=(1, 2), default=2,
                        help='Bound concurrent renderer startup work while preserving memory guards')
    parser.add_argument('--drive-damping-scale', type=float, default=1.,
                        help='Bounded damping-only controller diagnostic; preserves stiffness and torque caps')
    args = parser.parse_args()
    if not math.isfinite(args.drive_damping_scale) or not .01 <= args.drive_damping_scale <= 1.:
        raise ValueError('Damping diagnostic scale must be between 0.01 and 1')
    if args.repeated_cycle_probe and not (args.motion_schedule and args.capture_dir):
        raise ValueError('Repeated-cycle probes require a motion schedule and native capture')
    duration_limit = 240 if args.repeated_cycle_probe else 30 if args.motion_schedule else 10
    if not 2 <= args.seconds <= duration_limit:
        raise ValueError('Diagnostic duration is outside the bounded operation limit')
    if platform.system() != 'Windows' or platform.node().casefold() != 'l-gpc':
        raise ValueError('Native Windows L-GPC is required')
    root = args.manifest.resolve(strict=True).parent
    if bool(args.capture_dir) != bool(args.capture_module):
        raise ValueError('Native capture requires both output directory and frozen module')
    if args.capture_dir:
        if (args.capture_dir.exists() or not args.capture_dir.resolve().is_relative_to(root)
                or not args.capture_module.resolve(strict=True).is_relative_to(root)
                or args.capture_fps not in (6, 10, 12, 15, 20, 24, 30)):
            raise ValueError('Native capture needs fresh run-local outputs and a supported frame rate')
    manifest = json.loads(args.manifest.read_text())
    if Path(manifest['manifest']).resolve() != args.manifest.resolve():
        raise ValueError('Target manifest mismatch')
    for path in (args.out,args.receipt,args.kit_log):
        if path.exists() or not path.resolve().is_relative_to(root):
            raise ValueError('Fresh outputs inside the run are required')
    trace_checkpoint_dir = args.out.with_suffix(args.out.suffix+'.partial')
    if trace_checkpoint_dir.exists():
        raise ValueError('Trace checkpoint directory must be fresh')
    candidate = sha(args.usd)
    snapshot = json.loads(args.snapshot.read_text())
    if candidate != args.usd_sha256 or snapshot['usd_sha256'] != candidate:
        raise ValueError('Candidate identity mismatch')
    resources = json.loads(args.resource_preflight.read_text())
    preflight = json.loads(args.preflight.read_text())
    if (not preflight['ready'] or not resources['ready']
            or Path(preflight['manifest']).resolve() != args.manifest.resolve()
            or not 0 <= time.time()-resources['observed_epoch'] < 120
            or resources['candidate_sha256'] != candidate
            or resources['cook_script_sha256'] != sha(Path(__file__))
            or resources['experience_sha256'] != sha(args.experience)
            or resources['startup_preflight_sha256'] != sha(args.preflight)
            or resources['conflicting_runtime_processes'] != []
            or min(resources['memory'].values()) < 2**30):
        raise ValueError('Fresh native resource gate failed')
    gate = json.loads(args.usage_gate.read_text())
    operation='probe-articulation-motion' if args.motion_schedule else 'settle-articulation'
    if (gate['candidate_sha256'] != candidate or gate['operation'] != operation
            or not 0 <= time.time()-gate['observed_epoch'] < 120
            or not gate['remaining_percent'] > gate['minimum_remaining_percent']
            or gate['conflicting_runtime_processes'] != []):
        raise ValueError('Fresh usage/operation gate failed')
    cook = json.loads(args.cook_receipt.read_text())
    if cook['status'] != 'completed' or cook['candidate_sha256'] != candidate:
        raise ValueError('Completed exact-candidate native cooking is required')
    for path, scope in ((args.isolated_sweep,'Discrete configured isolated poses'),
                        (args.combined_sweep,'Explicit combined poses')):
        sweep = json.loads(path.read_text())
        if (sweep['status'] != 'completed' or not sweep['all_sampled_cases_pass']
                or sweep['candidate_sha256'] != candidate or sweep['scope'] != scope
                or sweep['evidence_sha256']['snapshot'] != sha(args.snapshot)
                or sweep['evidence_sha256']['cooked'] != cook['output_sha256']
                or sweep['evidence_sha256']['cook_receipt'] != sha(args.cook_receipt)
                or sweep['case_count_planned'] != len(sweep['cases'])
                or not sweep['cases'] or not all(c['clearance_pass'] for c in sweep['cases'])):
            raise ValueError('Full isolated and combined clearance must pass before gravity')
    plan = json.loads(args.planned_poses.read_text())
    if (plan['candidate_sha256'] != candidate or not plan['poses']
            or not all(p['optimizer_success'] and p['bounded_kinematic_pose_found']
                       and p['hard_constraints_satisfied'] for p in plan['poses'])):
        raise ValueError('Converged, feasible planned poses are required')
    transitions=json.loads(args.transition_poses.read_text())
    combined=json.loads(args.combined_sweep.read_text())
    if (transitions['candidate_sha256'] != candidate
            or transitions['planner_sha256'] != sha(args.planned_poses)
            or combined['evidence_sha256']['poses'] != sha(args.transition_poses)):
        raise ValueError('Combined clearance does not bind the converged plan')
    initial=None
    initial_index=0
    if args.initial_pose:
        initial=json.loads(args.initial_pose.read_text())
        initial_index=initial['pose_index']
        if (initial['candidate_sha256']!=candidate
                or initial['planned_poses_sha256']!=sha(args.planned_poses)
                or initial['transition_poses_sha256']!=sha(args.transition_poses)
                or initial['snapshot_sha256']!=sha(args.snapshot)
                or initial['cooked_sha256']!=cook['output_sha256']
                or type(initial_index) is not int
                or not 0<=initial_index<len(transitions['poses'])
                or initial['phase']!='before_simulation_attach_and_first_step'
                or set(initial['q_deg'])!=set(plan['reference_q_deg'])
                or any(abs(initial['q_deg'][n]-plan['reference_q_deg'][n])>1e-10
                       or abs(initial['q_deg'][n]-transitions['poses'][initial_index]['q_deg'][n])>1e-10
                       for n in initial['q_deg'])
                or initial['linear_velocity_m_s']!=[0,0,0]
                or initial['angular_velocity_deg_s']!=[0,0,0]
                or any(initial['joint_velocity_deg_s'][n]!=0 for n in initial['q_deg'])):
            raise ValueError('Initial configuration does not bind the screened reference')
    schedule=None
    if args.motion_schedule:
        if not args.keep_awake or not args.cooked or sha(args.cooked)!=cook['output_sha256']:
            raise ValueError('Motion requires awake contact reporting and exact cooked hulls')
        if not args.settle_receipt:
            raise ValueError('Motion requires a successful awake settle calibration')
        calibration=json.loads(args.settle_receipt.read_text())
        if (calibration['candidate_sha256']!=candidate or not calibration['settle_pass']
                or not calibration.get('quiet_settle_pass', False)
                or not calibration['final_support_window_all_awake']
                or calibration['fall_detected'] or calibration['invalid_physics_state']
                or calibration['status']!='completed'
                or calibration.get('drive_damping_scale', 1.)!=args.drive_damping_scale
                or calibration['evidence_sha256']['cook_receipt']!=sha(args.cook_receipt)
                or calibration['evidence_sha256'].get('initial_pose')!=
                   (sha(args.initial_pose) if args.initial_pose else None)):
            raise ValueError('Awake calibration does not qualify this motion candidate')
        schedule=json.loads(args.motion_schedule.read_text())
        knots=schedule['knots']
        if (schedule['candidate_sha256']!=candidate
                or schedule['transition_poses_sha256']!=sha(args.transition_poses)
                or schedule['duration_s']!=args.seconds or not knots
                or knots[0]!={'time_s':0.,'pose_index':initial_index}
                or knots[-1]['time_s']!=args.seconds):
            raise ValueError('Motion schedule evidence or duration mismatch')
        for i,knot in enumerate(knots):
            if (not math.isfinite(knot['time_s'])
                    or type(knot['pose_index']) is not int
                    or not 0<=knot['pose_index']<len(transitions['poses'])
                    or i and (knot['time_s']<=knots[i-1]['time_s']
                              or knot['pose_index']-knots[i-1]['pose_index'] not in (0,1))):
                raise ValueError('Schedule must traverse an ordered, adjacent screened prefix')
        time_warps = schedule.get('time_warp_segments', [])
        previous_end = -1.
        knot_times = {k['time_s'] for k in knots}
        for segment in time_warps:
            start, end = segment['start_s'], segment['end_s']
            if (segment.get('curve') != 'quintic_rest_to_rest'
                    or not math.isfinite(start) or not math.isfinite(end)
                    or not 0 <= start < end <= args.seconds
                    or start < previous_end
                    or start not in knot_times or end not in knot_times):
                raise ValueError('Invalid bounded phase timing segment')
            previous_end = end
        if args.repeated_cycle_probe:
            selected = transitions['poses'][initial_index:knots[-1]['pose_index']+1]
            completed = {p['step']: p for p in selected
                         if p.get('phase') == 'touchdown' and p.get('fraction') == 1.}
            if (len(completed) < 6 or sorted(completed) != list(range(len(completed)))
                    or any(completed[i]['stance'] == completed[i+1]['stance']
                           for i in range(len(completed)-1))
                    or plan.get('expected_root_forward_displacement_m', 0) < .1):
                raise ValueError('Repeated-cycle schedule needs at least six alternating complete steps')
    staged = root/'inputs'/('settle-'+candidate+'.usda')
    staged.parent.mkdir(parents=True,exist_ok=True)
    if staged.exists():
        if sha(staged) != candidate: raise ValueError('Staged input changed')
    else:
        shutil.copyfile(args.usd,staged);staged.chmod(0o444)
    receipt = {'schema_version':1,'kind':'native_gpu_gravity_settle_diagnostic',
               'candidate_sha256':candidate,'status':'starting',
               'physics_accepted':False,'walking_accepted':False,'simulation_started':False,
               'timeline_played':False,'runtime_root_resets':0,'external_support_applied':False,
               'drive_damping_scale':args.drive_damping_scale,
               'repeated_cycle_probe':args.repeated_cycle_probe,
               'evidence_sha256':{k:sha(p) for k,p in vars(args).items()
                    if isinstance(p,Path) and k not in ('out','receipt','kit_log','cache','capture_dir')},
               'script_sha256':sha(Path(__file__)),
               'memory_guard':{'minimum_free_gib':1,'host_interval_s':5,
                               'gpu_timeout_s':15,'maximum_gpu_age_s':30,'process_id':os.getpid()}}
    if schedule:
        receipt['kind']='native_gpu_bounded_motion_diagnostic'
        receipt['motion_schedule_is_gait_acceptance']=False
    save(args.receipt,receipt)
    stop = threading.Event();gpu_sample = None;app = None;simulation = None;attached = False
    capture = None
    def sample_gpu():
        return {'bytes':available_gpu_memory(),'monotonic':time.monotonic(),'epoch':time.time()}
    def check_memory():
        gpu = gpu_sample
        if gpu is None or time.monotonic()-gpu['monotonic'] > 30:
            raise RuntimeError('GPU memory observation is stale')
        sample = {**available_memory(False),'available_gpu_bytes':gpu['bytes']}
        with _write_lock:
            receipt['latest_memory'] = {**sample,'observed_epoch':time.time()}
            receipt.setdefault('memory_samples',[]).append({
                **receipt['latest_memory'],
                'completed_steps':receipt.get('completed_steps',0),
                'native_startup_completed':receipt.get('native_startup_completed',False)})
            breached = [name for name, value in sample.items() if value < 2**30]
            if breached:
                receipt['memory_guard_breach'] = {
                    'fields': breached, 'sample': receipt['latest_memory'].copy()}
                raise MemoryError('Memory reserve below 1 GiB: '+', '.join(breached))
    def gpu_sampler():
        nonlocal gpu_sample
        while not stop.wait(5):
            try: gpu_sample = sample_gpu()
            except Exception as error:
                with _write_lock: receipt['last_gpu_query_error'] = repr(error)
    def guard():
        while not stop.wait(5):
            try: check_memory();save(args.receipt,receipt)
            except Exception as error:
                with _write_lock:
                    receipt.update(status='failed',aborted_by_live_memory_guard=True,error=repr(error))
                    try: save(args.receipt,receipt)
                    finally: os._exit(75)
    try:
        gpu_sample=sample_gpu();check_memory()
        threading.Thread(target=gpu_sampler,daemon=True).start()
        threading.Thread(target=guard,daemon=True).start()
        from isaacsim import SimulationApp
        launch_config = {'headless':True,'disable_viewport_updates':not bool(args.capture_dir),
                         'multi_gpu':False,
                         'extra_args':['--portable','--portable-root='+str(args.cache),
                                       '--/log/file='+str(args.kit_log)]}
        if args.capture_dir:
            # Limit concurrent renderer initialization work without reducing the
            # live memory reserve. The scenario opens its own stage below.
            launch_config.update(limit_cpu_threads=args.capture_cpu_threads, create_new_stage=False)
            receipt['render_startup_limits'] = {
                'cpu_thread_limit': args.capture_cpu_threads, 'create_empty_stage': False,
                'memory_guard_minimum_gib': 1}
        app=SimulationApp(launch_config,experience=str(args.experience))
        if args.capture_dir:
            import carb.settings
            settings = carb.settings.get_settings()
            receipt['renderer_compilation_settings'] = {
                name: settings.get(name) for name in (
                    '/rtx/shaderDb/concurrency',
                    '/rtx-transient/psoDb/serializeAllCompiles',
                    '/rtx-transient/psoDb/maxPipelineCompileWorkerFactor')}
        receipt['native_startup_completed']=True;save(args.receipt,receipt)
        import numpy as np
        import omni.usd
        import omni.physics.tensors as tensors
        from omni.physx import get_physx_simulation_interface
        from pxr import UsdUtils,UsdPhysics,PhysxSchema,PhysicsSchemaTools,Sdf,UsdGeom,Gf
        omni.usd.get_context().open_stage(str(staged))
        for _ in range(10):app.update()
        stage=omni.usd.get_context().get_stage()
        bodypaths=[b['path'] for b in snapshot['bodies']]
        roots=[p for p in stage.Traverse() if p.HasAPI(UsdPhysics.ArticulationRootAPI)]
        if len(roots)!=1:raise ValueError('Require one articulation root')
        rootpath=str(roots[0].GetPath())
        if initial:
            if initial['root_path']!=rootpath or set(initial['body_world_matrices'])!=set(bodypaths):
                raise ValueError('Initial configuration body identity mismatch')
            matrices={p:np.asarray(v,dtype=float) for p,v in initial['body_world_matrices'].items()}
            for matrix in matrices.values():
                if (matrix.shape!=(4,4) or not np.isfinite(matrix).all()
                        or not np.allclose(matrix[3],[0,0,0,1],atol=1e-9,rtol=0)
                        or not np.allclose(matrix[:3,:3].T@matrix[:3,:3],np.eye(3),atol=1e-8,rtol=0)
                        or abs(np.linalg.det(matrix[:3,:3])-1)>1e-8):
                    raise ValueError('Initial body transform must be rigid')
            def joint_frame(position,quaternion):
                w,x,y,z=quaternion
                matrix=np.eye(4)
                matrix[:3,:3]=[[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],
                              [2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],
                              [2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]]
                matrix[:3,3]=position
                return matrix
            for joint in snapshot['joints']:
                a,b=joint['body0'][0],joint['body1'][0]
                left=joint_frame(joint['local_pos0'],joint['local_rot0_wxyz'])
                right=joint_frame(joint['local_pos1'],joint['local_rot1_wxyz'])
                observed=np.linalg.inv(matrices[a]@left)@matrices[b]@right
                expected=np.eye(4)
                if 'axis' in joint:
                    angle=initial['q_deg'][joint['name']]
                    if not joint['limits_deg'][0]-1e-7<=angle<=joint['limits_deg'][1]+1e-7:
                        raise ValueError('Initial angle exceeds authored limits')
                    axis=np.eye(3)['XYZ'.index(joint['axis'])]
                    x,y,z=axis
                    skew=np.array([[0,-z,y],[z,0,-x],[-y,x,0]])
                    radians=math.radians(angle)
                    expected[:3,:3]+=np.sin(radians)*skew+(1-np.cos(radians))*(skew@skew)
                if not np.allclose(observed,expected,atol=1e-7,rtol=0):
                    raise ValueError('Initial transforms disagree with source joint frames')
            stage.SetEditTarget(stage.GetSessionLayer())
            prim=stage.GetPrimAtPath(rootpath)
            parent_world=np.asarray(UsdGeom.XformCache().GetLocalToWorldTransform(prim.GetParent())).T
            local=np.linalg.inv(parent_world)@matrices[rootpath]
            xform=UsdGeom.Xformable(prim)
            xform.ClearXformOpOrder()
            xform.AddTransformOp(opSuffix='initialScenario').Set(Gf.Matrix4d(local.T.tolist()))
            body=UsdPhysics.RigidBodyAPI(prim)
            body.CreateVelocityAttr(Gf.Vec3f(0))
            body.CreateAngularVelocityAttr(Gf.Vec3f(0))
            for joint in snapshot['joints']:
                if 'axis' not in joint:continue
                jointprim=stage.GetPrimAtPath(joint['path'])
                state=PhysxSchema.JointStateAPI.Apply(jointprim,UsdPhysics.Tokens.angular)
                state.CreatePositionAttr(initial['q_deg'][joint['name']])
                state.CreateVelocityAttr(0.)
                UsdPhysics.DriveAPI(jointprim,UsdPhysics.Tokens.angular).CreateTargetPositionAttr(
                    initial['q_deg'][joint['name']])
            receipt['prephysics_initial_configuration']={
                'initial_pose_sha256':sha(args.initial_pose),'pose_index':initial_index,
                'root_world_matrix':matrices[rootpath].tolist(),'q_deg':initial['q_deg'],
                'authored_in_session_layer':True,'runtime_root_resets':0,
                'applied_before_attach_and_first_step':True}
        if not PhysxSchema.PhysxArticulationAPI(roots[0]).GetEnabledSelfCollisionsAttr().Get():
            raise ValueError('Self collision must remain enabled')
        if any(UsdPhysics.RigidBodyAPI(stage.GetPrimAtPath(p)).GetKinematicEnabledAttr().Get() for p in bodypaths):
            raise ValueError('Every robot body must remain dynamic')
        damping_edits = {}
        if args.drive_damping_scale != 1.:
            stage.SetEditTarget(stage.GetSessionLayer())
            for joint in snapshot['joints']:
                if 'axis' not in joint:
                    continue
                drive = UsdPhysics.DriveAPI(stage.GetPrimAtPath(joint['path']), UsdPhysics.Tokens.angular)
                if not drive or drive.GetTypeAttr().Get() != 'force':
                    raise ValueError('Damping diagnostic requires existing angular force drives')
                attribute = drive.GetDampingAttr()
                before = float(attribute.Get())
                if not math.isfinite(before) or before <= 0:
                    raise ValueError('Existing damping must be positive and finite')
                attribute.Set(before*args.drive_damping_scale)
                damping_edits[joint['name']] = {
                    'before_usd_per_degree':before,
                    'after_usd_per_degree':float(attribute.Get()),
                    'expected_native_per_radian':float(attribute.Get())*180/math.pi}
            receipt['prephysics_damping_configuration'] = {
                'authored_in_session_layer':True, 'edits':damping_edits,
                'stiffness_torque_caps_limits_geometry_preserved':True,
                'scope':'Controller diagnostic only; not a qualified actuator specification'}
        scenes=[p for p in stage.Traverse() if p.IsA(UsdPhysics.Scene)]
        if len(scenes)!=1:raise ValueError('Require one explicit physics scene')
        scene=UsdPhysics.Scene(scenes[0]);physxscene=PhysxSchema.PhysxSceneAPI(scenes[0])
        hz=physxscene.GetTimeStepsPerSecondAttr().Get();dt=1./hz
        if hz!=120 or not physxscene.GetEnableGPUDynamicsAttr().Get():
            raise ValueError('Expected authored 120 Hz GPU scene')
        receipt['usd_solver_configuration'] = {
            'scene_solver_type':scenes[0].GetAttribute('physxScene:solverType').Get(),
            'articulation':{
                name:{
                    'value':roots[0].GetAttribute('physxArticulation:'+name).Get(),
                    'authored':roots[0].GetAttribute('physxArticulation:'+name).HasAuthoredValueOpinion()}
                for name in ('solverPositionIterationCount','solverVelocityIterationCount')},
            'rigid_bodies':{
                path:{name:stage.GetPrimAtPath(path).GetAttribute('physxRigidBody:'+name).Get()
                      for name in ('solverPositionIterationCount','solverVelocityIterationCount')}
                for path in bodypaths},
            'scope':'USD authored values and schema fallbacks; not a native solver-count readback'}
        gravity=float(scene.GetGravityMagnitudeAttr().Get())
        receipt['authored_gravity_m_s2']=gravity
        if abs(gravity-9.81)>1e-5 or list(scene.GetGravityDirectionAttr().Get())!=[0.,0.,-1.]:
            raise ValueError('Expected Earth gravity in Z-up stage')
        for path in bodypaths:
            PhysxSchema.PhysxContactReportAPI.Apply(stage.GetPrimAtPath(path)).CreateThresholdAttr(0.)
        if args.capture_dir:
            import importlib.util
            module_spec = importlib.util.spec_from_file_location('native_capture_frozen', args.capture_module)
            capture_module = importlib.util.module_from_spec(module_spec)
            module_spec.loader.exec_module(capture_module)
            authored_root = np.asarray(initial['body_world_matrices'][rootpath] if initial else
                                       next(b['world_matrix'] for b in snapshot['bodies']
                                            if b['path'] == rootpath))[:3, 3]
            x, y, _ = authored_root
            capture = capture_module.NativeCapture(
                stage, args.capture_dir, candidate,
                eye=[x+1.8, y-2.5, 1.15], target=[x, y-.08, .43])
            receipt['native_capture'] = {
                'output_directory': str(args.capture_dir),
                'module_sha256': sha(args.capture_module), 'frame_rate': args.capture_fps,
                'physics_steps_per_frame': hz//args.capture_fps,
                'status': 'prepared_before_physics_attachment'}
        stageid=UsdUtils.StageCache.Get().GetId(stage).ToLongInt()
        simulation=get_physx_simulation_interface();check_memory()
        simulation.attach_stage(stageid);attached=True;check_memory()
        # PhysX inserts parsed articulations into the scene on the first step.
        # Record this real bootstrap step; it is not a hidden warm-up or reset.
        receipt.update(simulation_started=True,physics_step_dt_s=dt,
                       initial_unobserved_bootstrap_steps=1)
        save(args.receipt,receipt)
        simulation.simulate(dt,0.);simulation.fetch_results();check_memory()
        view=tensors.create_simulation_view('warp',stageid)
        robot=view.create_articulation_view(rootpath)
        names=[p.rsplit('/',1)[-1] for p in robot.dof_paths[0]]
        expected={j['name']:j for j in snapshot['joints'] if 'axis' in j}
        masses=robot.get_masses().numpy()[0]
        com_frames=robot.get_coms().numpy()[0]
        inertias=robot.get_inertias().numpy()[0].reshape(-1,3,3)
        limits=robot.get_dof_limits().numpy()[0]
        mass_total=float(masses.sum())
        expected_mass_total=sum(b['mass_kg'] for b in snapshot['bodies'])
        inertia_finite=bool(np.isfinite(inertias).all())
        eigenvalues=np.linalg.eigvalsh(inertias) if inertia_finite else None
        predicates={
            'single_articulation':robot.count==1,
            'link_count':robot.max_links==len(bodypaths),
            'dof_count':robot.max_dofs==len(expected),
            'dof_names':set(names)==set(expected),
            'positive_masses':bool(np.all(masses>0)),
            'finite_com_frames':bool(np.isfinite(com_frames).all()),
            'mass_total':abs(mass_total-expected_mass_total)<=1e-3,
            'finite_inertias':inertia_finite,
            'positive_inertias':eigenvalues is not None and bool(np.all(eigenvalues>0)),
            'tensor_readback_device':view.device_ordinal in (-1,0),
            'gpu_dynamics_authored':bool(physxscene.GetEnableGPUDynamicsAttr().Get()),
        }
        receipt['native_structure_observation']={
            'articulation_count':robot.count,'link_count':robot.max_links,
            'expected_link_count':len(bodypaths),'dof_count':robot.max_dofs,
            'expected_dof_count':len(expected),'dof_names':names,
            'expected_dof_names':sorted(expected),'masses_kg':masses.tolist(),
            'center_of_mass_frames_local_xyzw':com_frames.tolist(),
            'mass_total_kg':mass_total,'expected_mass_total_kg':expected_mass_total,
            'inertia_eigenvalues':eigenvalues.tolist() if eigenvalues is not None else None,
            'tensor_device':str(view.device),'tensor_device_ordinal':view.device_ordinal,
            'tensor_readback_note':'CPU tensor readback is allowed with GPU dynamics.',
            'predicates':predicates}
        receipt['native_drive_observation']={
            'dof_names':names,
            'stiffnesses':robot.get_dof_stiffnesses().numpy()[0].tolist(),
            'dampings':robot.get_dof_dampings().numpy()[0].tolist(),
            'maximum_forces':robot.get_dof_max_forces().numpy()[0].tolist()}
        material_properties = robot.get_material_properties().numpy()[0]
        contact_offsets = robot.get_contact_offsets().numpy()[0]
        rest_offsets = robot.get_rest_offsets().numpy()[0]
        if (not np.isfinite(material_properties).all()
                or not np.isfinite(contact_offsets).all()
                or not np.isfinite(rest_offsets).all()):
            raise ValueError('Non-finite native collision material or offsets')
        materials, material_counts = np.unique(
            material_properties.reshape(-1, 3), axis=0, return_counts=True)
        receipt['native_collision_properties'] = {
            'shape_count': int(material_properties.reshape(-1, 3).shape[0]),
            'material_columns': ['static_friction', 'dynamic_friction', 'restitution'],
            'unique_materials': [
                {'properties': values.tolist(), 'shape_count': int(count)}
                for values, count in zip(materials, material_counts)],
            'contact_offset_range_m': [float(contact_offsets.min()), float(contact_offsets.max())],
            'rest_offset_range_m': [float(rest_offsets.min()), float(rest_offsets.max())],
            'scope': 'Native robot shape readback; terrain material is not inferred'}
        if damping_edits and not np.allclose(
                receipt['native_drive_observation']['dampings'],
                [damping_edits[name]['expected_native_per_radian'] for name in names],
                rtol=1e-6, atol=1e-5):
            raise ValueError('Native damping readback differs from the declared controller configuration')
        save(args.receipt,receipt)
        if not all(predicates.values()):
            raise ValueError('Native structure predicates failed: '+str(
                [key for key,value in predicates.items() if not value]))
        def reject_physics_errors():
            log=args.kit_log.read_text(errors='replace') if args.kit_log.exists() else ''
            errors=[line for line in log.splitlines() if
                    'PhysX error:' in line or '[Error] [omni.physx' in line
                    or 'forcing CPU simulation' in line]
            if errors:
                receipt['native_physics_errors']=errors
                raise ValueError('Native PhysX error or CPU fallback; diagnostic rejected')
        reject_physics_errors()
        authored_mass={b['path']:b['mass_kg'] for b in snapshot['bodies']}
        if set(robot.link_paths[0])!=set(authored_mass) or any(
                abs(float(mass)-authored_mass[path])>1e-4
                for path,mass in zip(robot.link_paths[0],masses)):
            raise ValueError('Native per-link masses differ from authored masses')
        authored_com={b['path']:b['center_of_mass'] for b in snapshot['bodies']}
        if any(not np.allclose(com[:3],authored_com[path],atol=1e-6,rtol=0)
               for path,com in zip(robot.link_paths[0],com_frames)):
            raise ValueError('Native per-link centers of mass differ from the planning snapshot')
        for name,limit in zip(names,limits):
            if not np.allclose(limit,np.radians(expected[name]['limits_deg']),atol=1e-5,rtol=0):
                raise ValueError('Native joint limits differ from snapshot')
        ground='/World/Environment/terrain/visual/node_171_Terrain_Walk_Test'
        contacts=[view.create_rigid_contact_view('/World/Robot/foot'+side,[ground],4096)
                  for side in ('L','R')]
        if not all(c.sensor_count==1 and c.filter_count==1 for c in contacts):
            raise ValueError('Foot-ground contact sensors did not bind')
        colliding_bodies=[p for p in bodypaths if any(
            m['collision'] and m['path'].startswith(p+'/') for m in snapshot['meshes'])]
        body_ground=view.create_rigid_contact_view(
            colliding_bodies,[[ground] for _ in colliding_bodies],0)
        if body_ground.sensor_count!=len(colliding_bodies) or body_ground.filter_count!=1:
            raise ValueError('Whole-body ground sensors did not bind')
        self_contacts=None
        if schedule:
            self_contacts=view.create_rigid_contact_view(
                colliding_bodies,[colliding_bodies for _ in colliding_bodies],16384)
            if (self_contacts.sensor_count!=len(colliding_bodies)
                    or self_contacts.filter_count!=len(colliding_bodies)):
                raise ValueError('Named body-pair self-contact sensors did not bind')
            receipt['self_contact_sensor_body_paths']=list(self_contacts.sensor_paths)
            receipt['self_contact_filter_body_paths']=colliding_bodies
        root_id=PhysicsSchemaTools.sdfPathToInt(Sdf.Path(rootpath))
        if schedule:
            import warp as wp
            times=np.array([k['time_s'] for k in schedule['knots']])
            targets=np.radians([[transitions['poses'][k['pose_index']]['q_deg'][n]
                                 for n in names] for k in schedule['knots']])
            if np.any(targets<limits[:,0]-1e-7) or np.any(targets>limits[:,1]+1e-7):
                raise ValueError('Motion targets exceed native joint limits')
            indices=wp.array([0],dtype=wp.uint32,device=view.device)
            cooked=json.loads(gzip.decompress(args.cooked.read_bytes()))
            if cooked['candidate_sha256']!=candidate or cooked['vertex_frame']!='rigid-body-local':
                raise ValueError('Unexpected cooked vertex frame or candidate')
            foot_vertices={side:np.unique(np.concatenate([
                hull['vertices'] for mesh in cooked['meshes']
                if mesh['body']=='/World/Robot/foot'+side for hull in mesh['hulls']]),axis=0)
                for side in ('L','R')}
            terrain=UsdGeom.Mesh(stage.GetPrimAtPath(ground))
            terrain_transform=np.array(UsdGeom.XformCache().GetLocalToWorldTransform(terrain.GetPrim())).T
            points=np.asarray(terrain.GetPointsAttr().Get())
            ground_top=float((points@terrain_transform[:3,:3].T+terrain_transform[:3,3])[:,2].max())
            receipt['foot_clearance_reference_ground_top_m']=ground_top
            receipt['foot_height_reference_note']=(
                'Highest terrain point, not local surface. Positive height is a conservative '
                'clearance bound; negative height does not establish penetration.')
            receipt['drive_control']=(
                'Existing angular force drives with recorded damping scale; position targets only')
            def rotation_xyzw(q):
                x,y,z,w=q
                return np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],
                                 [2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],
                                 [2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]])
        receipt.update(native_structure={'links':robot.max_links,'dofs':robot.max_dofs,
                       'shapes':robot.max_shapes,'dof_names':names,'masses_kg':masses.tolist(),
                       'link_paths':robot.link_paths[0],'inertias':inertias.tolist(),
                       'center_of_mass_frames_local_xyzw':com_frames.tolist(),
                       'device':str(view.device),'device_ordinal':view.device_ordinal,
                       'limits_rad':limits.tolist(),'root_kinematic':False,
                       'authored_self_collision_enabled':True},
                       physics_step_dt_s=dt,step_method='IPhysxSimulation.simulate(dt,time), fetch_results',
                       instrumentation_added='Contact report API threshold zero on robot bodies',
                       gpu_name=subprocess.check_output(['nvidia-smi','--id=0','--query-gpu=name','--format=csv,noheader'],text=True).strip())
        start=robot.get_root_transforms().numpy()[0].copy();samples=[];fall=False;invalid=False
        trace_checkpoint_dir.mkdir()
        checkpoint_start = 0
        receipt['trace_checkpoints'] = []
        receipt['trace_checkpoints_are_acceptance'] = False
        def checkpoint_trace():
            nonlocal checkpoint_start
            if checkpoint_start == len(samples):
                return
            chunk = {
                'candidate_sha256': candidate, 'dt_s': dt, 'dof_names': names,
                'first_observation_index': checkpoint_start,
                'samples': samples[checkpoint_start:],
                'root_resets': 0, 'external_support_applied': False,
                'incomplete_diagnostic_checkpoint': True}
            path = trace_checkpoint_dir/f'chunk-{checkpoint_start:06d}.json.gz'
            temporary = path.with_suffix(path.suffix+'.tmp')
            temporary.write_bytes(gzip.compress(
                json.dumps(chunk, allow_nan=False).encode(), mtime=0))
            temporary.replace(path)
            record = {
                'path': str(path), 'sha256': sha(path),
                'first_observation_index': checkpoint_start,
                'observation_count': len(chunk['samples']),
                'first_time_s': chunk['samples'][0]['time_s'],
                'last_time_s': chunk['samples'][-1]['time_s']}
            with _write_lock:
                receipt['trace_checkpoints'].append(record)
                checkpoint_start = len(samples)
                save(args.receipt, receipt)
        receipt['ground_sensor_body_paths']=list(body_ground.sensor_paths)
        authored_start=(initial['body_world_matrices'][rootpath] if initial else
                        next(b['world_matrix'] for b in snapshot['bodies'] if b['path']==rootpath))
        receipt['authored_start_root_m']=np.array(authored_start)[:3,3].tolist()
        receipt['root_after_bootstrap_m']=start[:3].tolist()
        if not np.allclose(start[:3],np.array(authored_start)[:3,3],atol=.002,rtol=0):
            raise ValueError('Root moved more than 2 mm during the first gravity step')
        if initial:
            initial_q=np.radians([initial['q_deg'][n] for n in names])
            q_error=float(np.max(abs(robot.get_dof_positions().numpy()[0]-initial_q)))
            links=robot.get_link_transforms().numpy()[0]
            position_error=max(float(np.linalg.norm(
                link[:3]-np.asarray(initial['body_world_matrices'][path])[:3,3]))
                for path,link in zip(robot.link_paths[0],links))
            receipt['initial_configuration_after_bootstrap']={
                'maximum_joint_error_deg':math.degrees(q_error),
                'maximum_link_position_error_m':position_error}
            if q_error>math.radians(.1) or position_error>.002:
                raise ValueError('Native initial state disagrees with planned configuration')
        steps=round(args.seconds/dt);receipt['simulation_started']=True;save(args.receipt,receipt)
        receipt['keep_awake_requested']=args.keep_awake
        receipt['loaded_contact_polygon_minimum_normal_impulse_Ns']=1e-6
        receipt['wake_up_calls']=0
        def capture_state():
            return (robot.get_link_transforms().numpy()[0].copy(),
                    robot.get_dof_positions().numpy()[0].copy(),
                    robot.get_root_velocities().numpy()[0].copy(),
                    robot.get_dof_velocities().numpy()[0].copy())
        if capture:
            capture.capture(1, dt, robot.link_paths[0], *capture_state()[:2], capture_state)
        for i in range(1,steps):
            loop_started=time.perf_counter()
            commanded=None
            if schedule:
                t=i*dt
                scheduled_t = t
                for segment in time_warps:
                    phase_start, phase_end = segment['start_s'], segment['end_s']
                    if phase_start <= t <= phase_end:
                        u = (t-phase_start)/(phase_end-phase_start)
                        scheduled_t = phase_start+(phase_end-phase_start)*u*u*u*(10+u*(-15+6*u))
                        break
                right=min(int(np.searchsorted(times,scheduled_t,side='right')),len(times)-1)
                left=max(0,right-1)
                fraction=(scheduled_t-times[left])/(times[right]-times[left])
                commanded=targets[left]+fraction*(targets[right]-targets[left])
                robot.set_dof_position_targets(
                    wp.array(commanded[None,:].astype(np.float32),device=view.device),indices)
                native_targets=robot.get_dof_position_targets().numpy()[0].copy()
                if not np.allclose(native_targets,commanded,atol=1e-6,rtol=0):
                    raise ValueError('Native position target readback disagrees with command')
            targets_finished=time.perf_counter()
            if args.keep_awake:
                simulation.wake_up(stageid,root_id)
                receipt['wake_up_calls']+=1
            simulation.simulate(dt,i*dt);simulation.fetch_results()
            simulation_finished=time.perf_counter()
            pose=robot.get_root_transforms().numpy()[0].copy()
            q=robot.get_dof_positions().numpy()[0].copy()
            up=float(1-2*(pose[3]**2+pose[4]**2))
            invalid=not bool(np.isfinite(pose).all() and np.isfinite(q).all())
            fall=bool(up<.65 or pose[2]<.65*start[2])
            feet=[]
            for sensor in contacts:
                impulse=sensor.get_contact_force_matrix(1.).numpy().reshape(-1,3).sum(0)
                force=sensor.get_contact_force_matrix(dt).numpy().reshape(-1,3).sum(0)
                forces,points,normals,distances,counts,starts=sensor.get_contact_data(1.)
                counts=counts.numpy().ravel();starts=starts.numpy().ravel();distances=distances.numpy().ravel()
                if int(counts.sum())>=sensor.max_contact_data_count:raise ValueError('Contact buffer saturated')
                active=[float(x) for count,start_index in zip(counts,starts)
                        for x in distances[int(start_index):int(start_index+count)]]
                point_indices=np.array([j for count,start_index in zip(counts,starts)
                                        for j in range(int(start_index),int(start_index+count))],dtype=int)
                contact_points=points.numpy().reshape(-1,3)[point_indices]
                contact_weights=np.abs(forces.numpy().ravel()[point_indices])
                total_normal_impulse=float(contact_weights.sum())
                center_of_pressure=(np.average(contact_points,axis=0,weights=contact_weights).tolist()
                                    if total_normal_impulse>1e-12 else None)
                loaded_polygon=contact_hull_xy(contact_points[contact_weights>1e-6])
                feet.append({'impulse_Ns':impulse.tolist(),'force_N':force.tolist(),
                             'scaling_error_N':float(np.max(abs(force-impulse/dt))),
                             'count':int(counts.sum()),'minimum_separation_m':min(active) if active else None,
                             'normal_impulse_weighted_contact_center_world_m':center_of_pressure,
                             'total_absolute_normal_impulse_Ns':total_normal_impulse,
                             'loaded_contact_polygon_world_xy_m':loaded_polygon})
            samples.append({'time_s':(i+1)*dt,'root_position_m':pose[:3].tolist(),
                            'root_quaternion_xyzw':pose[3:].tolist(),'up_z':up,
                            'actual_dof_rad':q.tolist(),'feet':feet,
                            'actual_dof_velocity_rad_s':robot.get_dof_velocities().numpy()[0].tolist(),
                            'root_sleeping':bool(simulation.is_sleeping(stageid,root_id)),
                            'root_velocity':robot.get_root_velocities().numpy()[0].tolist(),
                            'body_ground_force_N':body_ground.get_contact_force_matrix(dt).numpy().reshape(-1,3).tolist()})
            base_readback_finished=time.perf_counter()
            if schedule:
                links=robot.get_link_transforms().numpy()[0].copy()
                pair_forces=self_contacts.get_contact_force_matrix(dt).numpy()
                normal_impulses,contact_points,contact_normals,separations,pair_counts,pair_starts=(
                    self_contacts.get_contact_data(1.))
                pair_counts=pair_counts.numpy()
                pair_starts=pair_starts.numpy()
                separations=separations.numpy().ravel()
                normal_impulses=normal_impulses.numpy().ravel()
                if int(pair_counts.sum())>=self_contacts.max_contact_data_count:
                    raise ValueError('Self-contact buffer saturated')
                active_self_contacts=[]
                for sensor_index,filter_index in np.argwhere(pair_counts>0):
                    count=int(pair_counts[sensor_index,filter_index])
                    begin=int(pair_starts[sensor_index,filter_index])
                    active_self_contacts.append({
                        'body_a':self_contacts.sensor_paths[sensor_index],
                        'body_b':colliding_bodies[filter_index],
                        'count':count,
                        'force_N':pair_forces[sensor_index,filter_index].tolist(),
                        'minimum_separation_m':float(separations[begin:begin+count].min()),
                        'maximum_normal_impulse_Ns':float(
                            np.abs(normal_impulses[begin:begin+count]).max())})
                foot_observation={}
                for side in ('L','R'):
                    foot=links[robot.link_paths[0].index('/World/Robot/foot'+side)]
                    world=foot_vertices[side]@rotation_xyzw(foot[3:]).T+foot[:3]
                    foot_observation[side]={'position_m':foot[:3].tolist(),
                        'quaternion_xyzw':foot[3:].tolist(),
                        'cooked_lowest_above_ground_m':float(world[:,2].min()-ground_top)}
                native_com=(sum(float(mass)*(rotation_xyzw(link[3:])@com[:3]+link[:3])
                                for mass,link,com in zip(masses,links,com_frames))/mass_total).tolist()
                measured_polygons={side:feet[i]['loaded_contact_polygon_world_xy_m']
                                   for i,side in enumerate(('L','R'))}
                measured_polygons['both']=contact_hull_xy(
                    measured_polygons['L']+measured_polygons['R'])
                samples[-1].update(commanded_dof_rad=commanded.tolist(),
                                   scheduled_path_time_s=scheduled_t,
                                   native_position_targets_rad=native_targets.tolist(),
                                   scheduled_lower_pose_index=schedule['knots'][left]['pose_index'],
                                   scheduled_upper_pose_index=schedule['knots'][right]['pose_index'],
                                   native_center_of_mass_world_m=native_com,
                                   measured_contact_support_margins_m={
                                       side:contact_margin(poly,native_com)
                                       for side,poly in measured_polygons.items()},
                                   projected_joint_forces_Nm=robot.get_dof_projected_joint_forces().numpy()[0].tolist(),
                                   explicit_dof_actuation_forces_Nm=robot.get_dof_actuation_forces().numpy()[0].tolist(),
                                   self_contacts=active_self_contacts,
                                   foot_geometry=foot_observation,
                                   link_transforms_xyzw=links.tolist())
            diagnostics_finished=time.perf_counter()
            if capture and i % (hz//args.capture_fps) == 0:
                capture.capture(i+1, (i+1)*dt, robot.link_paths[0],
                                *capture_state()[:2], capture_state)
            capture_finished=time.perf_counter()
            samples[-1]['wall_timing_s']={
                'targets':targets_finished-loop_started,
                'simulate_and_fetch':simulation_finished-targets_finished,
                'root_and_foot_readback':base_readback_finished-simulation_finished,
                'motion_diagnostics':diagnostics_finished-base_readback_finished,
                'native_capture':capture_finished-diagnostics_finished,
                'total_observation':capture_finished-loop_started}
            if i%60==0:
                checkpoint_trace()
                check_memory();receipt['completed_steps']=i+1
                receipt['latest_observation']=samples[-1]
                save(args.receipt,receipt)
                print('SETTLE_PROGRESS',i+1,up,pose[:3].tolist(),flush=True)
            if fall or invalid:break
        checkpoint_trace()
        late=[s for s in samples if s['time_s']>=args.seconds-.5]
        ratio=float(np.median([sum(f['force_N'][2] for f in s['feet']) for s in late])/(float(masses.sum())*gravity)) if late else None
        maximum_limit_excess=max(max(float(np.max(limits[:,0]-s['actual_dof_rad'])),
                                    float(np.max(np.array(s['actual_dof_rad'])-limits[:,1])),0.) for s in samples)
        force_error=max(f['scaling_error_N'] for s in samples for f in s['feet'])
        minimum_root_up=min(s['up_z'] for s in samples)
        uprightness_pass=minimum_root_up>=.95
        late_awake=bool(late) and all(not s['root_sleeping'] for s in late)
        quiet = None
        if not schedule and late:
            root_range = np.ptp(np.asarray([s['root_position_m'] for s in late]), axis=0)
            foot_force_std = np.std(np.asarray([
                [foot['force_N'][2] for foot in s['feet']] for s in late]), axis=0)
            foot_force_std /= float(masses.sum())*gravity
            quiet = {
                'window_start_s':args.seconds-.5,
                'root_position_axis_ranges_m':root_range.tolist(),
                'foot_vertical_force_std_over_weight':foot_force_std.tolist(),
                'maximum_root_axis_range_m':.001,
                'maximum_foot_force_std_over_weight':.05,
                'pass':bool(np.max(root_range)<=.001 and np.max(foot_force_std)<=.05),
                'scope':'Stationary contact-oscillation gate; not walking or balance acceptance'}
            receipt['settle_contact_stability'] = quiet
        reject_physics_errors()
        passed=(not fall and not invalid and len(samples)==steps-1 and ratio is not None
                and .9<=ratio<=1.1 and force_error<.01
                and late_awake and uprightness_pass
                and maximum_limit_excess<np.radians(.1)
                and robot.max_shapes==cook['hull_count'])
        if not schedule:
            receipt['quiet_settle_pass'] = bool(quiet and quiet['pass'])
            passed = bool(passed and receipt['quiet_settle_pass'])
        payload={'candidate_sha256':candidate,'dt_s':dt,'dof_names':names,'samples':samples,
                 'root_resets':0,'external_support_applied':False}
        args.out.write_bytes(gzip.compress(json.dumps(payload,allow_nan=False).encode(),mtime=0))
        receipt.update(status='completed',settle_pass=passed,fall_detected=fall,invalid_physics_state=invalid,
                       completed_steps=len(samples)+1,observed_steps=len(samples),
                       sample_output_sha256=sha(args.out),
                       settled_ground_force_over_weight=ratio,maximum_force_scaling_error_N=force_error,
                       final_support_window_all_awake=late_awake,
                       maximum_joint_limit_excess_rad=maximum_limit_excess,
                       minimum_root_up_z=minimum_root_up,
                       minimum_required_root_up_z=.95,
                       uprightness_pass=uprightness_pass,
                       sleeping_sample_count=sum(s['root_sleeping'] for s in samples),
                       nonzero_foot_force_sample_count=sum(
                           any(abs(x)>0 for foot in s['feet'] for x in foot['force_N'])
                           for s in samples),
                       runtime_shape_count_matches_cook=robot.max_shapes==cook['hull_count'])
        if schedule:
            receipt['settle_pass']=False
            receipt['motion_completed_without_fall']=bool(
                not fall and not invalid and len(samples)==steps-1)
            receipt['motion_probe_pass']=bool(passed)
        if sha(args.usd)!=candidate or sha(staged)!=candidate:raise ValueError('Input changed during native diagnostic')
    except BaseException as error:
        receipt.update(status='failed',error=repr(error),traceback=traceback.format_exc())
        raise
    finally:
        stop.set()
        if capture and capture.frames:
            try:
                capture_path = capture.finish()
                receipt['native_capture'].update(
                    status='captured', manifest=str(capture_path),
                    manifest_sha256=sha(capture_path), frame_count=len(capture.frames))
            except Exception as error:
                receipt.update(status='failed', settle_pass=False, motion_probe_pass=False,
                               capture_error=repr(error))
        if attached:
            try:simulation.detach_stage()
            except Exception as error:receipt['detach_error']=repr(error)
        save(args.receipt,receipt)
        if app:app.close(exit_code=0 if receipt.get('settle_pass') or receipt.get('motion_probe_pass') else 1)


if __name__=='__main__':main()
