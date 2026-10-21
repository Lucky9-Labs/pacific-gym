"""Strict GLB triangle decoding for rigid, unskinned part assemblies."""

import json
import struct
from pathlib import Path

import numpy as np


def read_rigid_glb(path):
    raw = Path(path).read_bytes()
    if len(raw) < 20 or struct.unpack_from("<4sII", raw) != (b"glTF", 2, len(raw)):
        raise ValueError("Expected a complete GLB 2.0")
    chunks, offset = {}, 12
    while offset < len(raw):
        if offset + 8 > len(raw):
            raise ValueError("Truncated GLB chunk header")
        length, kind = struct.unpack_from("<II", raw, offset)
        offset += 8
        if length % 4 or offset + length > len(raw) or kind in chunks:
            raise ValueError("Invalid or duplicate GLB chunk")
        chunks[kind] = raw[offset:offset+length]
        offset += length
    doc = json.loads(chunks[0x4E4F534A])
    binary = chunks[0x004E4942]
    if len(doc["buffers"]) != 1 or "uri" in doc["buffers"][0]:
        raise ValueError("Only self-contained GLB buffers are supported")
    if len(binary) < doc["buffers"][0]["byteLength"]:
        raise ValueError("Truncated GLB binary buffer")
    types = {5121: "<u1", 5123: "<u2", 5125: "<u4", 5126: "<f4"}
    widths = {"SCALAR": 1, "VEC3": 3}

    def accessor(index):
        a = doc["accessors"][index]
        if "sparse" in a or a.get("normalized") or "bufferView" not in a:
            raise ValueError("Sparse, normalized or implicit accessors are unsupported")
        view = doc["bufferViews"][a["bufferView"]]
        if view.get("buffer", 0) != 0:
            raise ValueError("Accessor uses an external buffer")
        dtype, width = np.dtype(types[a["componentType"]]), widths[a["type"]]
        stride = view.get("byteStride", dtype.itemsize*width)
        base = view.get("byteOffset", 0) + a.get("byteOffset", 0)
        end = base + (a["count"]-1)*stride + dtype.itemsize*width
        view_end = view.get("byteOffset", 0) + view["byteLength"]
        if a["count"] <= 0 or stride < dtype.itemsize*width or base < 0 or end > min(view_end, len(binary)):
            raise ValueError("Accessor lies outside its buffer view")
        return np.ndarray((a["count"], width), dtype=dtype, buffer=binary,
                          offset=base, strides=(stride, dtype.itemsize)).copy()

    nodes = doc["nodes"]
    parents = {}
    for index, node in enumerate(nodes):
        for child in node.get("children", []):
            if child in parents or not 0 <= child < len(nodes):
                raise ValueError("Invalid or multiply parented node")
            parents[child] = index
    matrices = {}

    def world(index, visiting=None):
        if index in matrices:
            return matrices[index]
        visiting = set() if visiting is None else set(visiting)
        if index in visiting:
            raise ValueError("Cyclic GLB hierarchy")
        visiting.add(index)
        node = nodes[index]
        if "matrix" in node:
            local = np.asarray(node["matrix"], float).reshape(4, 4).T
        else:
            x, y, z, w = node.get("rotation", [0, 0, 0, 1])
            if abs(np.linalg.norm([w, x, y, z])-1) > 1e-5:
                raise ValueError("GLB node has an invalid rotation")
            local = np.eye(4)
            local[:3, :3] = np.array([
                [1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)],
            ]) @ np.diag(node.get("scale", [1, 1, 1]))
            local[:3, 3] = node.get("translation", [0, 0, 0])
        matrices[index] = world(parents[index], visiting) @ local if index in parents else local
        if not np.isfinite(matrices[index]).all():
            raise ValueError("Nonfinite GLB transform")
        return matrices[index]

    converted = []
    conversion = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], float)
    for index, node in enumerate(nodes):
        world(index)
        if "mesh" not in node:
            continue
        if "skin" in node:
            raise ValueError("Skinned meshes require skin evaluation")
        vertices, faces = [], []
        count = 0
        for primitive in doc["meshes"][node["mesh"]]["primitives"]:
            if primitive.get("mode", 4) != 4 or any(
                    a.startswith(("JOINTS_", "WEIGHTS_")) for a in primitive["attributes"]):
                raise ValueError("Only rigid triangle primitives are supported")
            if primitive.get("targets"):
                raise ValueError("Morph targets require explicit evaluation")
            points = accessor(primitive["attributes"]["POSITION"]).astype(float)
            if points.shape[1] != 3:
                raise ValueError("Expected VEC3 positions")
            indices = accessor(primitive["indices"]).ravel() if "indices" in primitive else np.arange(len(points))
            if indices.dtype.kind not in "ui" or len(indices) % 3 or np.any(indices >= len(points)):
                raise ValueError("Invalid triangle indices")
            vertices.append(points)
            faces.append(indices.reshape(-1, 3).astype(np.int64) + count)
            count += len(points)
        matrix = matrices[index]
        points = np.concatenate(vertices) @ matrix[:3, :3].T + matrix[:3, 3]
        if not np.isfinite(points).all():
            raise ValueError("Nonfinite mesh positions")
        converted.append({"node": index, "name": node.get("name"),
                          "vertices": points @ conversion.T,
                          "faces": np.concatenate(faces)})
    return doc, matrices, converted
