"""Render a controlled, static foot-contact regression with Blender."""

import argparse
import json
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def material(name, color):
    result = bpy.data.materials.new(name)
    result.diffuse_color = (*color, 1)
    result.use_nodes = True
    result.node_tree.nodes.get("Principled BSDF").inputs["Base Color"].default_value = (*color, 1)
    return result


def cube(name, location, scale, color):
    bpy.ops.mesh.primitive_cube_add(size=1, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = scale
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(color)
    bevel = obj.modifiers.new("soft edges", "BEVEL")
    bevel.width = 0.055
    bevel.segments = 2
    obj.modifiers.new("weighted normals", "WEIGHTED_NORMAL")
    return obj


def beam(name, start, end, radius, color):
    midpoint = (Vector(start) + Vector(end)) / 2
    delta = Vector(end) - Vector(start)
    bpy.ops.mesh.primitive_cylinder_add(vertices=12, radius=radius, depth=delta.length, location=midpoint)
    obj = bpy.context.object
    obj.name = name
    obj.rotation_euler = delta.to_track_quat("Z", "Y").to_euler()
    obj.data.materials.append(color)


def label(text, location, size, color):
    curve = bpy.data.curves.new(text, "FONT")
    curve.body = text
    curve.size = size
    curve.extrude = 0.002
    obj = bpy.data.objects.new(text, curve)
    bpy.context.collection.objects.link(obj)
    obj.location = location
    obj.data.materials.append(color)


def render(path, left_foot_x):
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    metal = material("dark metal", (0.08, 0.15, 0.21))
    orange = material("left foot orange", (0.95, 0.31, 0.045))
    blue = material("right foot blue", (0.045, 0.43, 0.85))
    floor = material("warm floor", (0.45, 0.47, 0.43))
    white = material("white labels", (0.85, 0.87, 0.82))

    cube("ground", (0, 0, -0.08), (5, 2.6, 0.12), floor)
    for x in (-1.5, -1, -0.5, 0, 0.5, 1, 1.5):
        cube(f"contact grid x={x}", (x, 0, -0.014), (0.013, 2.3, 0.006), white)
    cube("torso", (0, 0, 1.6), (0.9, 0.65, 0.7), metal)
    cube("head", (0.16, 0, 2.16), (0.5, 0.52, 0.42), metal)
    for side, y, x, accent in (("left", -0.42, left_foot_x, orange), ("right", 0.42, -0.25, blue)):
        hip = (0, y, 1.34)
        knee = (x * 0.45 - 0.12, y, 0.68)
        ankle = (x, y, 0.19)
        beam(f"{side} thigh", hip, knee, 0.105, metal)
        beam(f"{side} shin", knee, ankle, 0.085, metal)
        cube(f"{side} foot contact", (x + 0.08, y, 0.095), (0.55, 0.28, 0.18), accent)
    label("FORWARD  >", (-1.7, -1.04, 0.01), 0.26, white)
    label("L", (left_foot_x + 0.04, -0.66, 0.01), 0.20, orange)

    bpy.ops.object.camera_add(location=(0, -8.0, 2.6))
    camera = bpy.context.object
    camera.rotation_euler = (Vector((0, 0, 1.12)) - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 4.7
    bpy.context.scene.camera = camera
    bpy.ops.object.light_add(type="AREA", location=(0, -3, 5))
    bpy.context.object.data.energy = 900
    bpy.context.object.data.shape = "DISK"
    bpy.context.object.data.size = 5
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 24
    scene.render.resolution_x = 900
    scene.render.resolution_y = 620
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(path)
    scene.world.color = (0.35, 0.35, 0.35)
    bpy.ops.render.render(write_still=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    args.out.mkdir(parents=True, exist_ok=True)
    reference = args.out / "reference.png"
    candidate = args.out / "candidate.png"
    render(reference, 0.55)
    render(candidate, -0.55)
    (args.out / "fixture.json").write_text(json.dumps({
        "fixture": "synthetic static contact pose",
        "forward_axis": "+X",
        "reference_left_foot_x": 0.55,
        "candidate_left_foot_x": -0.55,
        "right_foot_x": -0.25,
        "expected_steering": "move the left foot contact forward",
        "render_size": [900, 620],
    }, indent=2) + "\n")


if __name__ == "__main__":
    main()
