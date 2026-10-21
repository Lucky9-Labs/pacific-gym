"""Audit closed visual component volumes and coincident duplicate shells.

Runs on source geometry JSON exported from Blender. Deduplication requires a
bijective vertex match within tolerance AND identical mapped triangle topology;
it never treats a small signed volume alone as evidence of a duplicate.
Requires NumPy and SciPy. Estimates do not account for overlap between parts.
"""

import argparse
import hashlib
import json
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree


def closed_components(vertices, faces):
    vertices = np.asarray(vertices, float)
    triangles = np.asarray([[f[0], f[i], f[i+1]] for f in faces for i in range(1, len(f)-1)], int)
    if vertices.ndim != 2 or vertices.shape[1] != 3 or not np.isfinite(vertices).all():
        raise ValueError("Invalid vertex array")
    if triangles.size == 0 or np.any(triangles < 0) or np.any(triangles >= len(vertices)):
        raise ValueError("Invalid triangle topology")
    edges = defaultdict(list)
    for index, face in enumerate(triangles):
        if len(set(face)) != 3:
            raise ValueError("Degenerate triangle")
        for a, b in zip(face, np.roll(face, -1)):
            edges[tuple(sorted((a, b)))].append((index, 1 if a < b else -1))
    if any(len(uses) != 2 for uses in edges.values()):
        raise ValueError("Mesh is not a closed two-manifold")
    adjacency = defaultdict(list)
    for uses in edges.values():
        (i, di), (j, dj) = uses
        relation = -di*dj
        adjacency[i].append((j, relation))
        adjacency[j].append((i, relation))
    signs = {}
    components = []
    for seed in range(len(triangles)):
        if seed in signs:
            continue
        signs[seed] = 1
        queue, members = deque([seed]), []
        while queue:
            i = queue.popleft()
            members.append(i)
            for j, relation in adjacency[i]:
                expected = signs[i]*relation
                if j in signs:
                    if signs[j] != expected:
                        raise ValueError("Mesh has nonorientable topology")
                else:
                    signs[j] = expected
                    queue.append(j)
        selected = triangles[members]
        vertex_indices, remapped = np.unique(selected.ravel(), return_inverse=True)
        points = vertices[vertex_indices]
        local_faces = remapped.reshape(-1, 3)
        reference = points.mean(0)
        a, b, c = (vertices[selected[:, i]]-reference for i in range(3))
        signed = np.einsum("ij,ij->i", a, np.cross(b, c))/6
        repaired = signed*np.array([signs[i] for i in members])
        oriented_faces = local_faces.copy()
        flip = np.array([signs[i] < 0 for i in members])
        oriented_faces[flip] = oriented_faces[flip][:, ::-1]
        if repaired.sum() < 0:
            oriented_faces = oriented_faces[:, ::-1]
        components.append({
            "points": points, "faces": local_faces,
            "outward_faces": oriented_faces,
            "volume_m3": abs(float(repaired.sum())),
            "raw_signed_volume_m3": float(signed.sum()),
            "faces_reoriented": sum(signs[i] < 0 for i in members),
        })
    return components


def triangle_set(faces):
    return {tuple(sorted(map(int, face))) for face in faces}


def same_shell(a, b, tolerance):
    if a["points"].shape != b["points"].shape or a["faces"].shape != b["faces"].shape:
        return False, None
    distances, mapping = cKDTree(a["points"]).query(b["points"])
    maximum = float(distances.max())
    if maximum > tolerance or len(set(map(int, mapping))) != len(mapping):
        return False, maximum
    return triangle_set(a["faces"]) == triangle_set(mapping[b["faces"]]), maximum


def mesh_volume(vertices, faces, tolerance=1e-6):
    components = closed_components(vertices, faces)
    unique, rows, ambiguous = [], [], []
    for index, component in enumerate(components):
        duplicate, distance = None, None
        for prior in unique:
            match, distance = same_shell(components[prior], component, tolerance)
            if match:
                duplicate = prior
                break
        if duplicate is None:
            for prior in unique:
                a, b = components[prior]["points"], component["points"]
                overlap = np.minimum(a.max(0), b.max(0)) - np.maximum(a.min(0), b.min(0))
                if np.all(overlap > tolerance):
                    ambiguous.append([prior, index])
            unique.append(index)
        rows.append({
            "component": index, "vertices": len(component["points"]),
            "triangles": len(component["faces"]), "volume_m3": component["volume_m3"],
            "raw_signed_volume_m3": component["raw_signed_volume_m3"],
            "faces_reoriented": component["faces_reoriented"],
            "duplicate_of": duplicate,
            "maximum_bijective_vertex_distance_m": distance if duplicate is not None else None,
        })
    return {
        "components": rows, "unique_component_count": len(unique),
        "volume_m3": sum(components[i]["volume_m3"] for i in unique),
        "overlapping_component_bounds_requiring_union_audit": ambiguous,
        "volume_qualified": not ambiguous and all(
            components[i]["volume_m3"] > 1e-15 for i in unique),
    }


