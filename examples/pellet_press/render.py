"""Render the exported GLBs, then typeset a single two-view review sheet."""

from pathlib import Path
from tempfile import TemporaryDirectory

import bpy
import numpy as np
from mathutils import Vector
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parent
WIDTH, HEIGHT = 990, 700


def render(model, destination):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(model))
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 64
    scene.cycles.use_denoising = False
    scene.cycles.max_bounces = 5
    scene.cycles.transparent_max_bounces = 2
    scene.render.film_transparent = True
    scene.render.resolution_x = WIDTH
    scene.render.resolution_y = HEIGHT
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.world = bpy.data.worlds.new("studio")
    scene.world.use_nodes = True
    background = scene.world.node_tree.nodes.get("Background")
    background.inputs["Color"].default_value = (0.84, 0.90, 1, 1)
    background.inputs["Strength"].default_value = 0.5
    scene.view_settings.view_transform = "AgX"

    # Measure actual imported vertices. Loose per-part boxes can considerably
    # oversize the camera around the 141-degree service door.
    objects = [obj for obj in bpy.context.selected_objects if obj.type == "MESH"]
    corners = [obj.matrix_world @ v.co for obj in objects for v in obj.data.vertices]
    rotation = Vector((3.15, 5.6, -2.8)).to_track_quat("-Z", "Y")
    local = [rotation.inverted() @ p for p in corners]
    low = Vector([min(p[k] for p in local) for k in range(3)])
    high = Vector([max(p[k] for p in local) for k in range(3)])
    target = rotation @ ((low + high) * 0.5)
    camera = bpy.data.cameras.new("orthographic inspection")
    camera.type = "ORTHO"
    camera.ortho_scale = max(high.x - low.x, (high.y - low.y) * WIDTH / HEIGHT) * 1.135
    cam = bpy.data.objects.new("camera", camera)
    scene.collection.objects.link(cam)
    cam.rotation_euler = rotation.to_euler()
    cam.location = target + rotation @ Vector((0, 0, 8))
    scene.camera = cam
    lowest = min(p.z for p in corners)
    bpy.ops.mesh.primitive_plane_add(size=80, location=(0, 0, lowest - 0.015))
    bpy.context.object.is_shadow_catcher = True

    def softbox(name, location, power, size, colour):
        light = bpy.data.lights.new(name, "AREA")
        light.energy = power
        light.shape = "DISK"
        light.size = size
        light.color = colour
        obj = bpy.data.objects.new(name, light)
        scene.collection.objects.link(obj)
        obj.location = location
        obj.rotation_euler = (target - obj.location).to_track_quat("-Z", "Y").to_euler()

    softbox("left softbox", (-3.4, -4.3, 6), 620, 4.2, (1, 0.93, 0.84))
    softbox("right bounce", (4, -1.7, 3.5), 470, 3.3, (0.84, 0.93, 1))
    softbox("strip highlight", (1.4, 3, 5), 750, 3, (1, 1, 1))
    scene.render.filepath = str(destination)
    bpy.ops.render.render(write_still=True)
    print(
        f"Rendered {model.name}: {len(objects)} mesh nodes, scale {camera.ortho_scale:.3f} m."
    )


def font(size, bold=False):
    try:
        return ImageFont.truetype(
            "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", size
        )
    except OSError:
        return ImageFont.load_default(size)


def make_sheet(paths):
    bg = (244, 246, 244)
    sheet = Image.new("RGB", (2 * WIDTH + 60, HEIGHT + 212), bg)
    draw = ImageDraw.Draw(sheet)
    ink, muted = (28, 52, 53), (74, 92, 92)
    draw.text((52, 32), "Ring-die pellet press", font=font(47, True), fill=ink)
    draw.text(
        (54, 98),
        "Engineering concept   /   Service access and assembly arrangement",
        font=font(22),
        fill=muted,
    )
    draw.line((52, 145, sheet.width - 52, 145), fill=(160, 180, 176), width=2)
    for ix, (path, caption) in enumerate(
        zip(paths, ("Service-open assembly", "Exploded arrangement"))
    ):
        tile = Image.open(path).convert("RGBA").filter(ImageFilter.MedianFilter(3))
        # Fade just the outer studio shadow into the shared sheet background.
        x = np.minimum(np.arange(WIDTH), np.arange(WIDTH)[::-1]) / (WIDTH * 0.065)
        y = np.minimum(np.arange(HEIGHT), np.arange(HEIGHT)[::-1]) / (HEIGHT * 0.075)
        weights = np.clip(np.minimum(x[None, :], y[:, None]), 0, 1)
        mask = Image.fromarray((weights * 255).astype(np.uint8))
        tile.putalpha(ImageChops.multiply(tile.getchannel("A"), mask))
        x = ix * (WIDTH + 20) + 20
        sheet.paste(tile, (x, 150), tile.getchannel("A"))
        draw.text((x + 33, HEIGHT + 153), caption, font=font(24, True), fill=ink)
    sheet.quantize(colors=256, dither=Image.Dither.NONE).save(
        OUT / "preview.png", optimize=True
    )


with TemporaryDirectory() as temp:
    temp = Path(temp)
    paths = []
    for name in ("assembled", "exploded"):
        paths.append(temp / f"{name}.png")
        render(OUT / f"{name}.glb", paths[-1])
    make_sheet(paths)
