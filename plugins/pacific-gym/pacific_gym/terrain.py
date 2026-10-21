"""Hash-bound local triangle queries and conservative foot support planes.

These are planning observations. A near-surface source vertex is not a native
contact, and a planned support polygon does not establish dynamic balance.
"""

import hashlib
import json

import numpy as np
from scipy.spatial import ConvexHull, QhullError


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def area(polygon):
    p = np.asarray(polygon)
    if len(p) < 3:
        return 0.
    return float(abs(np.dot(p[:, 0], np.roll(p[:, 1], -1))
                     - np.dot(p[:, 1], np.roll(p[:, 0], -1)))/2)


def clip_polygon(polygon, triangle):
    """Intersect two convex XY polygons using the CCW triangle half-planes."""
    output = np.asarray(polygon, dtype=float)
    for a, b in zip(triangle, np.roll(triangle, -1, axis=0)):
        if not len(output):
            break
        edge = b-a
        def distance(point):
            d = point-a
            return edge[0]*d[1]-edge[1]*d[0]
        clipped = []
        previous = output[-1]
        previous_distance = distance(previous)
        for current in output:
            current_distance = distance(current)
            inside, previous_inside = current_distance >= -1e-12, previous_distance >= -1e-12
            if inside != previous_inside:
                fraction = previous_distance/(previous_distance-current_distance)
                clipped.append(previous+fraction*(current-previous))
            if inside:
                clipped.append(current)
            previous, previous_distance = current, current_distance
        output = np.asarray(clipped, dtype=float).reshape(-1, 2)
    return output


