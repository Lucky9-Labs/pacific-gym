"""Author one explicitly requested GPU aggregate-pair capacity in a fresh USD."""
import argparse,hashlib,json
from pathlib import Path
from pxr import Usd,UsdPhysics,Sdf

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('usd','out','receipt'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--usd-sha256',required=True)
    p.add_argument('--found-lost-aggregate-pairs',type=int,required=True)
    a=p.parse_args()
    if a.out.exists() or a.receipt.exists() or sha(a.usd)!=a.usd_sha256:
        raise ValueError('Require pinned input and fresh outputs')
    if not 1024<=a.found_lost_aggregate_pairs<=2**24:
        raise ValueError('Explicit capacity must be 1024 through 16777216')
    stage=Usd.Stage.Open(Usd.Stage.Open(str(a.usd)).Flatten())
    baseline=stage.GetRootLayer().ExportToString()
    scenes=[prim for prim in stage.Traverse() if prim.IsA(UsdPhysics.Scene)]
    if len(scenes)!=1:raise ValueError('Require one physics scene')
    prim=scenes[0];name='physxScene:gpuFoundLostAggregatePairsCapacity';attr=prim.GetAttribute(name)
    before={'existed':bool(attr),'authored':bool(attr and attr.HasAuthoredValueOpinion()),'value':attr.Get() if attr else None}
    if attr and attr.GetTypeName()!=Sdf.ValueTypeNames.UInt:raise ValueError('Unexpected capacity type')
    prim.CreateAttribute(name,Sdf.ValueTypeNames.UInt,custom=False).Set(a.found_lost_aggregate_pairs)
    stage.GetRootLayer().Export(str(a.out));readback=Usd.Stage.Open(str(a.out));target=readback.GetPrimAtPath(prim.GetPath())
    if target.GetAttribute(name).Get()!=a.found_lost_aggregate_pairs:raise ValueError('Serialized capacity changed')
    if not before['existed']:target.RemoveProperty(name)
    elif before['authored']:target.GetAttribute(name).Set(before['value'])
    else:target.GetAttribute(name).Clear()
    if readback.GetRootLayer().ExportToString()!=baseline:raise ValueError('Unexpected USD change')
    if sha(a.usd)!=a.usd_sha256:raise ValueError('Input changed')
    r={'status':'capacity_changed_requires_native_validation','base_candidate_sha256':a.usd_sha256,
       'candidate_sha256':sha(a.out),'script_sha256':sha(Path(__file__)),'path':str(prim.GetPath()),
       'attribute':name,'before':before,'after':a.found_lost_aggregate_pairs,
       'all_other_usd_content_preserved':True,'physics_accepted':False}
    a.receipt.write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r))
if __name__=='__main__':main()
