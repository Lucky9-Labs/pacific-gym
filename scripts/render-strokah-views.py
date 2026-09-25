"""Render neutral, unposed views of a verified Strokah GLB in Blender.

Usage: blender --background --python scripts/render-strokah-views.py -- GLB OUT_DIR
"""

import hashlib
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


args = sys.argv[sys.argv.index("--") + 1:]
source, output = Path(args[0]).resolve(), Path(args[1]).resolve()
output.mkdir(parents=True, exist_ok=True)
before = sha256(source)

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=str(source))
render_exclusions = {"Terrain_Walk_Test", "Icosphere"}
for obj in bpy.context.scene.objects:
    if obj.name in render_exclusions:
        obj.hide_render = True
meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH" and not obj.hide_render]
if not meshes:
    raise RuntimeError("GLB contains no renderable meshes")
corners = [obj.matrix_world @ Vector(corner) for obj in meshes for corner in obj.bound_box]
low = Vector([min(p[i] for p in corners) for i in range(3)])
high = Vector([max(p[i] for p in corners) for i in range(3)])
center = (low + high) / 2
span = high - low
size = max(span)

# A neutral presentation floor makes the planted foot plane readable. This is
# render-only scene geometry; it never touches or exports the source GLB.
bpy.ops.mesh.primitive_plane_add(size=size * 5, location=(center.x, center.y, low.z - size * 0.005))
floor = bpy.context.object
floor.name = "Render-only contact floor"
floor_material = bpy.data.materials.new("Matte gray floor")
floor_material.diffuse_color = (0.04, 0.05, 0.06, 1)
floor_material.use_nodes = True
floor_material.node_tree.nodes.get("Principled BSDF").inputs["Base Color"].default_value = (0.025, 0.03, 0.04, 1)
floor_material.node_tree.nodes.get("Principled BSDF").inputs["Roughness"].default_value = 0.92
floor.data.materials.append(floor_material)

scene = bpy.context.scene
scene.render.engine = "BLENDER_EEVEE"
scene.render.resolution_x = 768
scene.render.resolution_y = 768
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.render.film_transparent = False
scene.world = bpy.data.worlds.new("Neutral backdrop")
scene.world.color = (0.13, 0.13, 0.13)
scene.view_settings.view_transform = "AgX"

def area(name, location, power, width):
    data = bpy.data.lights.new(name, "AREA")
    data.energy = power
    data.shape = "DISK"
    data.size = width
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)
    obj.location = center + Vector(location) * size
    obj.rotation_euler = (center - obj.location).to_track_quat("-Z", "Y").to_euler()


area("key", (1.6, -2.1, 2.0), 850, 4)
area("fill", (-1.8, -0.7, 1.0), 500, 4)
area("rim", (0.4, 2.1, 1.5), 1000, 3)
camera_data = bpy.data.cameras.new("Reference camera")
camera_data.type = "ORTHO"
camera_data.ortho_scale = size * 1.55
camera = bpy.data.objects.new("Reference camera", camera_data)
scene.collection.objects.link(camera)
scene.camera = camera

# Cardinal headings are names in GLB coordinates; selection of the walk start
# view is made only after visual inspection of these renders.
views = {
    "negative-y": (0, -2.8, 1.0),
    "positive-y": (0, 2.8, 1.0),
    "negative-x": (-2.8, 0, 1.0),
    "positive-x": (2.8, 0, 1.0),
    "three-quarter": (1.9, -2.2, 1.3),
}
files = {}
for name, offset in views.items():
    camera.location = center + Vector(offset) * size
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
    path = output / f"{name}.png"
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    files[name] = {"path": path.name, "sha256": sha256(path), "bytes": path.stat().st_size}

if sha256(source) != before:
    raise RuntimeError("Source GLB bytes changed during rendering")
(output / "render.json").write_text(json.dumps({
    "source_sha256": before,
    "blender_version": bpy.app.version_string,
    "render_only_exclusions": sorted(render_exclusions.intersection({obj.name for obj in bpy.context.scene.objects})),
    "bounding_box": {"min": list(low), "max": list(high)},
    "views": files,
}, indent=2) + "\n")
print("STROKAH_RENDER_PASS", output)
