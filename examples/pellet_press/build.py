"""Build an illustrative service-open pellet press in Blender. Coordinates are metres."""

import math
from functools import lru_cache
from pathlib import Path

import bmesh
import bpy
from mathutils import Euler, Matrix, Vector

OUT = Path(__file__).resolve().parent
TAU = 2 * math.pi
bpy.ops.wm.read_factory_settings(use_empty=True)


def material(name, colour, metal, rough):
    """Convert an sRGB paint swatch to Blender's linear RGB."""
    rgb = [int(colour[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*linear, 1)
    bsdf.inputs["Metallic"].default_value = metal
    bsdf.inputs["Roughness"].default_value = rough
    mat.diffuse_color = (*linear, 1)
    return mat


SKID = material("Graphite powder coat", "394547", 0.30, 0.34)
CAST = material("Pale mineral cast finish", "DCE3DE", 0.26, 0.35)
TEAL = material("Deep petrol enamel", "287270", 0.29, 0.30)
RIB = material("Cooling fin enamel", "398A85", 0.34, 0.31)
STEEL = material("Machined stainless steel", "C5CDCD", 0.77, 0.29)
DARK = material("Blackened steel", "333D41", 0.49, 0.37)
BRONZE = material("Oil bronze", "C39C66", 0.66, 0.33)
ORANGE = material("Coupling guard oxide enamel", "C9793F", 0.30, 0.36)
SHEET = material("Satin sheet metal", "B8C7C6", 0.58, 0.37)
GASKET = material("Nitrile rubber", "202829", 0.03, 0.62)


@lru_cache(maxsize=128)
def box_data(dimensions, radius):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1)
    for v in bm.verts:
        v.co = tuple(a * b for a, b in zip(v.co, dimensions))
    if radius > 0:
        bmesh.ops.bevel(
            bm,
            geom=list(bm.edges),
            offset=min(radius, min(dimensions) * 0.23),
            segments=3,
            affect="EDGES",
        )
    bm.verts.index_update()
    vertices = [tuple(v.co) for v in bm.verts]
    faces = [tuple(v.index for v in face.verts) for face in bm.faces]
    bm.free()
    return vertices, faces


class Part:
    def __init__(self, name, offset, transform=None):
        self.name, self.offset = name, offset
        self.transform = transform or Matrix.Identity(4)
        self.verts, self.faces, self.materials, self.face_materials, self.smooth = (
            [],
            [],
            [],
            [],
            [],
        )
        parts.append(self)

    def surface(self, points, faces, mat, smooth=False):
        start = len(self.verts)
        self.verts.extend(self.transform @ Vector(p) for p in points)
        for face in faces:
            self.faces.append(tuple(start + i for i in face))
        if mat not in self.materials:
            self.materials.append(mat)
        self.face_materials.extend([self.materials.index(mat)] * len(faces))
        self.smooth.extend([smooth] * len(faces))

    def box(self, center, dimensions, mat, bevel=0.006, angle=(0, 0, 0)):
        vs, fs = box_data(tuple(dimensions), bevel)
        rot = Euler(angle).to_matrix()
        self.surface([Vector(center) + rot @ Vector(v) for v in vs], fs, mat)

    def tube(self, a, b, outer, mat, inner=0, sides=48, end_radius=None):
        a, b = Vector(a), Vector(b)
        q = (b - a).to_track_quat("Z", "Y")

        def ring(center, r):
            return [
                center
                + q
                @ Vector(
                    (r * math.cos(TAU * i / sides), r * math.sin(TAU * i / sides), 0)
                )
                for i in range(sides)
            ]

        vs = ring(a, outer) + ring(b, outer if end_radius is None else end_radius)
        faces = [
            (i, (i + 1) % sides, sides + (i + 1) % sides, sides + i)
            for i in range(sides)
        ]
        if inner:
            vs += ring(a, inner) + ring(b, inner)
            for i in range(sides):
                j = (i + 1) % sides
                faces.extend(
                    [
                        (i, j, 2 * sides + j, 2 * sides + i),
                        (sides + i, 3 * sides + i, 3 * sides + j, sides + j),
                        (2 * sides + i, 2 * sides + j, 3 * sides + j, 3 * sides + i),
                    ]
                )
        else:
            faces.extend(
                [tuple(reversed(range(sides))), tuple(range(sides, 2 * sides))]
            )
        self.surface(vs, faces, mat)

    def drum(self, x, y, z, r, length, mat, inner=0, sides=64):
        self.tube((x, y - length / 2, z), (x, y + length / 2, z), r, mat, inner, sides)

    def finish(self):
        mesh = bpy.data.meshes.new(self.name)
        mesh.from_pydata(self.verts, [], self.faces)
        for mat in self.materials:
            mesh.materials.append(mat)
        for poly, slot, smooth in zip(mesh.polygons, self.face_materials, self.smooth):
            poly.material_index, poly.use_smooth = slot, smooth
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
        bm.to_mesh(mesh)
        bm.free()
        obj = bpy.data.objects.new(self.name, mesh)
        bpy.context.collection.objects.link(obj)
        obj["explosion_translation_m"] = (
            self.offset[0],
            self.offset[2],
            -self.offset[1],
        )  # glTF Y-up
        self.obj = obj


parts = []
CX, Z = 0.36, 0.96
frame = Part("frame", (0, 0, -0.27))
for x in (-1.12, 0.97):
    frame.box((x, -0.025, 0.125), (0.13, 1.56, 0.19), SKID)
for y in (-0.72, -0.22, 0.67):
    frame.box((-0.075, y, 0.126), (1.966, 0.12, 0.18), SKID)
for x in (-1.12, 0.97):
    for y in (-0.70, 0.65):
        frame.box((x, y, 0.035), (0.26, 0.25, 0.045), DARK)
        frame.tube((x, y, 0.052), (x, y, 0.094), 0.032, STEEL, sides=6)
for x in (-0.91, -0.53):
    frame.box((x, -0.09, 0.231), (0.13, 0.88, 0.07), STEEL)

pedestal = Part("head_pedestal", (0, 0, -0.10))
for x in (CX - 0.255, CX + 0.255):
    pedestal.box((x, 0.055, 0.39), (0.16, 0.75, 0.47), CAST, 0.016)
    pedestal.box((x, 0.06, 0.217), (0.29, 0.8, 0.052), TEAL)
    for y in (-0.22, 0.34):
        pedestal.tube(
            (x - 0.098, y, 0.237), (x - 0.098, y, 0.273), 0.030, STEEL, sides=6
        )
pedestal.box((CX, 0.065, 0.578), (0.72, 0.70, 0.11), CAST)

motor = Part("motor", (-0.48, 0.015, 0.13))
MX, MZ = -0.735, 0.552
motor.drum(MX, -0.035, MZ, 0.229, 0.69, TEAL)
for i in range(28):
    a = TAU * i / 28
    motor.box(
        (MX + 0.238 * math.cos(a), -0.02, MZ + 0.238 * math.sin(a)),
        (0.048, 0.565, 0.013),
        RIB,
        0.002,
        (0, -a, 0),
    )
for y, r, length in [(-0.385, 0.243, 0.07), (0.32, 0.24, 0.055), (0.361, 0.208, 0.047)]:
    motor.drum(MX, y, MZ, r, length, TEAL)
for x in (MX - 0.18, MX + 0.18):
    motor.box((x, -0.02, 0.309), (0.12, 0.59, 0.11), TEAL)
    for y in (-0.22, 0.2):
        motor.tube((x, y, 0.367), (x, y, 0.389), 0.024, STEEL, sides=6)
motor.box((MX, 0.04, 0.850), (0.28, 0.29, 0.115), TEAL, 0.014)
motor.box((MX, 0.04, 0.918), (0.305, 0.304, 0.028), RIB)
motor.tube((MX - 0.135, 0.06, 0.849), (MX - 0.204, 0.06, 0.849), 0.035, DARK, sides=12)

fan = Part("motor_fan_guard", (-0.56, -0.18, 0.13))
fan.drum(MX, -0.444, MZ, 0.225, 0.092, TEAL, inner=0.192)
fan.drum(MX, -0.470, MZ, 0.192, 0.010, DARK)
for radius in (0.058, 0.094, 0.13, 0.165, 0.192):
    fan.drum(MX, -0.489, MZ, radius, 0.013, STEEL, inner=radius - 0.0055)
for i in range(8):
    a = TAU * i / 8
    fan.tube(
        (MX, -0.486, MZ),
        (MX + 0.196 * math.cos(a), -0.486, MZ + 0.196 * math.sin(a)),
        0.0055,
        STEEL,
        sides=8,
    )
fan.drum(MX, -0.496, MZ, 0.047, 0.017, TEAL)

drive = Part("reduction_gearcase", (-0.12, 0.68, 0.12))
drive.box((-0.14, 0.523, 0.729), (1.58, 0.33, 0.515), TEAL, 0.07)
for x, z, r in ((MX, MZ, 0.213), (CX, Z, 0.292)):
    drive.drum(x, 0.51, z, r, 0.39, TEAL)
    drive.drum(x, 0.324, z, r * 0.89, 0.035, CAST)
    for i in range(10):
        a = TAU * i / 10
        drive.drum(
            x + r * 0.75 * math.cos(a),
            0.298,
            z + r * 0.75 * math.sin(a),
            0.018,
            0.028,
            STEEL,
            sides=6,
        )
for x in (-0.46, -0.32, -0.18, -0.04, 0.10):
    drive.box((x, 0.53, 0.74), (0.029, 0.36, 0.55), RIB, 0.008)
coupling = Part("drive_coupling", (-0.53, 0.39, 0.14))
coupling.drum(MX, 0.405, MZ, 0.122, 0.114, ORANGE)
for y in (0.36, 0.445):
    coupling.drum(MX, y, MZ, 0.13, 0.021, BRONZE)

head = Part("compression_housing", (0, 0.23, 0.05))
head.drum(CX, -0.075, Z, 0.46, 0.52, CAST, inner=0.33)
for y, radius in ((-0.337, 0.483), (0.185, 0.46)):
    head.drum(CX, y, Z, radius, 0.046, TEAL, inner=0.337)
for i in range(16):
    a = TAU * i / 16
    head.drum(
        CX + 0.448 * math.cos(a),
        -0.366,
        Z + 0.448 * math.sin(a),
        0.018,
        0.025,
        STEEL,
        sides=6,
    )
head.drum(CX, 0.185, Z, 0.338, 0.06, DARK)
shaft = Part("shaft", (0, 0.70, 0.26))
shaft.drum(CX, -0.024, Z, 0.068, 0.83, STEEL)
shaft.drum(CX, 0.105, Z, 0.129, 0.10, BRONZE)
shaft.drum(CX, -0.377, Z, 0.098, 0.055, DARK)
yoke = Part("roller_carrier", (0, -0.42, -0.06))
yoke.box((CX, -0.421, Z), (0.457, 0.075, 0.115), DARK, 0.032)
yoke.drum(CX, -0.436, Z, 0.083, 0.09, BRONZE)

die = Part("ring_die", (0, -0.51, 0.13))


# Each circular opening connects both cylindrical surfaces. Neighbouring
# rectangular patches share coordinates along each angular / axial border.
def perforated_ring(part, outer, inner, y0, y1, columns=46, rows=3, hole=0.012):
    pts, fs, index = [], [], {}

    def vertex(radius, theta, y):
        p = (CX + radius * math.cos(theta), y, Z + radius * math.sin(theta))
        key = tuple(round(c, 9) for c in p)
        if key not in index:
            index[key] = len(pts)
            pts.append(p)
        return index[key]

    da = TAU / columns
    dy = (y1 - y0) / rows
    for row in range(rows):
        for col in range(columns):
            theta = (col + 0.5) * da
            y = y0 + (row + 0.5) * dy
            boundary = []
            opening = []
            for k in range(16):
                co, si = math.cos(TAU * k / 16), math.sin(TAU * k / 16)
                edge = max(abs(co), abs(si))
                boundary.append((theta + co / edge * da / 2, y + si / edge * dy / 2))
                opening.append((theta + co * hole / outer, y + si * hole))
            for k in range(16):
                j = (k + 1) % 16
                for r in (inner, outer):
                    fs.append(
                        (
                            vertex(r, *boundary[k]),
                            vertex(r, *boundary[j]),
                            vertex(r, *opening[j]),
                            vertex(r, *opening[k]),
                        )
                    )
                fs.append(
                    (
                        vertex(outer, *opening[k]),
                        vertex(outer, *opening[j]),
                        vertex(inner, *opening[j]),
                        vertex(inner, *opening[k]),
                    )
                )
                if abs(boundary[k][1] - boundary[j][1]) < 1e-10 and (
                    abs(boundary[k][1] - y0) < 1e-10 or abs(boundary[k][1] - y1) < 1e-10
                ):
                    fs.append(
                        (
                            vertex(outer, *boundary[k]),
                            vertex(outer, *boundary[j]),
                            vertex(inner, *boundary[j]),
                            vertex(inner, *boundary[k]),
                        )
                    )
    part.surface(pts, fs, STEEL)


perforated_ring(die, 0.39, 0.287, -0.609, -0.413)
for y in (-0.613, -0.41):
    die.drum(CX, y, Z, 0.398, 0.019, STEEL, inner=0.285, sides=128)
for i in range(10):
    a = TAU * i / 10
    die.drum(
        CX + 0.345 * math.cos(a),
        -0.629,
        Z + 0.345 * math.sin(a),
        0.0135,
        0.014,
        DARK,
        sides=6,
    )


def grooved_roller(part, x):
    teeth, sections, length = 27, 7, 0.173
    n = teeth * 4
    pts, faces = [], []
    for j in range(sections):
        y = -0.622 + length * j / (sections - 1)
        for i in range(n):
            a = TAU * i / n + 0.115 * j / (sections - 1)
            r = 0.122 if i % 4 in (1, 2) else 0.115
            pts.append((x + r * math.cos(a), y, Z + r * math.sin(a)))
    for j in range(sections - 1):
        for i in range(n):
            k = (i + 1) % n
            faces.append((j * n + i, j * n + k, (j + 1) * n + k, (j + 1) * n + i))
    faces.extend([tuple(range(n)), tuple(range((sections - 1) * n, sections * n))])
    part.surface(pts, faces, DARK)
    part.drum(x, -0.628, Z, 0.113, 0.022, STEEL, inner=0.059)
    part.drum(x, -0.635, Z, 0.064, 0.026, BRONZE, inner=0.036)
    part.drum(x, -0.65, Z, 0.037, 0.036, DARK, sides=6)


for name, side in (("roller_left", -1), ("roller_right", 1)):
    roller = Part(name, (side * 0.11, -1.045, 0.18 + side * 0.095))
    grooved_roller(roller, CX + side * 0.148)

hopper = Part("feed_hopper", (-0.03, 0.06, 0.58))


def hopper_wall(part, x, y, z, upper, lower, height):
    points = []
    for zz, (w, d) in ((z, lower), (z + height, upper)):
        for sx, sy in [(-1, -1), (1, -1), (1, 1), (-1, 1)]:
            points.append((x + sx * w / 2, y + sy * d / 2, zz))
    # Two surfaces per sheet including the clearly visible interior.
    for i in range(4):
        j = (i + 1) % 4
        outer = [points[k] for k in (i, j, 4 + j, 4 + i)]
        inner = [(x + 0.979 * (v[0] - x), y + 0.979 * (v[1] - y), v[2]) for v in outer]
        part.surface(
            outer + inner,
            [
                (0, 1, 2, 3),
                (7, 6, 5, 4),
                (0, 4, 5, 1),
                (3, 2, 6, 7),
                (1, 5, 6, 2),
                (0, 3, 7, 4),
            ],
            SHEET,
        )
        part.tube(points[4 + i], points[4 + j], 0.014, STEEL, sides=12)


hopper_wall(hopper, CX, -0.025, 1.434, (0.71, 0.59), (0.22, 0.20), 0.348)
hopper.box((CX, -0.025, 1.418), (0.235, 0.214, 0.055), SHEET)
hopper.box((CX, -0.025, 1.396), (0.286, 0.268, 0.029), TEAL)
for sx in (-1, 1):
    hopper.box((CX + sx * 0.153, -0.025, 1.405), (0.028, 0.275, 0.032), DARK)

chute = Part("discharge_chute", (0.24, -0.24, -0.22))
# Sloping open U-chute guides pellets from the lower edge of the chamber.
chute.box((CX, -0.468, 0.362), (0.53, 0.54, 0.025), SHEET, 0.003, (-0.32, 0, 0))
for sx in (-1, 1):
    chute.box(
        (CX + sx * 0.261, -0.468, 0.417),
        (0.017, 0.54, 0.136),
        SHEET,
        0.003,
        (-0.32, 0, 0),
    )
chute.box((CX, -0.722, 0.276), (0.55, 0.034, 0.034), STEEL)

hinge_x, hinge_y = CX + 0.462, -0.406
hinges = Part("service_hinges", (0.13, -0.05, 0.08))
for z in (Z - 0.219, Z + 0.219):
    hinges.box((hinge_x - 0.023, hinge_y + 0.034, z), (0.109, 0.116, 0.083), TEAL)
    hinges.tube(
        (hinge_x, hinge_y, z - 0.055), (hinge_x, hinge_y, z + 0.055), 0.034, BRONZE
    )
pivot = Vector((hinge_x, hinge_y, 0))
swing = (
    Matrix.Translation(pivot)
    @ Matrix.Rotation(math.radians(141), 4, "Z")
    @ Matrix.Translation(-pivot)
)
door = Part("service_cover", (0.60, -0.25, 0.13), swing)
door.drum(CX, hinge_y, Z, 0.453, 0.082, CAST, inner=0.219)
door.drum(CX, hinge_y - 0.048, Z, 0.448, 0.030, TEAL, inner=0.416)
door.drum(CX, hinge_y - 0.057, Z, 0.245, 0.038, STEEL, inner=0.214)
door.drum(CX, hinge_y + 0.046, Z, 0.421, 0.012, GASKET, inner=0.395)
# Cast ribs across the annular door web. Hinge leaves reach from the
# reinforced flange to the two pivot barrels in the open position.
for i in range(8):
    a = TAU * (i + 0.5) / 8
    door.box(
        (CX + 0.312 * math.cos(a), hinge_y + 0.062, Z + 0.312 * math.sin(a)),
        (0.147, 0.038, 0.018),
        CAST,
        0.005,
        (0, -a, 0),
    )
for z in (Z - 0.219, Z + 0.219):
    door.box((hinge_x - 0.057, hinge_y, z), (0.16, 0.06, 0.049), CAST)
    door.tube((hinge_x, hinge_y, z - 0.016), (hinge_x, hinge_y, z + 0.027), 0.041, TEAL)
door.box((CX - 0.402, hinge_y + 0.069, Z), (0.057, 0.074, 0.135), ORANGE, 0.011)
for i in range(12):
    a = TAU * i / 12
    door.drum(
        CX + 0.381 * math.cos(a),
        hinge_y - 0.048,
        Z + 0.381 * math.sin(a),
        0.015,
        0.024,
        DARK,
        sides=6,
    )
door.box((CX - 0.382, hinge_y - 0.072, Z), (0.038, 0.09, 0.20), DARK, 0.015)

for part in parts:
    part.finish()

scene = bpy.context.scene
scene.unit_settings.system = "METRIC"
scene.unit_settings.scale_length = 1.0
for name, explode in (("assembled", False), ("exploded", True)):
    for part in parts:
        part.obj.location = part.offset if explode else (0, 0, 0)
    bpy.context.view_layer.update()
    bpy.ops.export_scene.gltf(
        filepath=str(OUT / f"{name}.glb"),
        export_format="GLB",
        export_extras=True,
        export_animations=False,
        export_cameras=False,
        export_lights=False,
    )
print(f"Saved two GLBs with {len(parts)} named mesh groups.")
