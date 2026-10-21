"""Audit a stationary native hold; average support alone is not quiet contact."""
import argparse,gzip,hashlib,json,sys
from pathlib import Path
import numpy as np
from scipy.spatial import ConvexHull
from pacific_gym.quasistatic_plan import Model
from pacific_gym.terrain import TriangleTerrain

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(gzip.decompress(p.read_bytes()) if p.suffix=='.gz' else p.read_text())
def rotation(q):
 x,y,z,w=q
 return np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],[2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],[2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]])
def support(points,com):
 points=np.asarray(points).reshape(-1,2)
 if len(points)<3 or np.linalg.matrix_rank(points-points[0],tol=1e-10)<2:return None
 h=ConvexHull(points);return float(np.min(-h.equations[:,:2]@com[:2]-h.equations[:,2]))
def main():
 parser=argparse.ArgumentParser(description=__doc__)
 for name in ['receipt','samples','snapshot','cooked','cook-receipt','source-geometry','plan','out']:parser.add_argument('--'+name,type=Path,required=True)
 parser.add_argument('--start',type=float,required=True);parser.add_argument('--end',type=float,required=True);args=parser.parse_args()
 if args.out.exists():raise FileExistsError(args.out)
 r,t,s,c,cr,g,p=[read(getattr(args,n)) for n in ['receipt','samples','snapshot','cooked','cook_receipt','source_geometry','plan']]
 if (r['sample_output_sha256']!=sha(args.samples) or r['evidence_sha256']['snapshot']!=sha(args.snapshot) or r['evidence_sha256']['cook_receipt']!=sha(args.cook_receipt) or cr['output_sha256']!=sha(args.cooked) or r['evidence_sha256']['planned_poses']!=sha(args.plan) or p['source_geometry_sha256']!=sha(args.source_geometry) or len({r['candidate_sha256'],t['candidate_sha256'],s['usd_sha256'],c['candidate_sha256'],p['candidate_sha256']})!=1):raise ValueError('Native trace, geometry, plan and cook lineage mismatch')
 samples=[z for z in t['samples'] if args.start<=z['time_s']<=args.end]
 if not 0<=args.start<args.end<=t['samples'][-1]['time_s'] or len(samples)<3:raise ValueError('Stationary window must be covered by the native trace')
 names=t['dof_names'];target=np.radians([r['prephysics_initial_configuration']['q_deg'][n] for n in names])
 if any('commanded_dof_rad' in z and not np.allclose(z['commanded_dof_rad'],target,atol=1e-7,rtol=0) for z in samples):raise ValueError('Window contains commanded motion')
 m=Model(s,g);m.use_cooked_feet(c,sha(args.cooked));m.terrain=TriangleTerrain(s,g)
 for side in ['L','R']:m.set_terrain_placement(side,p['initial_terrain_placements'][side])
 indices=[names.index(n) for n in m.names];grounded=m.transforms(target[indices],'L');planned_com=m.center_of_mass(grounded);weight=sum(r['native_structure']['masses_kg'])*r['authored_gravity_m_s2'];rows=[]
 for z in samples:
  fk=m.transforms(np.asarray(z['actual_dof_rad'])[indices],'L');native_root=np.eye(4);native_root[:3,:3]=rotation(z['root_quaternion_xyzw']);native_root[:3,3]=z['root_position_m'];alignment=native_root@np.linalg.inv(fk[m.root]);tf={path:alignment@M for path,M in fk.items()};com=m.center_of_mass(tf);row={'time_s':z['time_s'],'fk_from_native_root_and_joints_com_world_m':com.tolist(),'com_minus_planned_m':(com-planned_com).tolist(),'feet':{}}
  both=[]
  for i,side in enumerate(['L','R']):
   foot=tf[m.foot_paths[side]];world=m.foot_vertices[side]@foot[:3,:3].T+foot[:3,3];height,_=m.terrain.query(world[:,:2]);polygon=z['feet'][i]['loaded_contact_polygon_world_xy_m'];both.extend(polygon);normal=foot[:3,:3]@np.asarray(p['initial_terrain_placements'][side]['sole_support_normal_local']);row['feet'][side]={'inferred_minimum_cooked_vertex_gap_m':float((world[:,2]-height).min()),'inferred_sole_normal_tilt_deg':float(np.degrees(np.arccos(np.clip(normal[2],-1,1)))),'loaded_contact_vertex_count':len(polygon),'inferred_com_loaded_polygon_margin_m':support(polygon,com)}
  row['inferred_com_both_loaded_polygon_margin_m']=support(both,com);rows.append(row)
 q=np.asarray([z['actual_dof_rad'] for z in samples]);root=np.asarray([z['root_position_m'] for z in samples]);velocity=np.asarray([z['root_velocity'] for z in samples]);dof_velocity=np.asarray([z['actual_dof_velocity_rad_s'] for z in samples]) if all('actual_dof_velocity_rad_s' in z for z in samples) else None;forces=np.asarray([[z['feet'][i]['force_N'][2] for i in range(2)] for z in samples])/weight
 result={'candidate_sha256':r['candidate_sha256'],'evidence_sha256':{k:sha(v) for k,v in vars(args).items() if isinstance(v,Path) and k!='out'},'script_sha256':sha(Path(__file__)),'model_sha256':sha(Path(sys.modules[Model.__module__].__file__)),'window_s':[args.start,args.end],'sample_count':len(samples),'drive_damping_scale':r.get('drive_damping_scale',1.),'root_position_range_m':np.ptp(root,axis=0).tolist(),'root_linear_velocity_rms_m_s':float(np.sqrt(np.mean(np.sum(velocity[:,:3]**2,axis=1)))),'root_angular_velocity_rms_rad_s':float(np.sqrt(np.mean(np.sum(velocity[:,3:]**2,axis=1)))),'joint_position_error_rms_deg':dict(zip(names,np.degrees(np.sqrt(np.mean((q-target)**2,axis=0))).tolist())),'finite_difference_joint_velocity_rms_rad_s':dict(zip(names,np.sqrt(np.mean((np.diff(q,axis=0)/t['dt_s'])**2,axis=0)).tolist())),'reported_joint_velocity_rms_rad_s':None if dof_velocity is None else dict(zip(names,np.sqrt(np.mean(dof_velocity**2,axis=0)).tolist())),'feet':{},'maximum_com_deviation_from_planned_m':max(float(np.linalg.norm(z['com_minus_planned_m'])) for z in rows),'physics_accepted':False,'walking_accepted':False,'notes':['Foot poses and CoM are inferred from native root and actual joints through the pinned model, not independent native link readback.','Loaded polygons and forces are native contact observations.','This report describes stationary stability; it does not establish a walking cycle or actual actuator saturation.'],'samples':rows}
 for i,side in enumerate(['L','R']):result['feet'][side]={'vertical_force_over_weight_mean':float(forces[:,i].mean()),'vertical_force_over_weight_std':float(forces[:,i].std()),'vertical_force_over_weight_range':[float(forces[:,i].min()),float(forces[:,i].max())],'loaded_polygon_degenerate_fraction':float(np.mean([z['feet'][side]['loaded_contact_vertex_count']<3 for z in rows])),'maximum_inferred_sole_normal_tilt_deg':max(z['feet'][side]['inferred_sole_normal_tilt_deg'] for z in rows)}
 args.out.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n');print(json.dumps({k:v for k,v in result.items() if k not in ['samples','evidence_sha256']},indent=2))
if __name__=='__main__':main()
