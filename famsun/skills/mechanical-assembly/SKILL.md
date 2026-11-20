---
name: mechanical-assembly
description: Build conceptual 3D mechanical assemblies with named parts, materials and exploded views, including feed pellet mills, housings, ring dies and rollers.
---

Use the installed assembly builder for equipment that can be represented by boxes, solid cylinders and hollow annuli. You choose part names, dimensions, positions, colors and disassembly directions according to the user’s brief. The builder exports two self-contained GLBs and reopens both to verify the parts, rigid poses, geometry and materials. For curved or detailed custom parts outside these primitives, use trimesh directly.

Author a manifest in the workspace. This small mounting example shows the schema:

```json
{
  "units": "cm",
  "parts": [
    {"name":"bracket","shape":"box","size":[9,6,1],"position":[0,0,0],"color":[63,78,85,255]},
    {"name":"bushing","shape":"annulus","inner_radius":0.9,"outer_radius":1.6,"height":2,"position":[0,0,1.5],"axis":[0,0,1],"color":[201,156,91,255],"explode":[0,0,3]}
  ]
}
```

Units can be mm, cm or m. Each named part is centered on its `position`. Cylinder and annulus heights extend symmetrically along local Z. A solid cylinder requires `radius` and `height`. Use `axis` to aim local Z along a world direction. Optional `roll_deg` rotates around this local Z before alignment. `explode` is a translation in world coordinates in the same units. Defaults are zero translation, +Z axis, zero roll and opaque grey. At least one part must move.

Run the builder with the actual requested output filenames. A single successful invocation verifies both exports:

```sh
python /opt/famsun/skills/mechanical-assembly/scripts/build_assembly.py /workspace/assembly.json /workspace/assembled.glb /workspace/exploded.glb
```

For rotating machinery, use a common shaft/die axis, distinct material colors and stable part names in both views. Place rollers within the ring bore and choose clear disassembly directions. Include a human-readable parts list or a viewer if requested. Report units, illustrative dimensions and the verification result.