class TriangleTerrain:
    def __init__(self, snapshot, source):
        meshes = [m for m in source["meshes"] if m["category"] == "terrain"]
        if len(meshes) != 1:
            raise ValueError("Require one explicit terrain mesh")
        mesh = meshes[0]
        geometry = {"points": mesh["vertices"], "counts": [len(f) for f in mesh["faces"]],
                    "indices": [i for face in mesh["faces"] for i in face]}
        self.geometry_sha256 = digest(geometry)
        matches = [m for m in snapshot["meshes"] if m["collision"]
                   and m["world_geometry_sha256"] == self.geometry_sha256
                   and m["path"].startswith("/World/Environment/")]
        if len(matches) != 1 or any(len(f) != 3 for f in mesh["faces"]):
            raise ValueError("Source terrain must exactly match one candidate triangle collider")
        self.path = matches[0]["path"]
        self.candidate_sha256 = snapshot["usd_sha256"]
        self.vertices = np.asarray(mesh["vertices"], dtype=float)
        self.faces = np.asarray(mesh["faces"], dtype=int)
        self.triangles = self.vertices[self.faces]
        normals = np.cross(self.triangles[:, 1]-self.triangles[:, 0],
                           self.triangles[:, 2]-self.triangles[:, 0])
        lengths = np.linalg.norm(normals, axis=1)
        if np.any(lengths <= 1e-12):
            raise ValueError("Degenerate terrain triangle")
        self.normals = normals/lengths[:, None]
        if np.any(self.normals[:, 2] <= .5):
            raise ValueError("Terrain must be an upward-facing height surface")
        self.offsets = np.einsum("ij,ij->i", self.normals, self.triangles[:, 0])
        basis = np.stack((self.triangles[:, 1, :2]-self.triangles[:, 0, :2],
                          self.triangles[:, 2, :2]-self.triangles[:, 0, :2]), axis=2)
        self.inverse_xy = np.linalg.inv(basis)
        # Area-sum coverage certificates require disjoint triangle interiors.
        for i, triangle in enumerate(self.triangles):
            low, high = triangle[:, :2].min(0), triangle[:, :2].max(0)
            for other in self.triangles[i+1:]:
                if np.any(np.minimum(high, other[:, :2].max(0))
                          - np.maximum(low, other[:, :2].min(0)) <= 1e-12):
                    continue
                if area(clip_polygon(triangle[:, :2], other[:, :2])) > 1e-10:
                    raise ValueError("Overlapping terrain interiors cannot use area-sum coverage")

    def query(self, xy):
        xy = np.atleast_2d(np.asarray(xy, dtype=float))
        if not np.isfinite(xy).all():
            raise ValueError("Nonfinite terrain query")
        uv = np.einsum("tij,ntj->nti", self.inverse_xy,
                       xy[:, None, :]-self.triangles[None, :, 0, :2])
        inside = (uv[:, :, 0] >= -1e-9) & (uv[:, :, 1] >= -1e-9) & (uv.sum(2) <= 1+1e-9)
        if np.any(~inside.any(1)):
            raise ValueError("Foot query leaves the authored terrain")
        heights = (self.offsets[None, :]-xy@self.normals[:, :2].T)/self.normals[None, :, 2]
        heights = np.where(inside, heights, -np.inf)
        highest = heights.max(1)
        triangle_ids = [np.flatnonzero(inside[i] & (abs(heights[i]-highest[i]) < 1e-8)).tolist()
                        for i in range(len(xy))]
        return highest, triangle_ids

    def support_certificate(self, normal, offset, polygon):
        """Prove a plane is above every triangle over a convex XY footprint."""
        polygon = np.asarray(polygon, dtype=float)
        normal = np.asarray(normal, dtype=float)
        intersections = []
        covered_area = 0.
        minimum_gap = float("inf")
        for i, triangle in enumerate(self.triangles):
            clipped = clip_polygon(polygon, triangle[:, :2])
            clipped_area = area(clipped)
            if clipped_area <= 1e-14:
                continue
            terrain_z = (self.offsets[i]-clipped@self.normals[i, :2])/self.normals[i, 2]
            plane_z = (offset-clipped@normal[:2])/normal[2]
            minimum_gap = min(minimum_gap, float((plane_z-terrain_z).min()))
            covered_area += clipped_area
            intersections.append({"triangle_id": i, "vertices_xy_m": clipped.tolist(),
                                  "terrain_z_m": terrain_z.tolist(), "plane_z_m": plane_z.tolist()})
        footprint_area = area(polygon)
        covered = footprint_area > 1e-12 and abs(covered_area-footprint_area) <= max(1e-10, footprint_area*1e-8)
        return {"covers_footprint": covered, "plane_above_terrain": covered and minimum_gap >= -1e-9,
                "footprint_area_m2": footprint_area, "covered_area_m2": covered_area,
                "minimum_plane_minus_terrain_m": minimum_gap if intersections else None,
                "intersections": intersections}

    def place_foot(self, local_vertices, xy, contact_band=.002, preferred_normal=None,
                   support_normals_local=None, preferred_support_normal_local=None,
                   heading_deg=0.):
        vertices = np.asarray(local_vertices, dtype=float)
        xy = np.asarray(xy, dtype=float)
        if vertices.ndim != 2 or vertices.shape[1] != 3 or xy.shape != (2,):
            raise ValueError("Invalid foot geometry or position")
        if not np.isfinite(heading_deg) or abs(heading_deg)>180:
            raise ValueError("Foot heading must be finite and within 180 degrees")
        heading = np.array([np.cos(np.radians(heading_deg)),
                            np.sin(np.radians(heading_deg)), 0.])
        local_normals = ([np.array([0., 0., 1.])] if support_normals_local is None
                         else np.asarray(support_normals_local, dtype=float))
        if (not len(local_normals) or any(
                not np.isfinite(n).all() or abs(np.linalg.norm(n)-1)>1e-8 or n[2]<.99
                for n in local_normals)):
            raise ValueError("Support normals must be unit vectors near the sole direction")
        candidates, seen = [], set()
        for triangle_id, normal, offset, sole_normal in (
                (i, n, d, sn) for i, (n, d) in enumerate(zip(self.normals, self.offsets))
                for sn in local_normals):
            key = tuple(np.round(np.r_[normal, offset, sole_normal], 12))
            if key in seen:
                continue
            seen.add(key)
            xaxis = heading-np.dot(heading,normal)*normal
            xaxis /= np.linalg.norm(xaxis)
            terrain_frame = np.column_stack((xaxis, np.cross(normal, xaxis), normal))
            sole_xaxis = np.array([1., 0., 0.])-sole_normal[0]*sole_normal
            sole_xaxis /= np.linalg.norm(sole_xaxis)
            sole_frame = np.column_stack((sole_xaxis, np.cross(sole_normal, sole_xaxis), sole_normal))
            rotation = terrain_frame@sole_frame.T
            rotated = vertices@rotation.T
            origin = np.r_[xy, (offset-xy@normal[:2]-np.min(rotated@normal))/normal[2]]
            world = rotated+origin
            footprint = world[ConvexHull(world[:, :2]).vertices, :2]
            certificate = self.support_certificate(normal, offset, footprint)
            if not certificate["plane_above_terrain"]:
                continue
            heights, ids = self.query(world[:, :2])
            gaps = world[:, 2]-heights
            if gaps.min() < -1e-8:
                raise ValueError("Support-plane proof and terrain queries disagree")
            contact_indices = np.flatnonzero(gaps <= contact_band)
            if len(contact_indices) < 3:
                continue
            try:
                support = ConvexHull(world[contact_indices, :2])
            except QhullError:
                continue
            selected_indices = contact_indices[support.vertices]
            matrix = np.eye(4)
            matrix[:3, :3], matrix[:3, 3] = rotation, origin
            record = {
                "kind": "local_triangle_foot_placement",
                "candidate_sha256": self.candidate_sha256,
                "terrain_path": self.path,
                "terrain_world_geometry_sha256": self.geometry_sha256,
                "foot_local_vertices_sha256": digest(vertices.tolist()),
                "supporting_triangle_id": triangle_id,
                "plane_normal": normal.tolist(), "plane_offset_m": float(offset),
                "sole_support_normal_local": sole_normal.tolist(),
                "heading_deg": float(heading_deg),
                "world_matrix": matrix.tolist(),
                "near_surface_band_m": contact_band,
                "support_polygon_xy_m": world[selected_indices, :2].tolist(),
                "support_polygon_area_m2": float(support.volume),
                "source_vertex_minimum_vertical_gap_m": float(gaps.min()),
                "support_vertex_queries": [
                    {"source_vertex_index": int(i), "world_vertex_m": world[i].tolist(),
                     "terrain_height_m": float(heights[i]), "triangle_ids": ids[i]}
                    for i in selected_indices],
                "plane_certificate": certificate,
                "native_contact_evidence": False,
            }
            candidates.append(record)
        if not candidates:
            raise ValueError("No supported local terrain foot placement")
        preferred = [] if preferred_normal is None else [
            c for c in candidates if np.allclose(c["plane_normal"], preferred_normal, atol=1e-10, rtol=0)]
        if preferred_support_normal_local is not None:
            same_sole = [c for c in preferred or candidates if np.allclose(
                c["sole_support_normal_local"], preferred_support_normal_local, atol=1e-10, rtol=0)]
            preferred = same_sole or preferred
        selected = max(preferred or candidates, key=lambda c: (c["support_polygon_area_m2"],
                                                              c["plane_normal"][2],
                                                              -c["supporting_triangle_id"]))
        selected["selection_policy"] = ("preserve_qualified_previous_plane" if preferred
                                        else "largest_qualified_support_patch")
        selected["preferred_normal"] = None if preferred_normal is None else list(map(float, preferred_normal))
        selected["qualified_candidates"] = [
            {"triangle_id": c["supporting_triangle_id"], "normal": c["plane_normal"],
             "sole_support_normal_local": c["sole_support_normal_local"],
             "support_polygon_area_m2": c["support_polygon_area_m2"]} for c in candidates]
        selected["query_sha256"] = digest(selected)
        return selected

    def place_cooked_foot(self, local_vertices, xy, cooked_sha256, preferred_normal=None,
                          preferred_support_normal_local=None, heading_deg=0.,
                          restrict_to_preferred_sole=False):
        """Use actual cooked vertices on a supporting plane, with a 0.1 µm tolerance.

        The support polygon joins actual potential contact points. It does not
        invent collision faces between parts, or claim measured native contact.
        """
        vertices = np.unique(np.asarray(local_vertices, dtype=float), axis=0)
        hull = ConvexHull(vertices)
        normals, seen = [], set()
        for equation in hull.equations:
            normal = -equation[:3]
            if normal[2] < np.cos(np.radians(5)):
                continue
            key = tuple(np.round(normal, 12))
            if key not in seen:
                seen.add(key)
                normals.append(normal)
        if restrict_to_preferred_sole:
            if preferred_support_normal_local is None:
                raise ValueError("An explicit sole normal is required for a restricted placement")
            normals = [n for n in normals if np.allclose(
                n, preferred_support_normal_local, atol=1e-10, rtol=0)]
            if not normals:
                raise ValueError("Requested sole is not a cooked convex support plane")
        record = self.place_foot(vertices, xy, contact_band=1e-7,
                                 preferred_normal=preferred_normal,
                                 support_normals_local=normals,
                                 preferred_support_normal_local=preferred_support_normal_local,
                                 heading_deg=heading_deg)
        record.pop("query_sha256")
        record.update(kind="native_cooked_geometry_potential_contact_placement",
                      cooked_sha256=cooked_sha256,
                      support_geometry_note="Convex support polygon of actual cooked vertices within 0.1 micrometre of local terrain; not measured contacts")
        record["query_sha256"] = digest(record)
        return record