def allocate_masses(volumes, total, floor):
    if not volumes or not np.isfinite([total, floor, *volumes.values()]).all():
        raise ValueError("Nonfinite or missing mass inputs")
    if total <= 0 or floor <= 0 or any(v < 0 for v in volumes.values()) or len(volumes)*floor > total:
        raise ValueError("Impossible mass or floor constraints")
    free, masses, remaining = set(volumes), {}, total
    while free:
        weight = sum(volumes[name] for name in sorted(free))
        if weight <= 0:
            if abs(remaining-len(free)*floor) > 1e-10:
                raise ValueError("Cannot distribute residual mass with no source volume")
            masses.update({name: floor for name in free})
            break
        below = {name for name in free if remaining*volumes[name]/weight < floor}
        if not below:
            masses.update({name: remaining*volumes[name]/weight for name in free})
            break
        masses.update({name: floor for name in below})
        remaining -= floor*len(below)
        free -= below
    return {name: masses[name] for name in sorted(masses)}


def source_audit(source, config, tolerance):
    if source["source_sha256"] != config["source_sha256"]:
        raise ValueError("Source/config mismatch")
    volumes = {m["owner"]: 0. for m in source["meshes"] if m["category"] == "robot"}
    # Explicit link inventory can retain empty links; no asset-specific node correction.
    for link in config.get("articulation_links", []):
        volumes.setdefault(link, 0.)
    rows, unresolved = [], []
    for mesh in source["meshes"]:
        if mesh["category"] != "robot":
            continue
        if not mesh["source_manifold"]:
            rows.append({"node": mesh["node"], "owner": mesh["owner"],
                         "status": "excluded_nonmanifold_source"})
            continue
        try:
            row = mesh_volume(mesh["vertices"], mesh["faces"], tolerance)
        except ValueError as error:
            row = {"volume_qualified": False, "reason": str(error)}
        row.update(node=mesh["node"], owner=mesh["owner"])
        if row["volume_qualified"]:
            volumes[mesh["owner"]] += row["volume_m3"]
        else:
            unresolved.append(mesh["node"])
        rows.append(row)
    mass = config["mass_model"]
    return {
        "schema_version": 1, "source_sha256": source["source_sha256"],
        "duplicate_tolerance_m": tolerance,
        "method": "Consistently oriented closed components; deduplicate bijective coincident triangle meshes.",
        "cross_part_volume_union_measured": False,
        "mass_is_an_estimate": True,
        "rows": rows, "unresolved_nodes": unresolved,
        "per_link_closed_visual_volume_m3": volumes,
        "proposed_masses_kg": allocate_masses(volumes, mass["total_mass_kg"],
                                             mass["minimum_link_mass_kg"]) if not unresolved else None,
        "physics_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-geometry", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--snapshot", type=Path, help="Include inferred links with no assigned source mesh")
    parser.add_argument("--duplicate-tolerance-m", type=float, default=1e-6)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    if not 0 < args.duplicate_tolerance_m <= 1e-5:
        raise ValueError("Duplicate tolerance must be in (0, 10 micrometers]")
    config = json.loads(args.config.read_text())
    if args.snapshot:
        config["articulation_links"] = [
            b["path"].rsplit("/", 1)[-1] for b in json.loads(args.snapshot.read_text())["bodies"]]
    result = source_audit(json.loads(args.source_geometry.read_text()), config,
                          args.duplicate_tolerance_m)
    result["evidence_sha256"] = {name: hashlib.sha256(path.read_bytes()).hexdigest()
                                for name, path in vars(args).items() if isinstance(path, Path) and name != "out"}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, allow_nan=False)+"\n")
    print(json.dumps({"report": str(args.out), "unresolved_nodes": result["unresolved_nodes"],
                      "proposed_masses_kg": result["proposed_masses_kg"]}))


if __name__ == "__main__":
    main()
