---
name: mechanical-assembly
description: Use for conceptual 3D mechanical assemblies and exploded views, including feed pellet mills, ring dies, rollers and housings.
---

State the units, assumptions and the intended level of geometric detail. For a conceptual ring-die pellet mill represent the base, main housing, ring die, compression rollers, spindle, drive and feed components as distinct named meshes. Use a consistent axis for the shaft, die and rollers. Select proportions appropriate to the request. Identify illustrative dimensions in the final answer.

With trimesh, construct a Scene with a separate geometry/node for each moving group. Use annular geometry for a ring die. Offset transforms of the same components along sensible disassembly directions to generate the exploded scene. Keep an identity or part-name mapping between both views. Export both configurations with embedded materials, using the filenames the user requested. Keep the model-generation script readable. For a user-requested slider, provide a local self-contained viewer or exported animation.

Reopen the GLBs with trimesh to verify file signatures, expected node count, nonempty geometry and finite bounds. Verify that relative part distances increase in the exploded file. Report the measured checks.
