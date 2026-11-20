---
name: scientific-simulation
description: Use for physical simulations and differential equations, including Newtonian N-body trajectories, conservation checks and visualizations.
---

## Newtonian point masses

For unsoftened Newtonian point masses in three dimensions use the bundled solver. Prepare `/workspace/initial.json` from the requested initial conditions. Supply positive `masses` as an array, `positions` and `velocities` as matching arrays of XYZ triples, a positive gravitational constant `G`, `t_start`, `t_end`, and an integer `samples` including both endpoints. Optional fields are solver tolerances `rtol`, `atol`, and `references`, a list of source descriptions. Use one consistent unit system and inertial frame. Apply any requested coordinate transformation to both positions and velocities before writing the input. For a sample interval Δt, check that the requested span is divisible by Δt and use 1 + (end − start) / Δt samples.

Run the helper in the session sandbox, passing the filenames requested by the user.

```bash
python /opt/famsun/skills/scientific-simulation/scripts/nbody.py /workspace/initial.json \
  --csv /workspace/trajectory.csv --plot /workspace/orbits.png --checks /workspace/checks.json
```

The DOP853 solver exports one CSV row per sample and body with columns `t,body,x,y,z,vx,vy,vz` (body indices start at one), an equal-scale 3D PNG and JSON diagnostics. Diagnostics include the extrema of energy and momentum drift, minimum *sampled* pair separation, and center-of-mass drift from its predicted inertial motion. When initial energy or momentum is zero to roundoff, report the absolute drift; the relative diagnostic is null. Use `max_com_displacement` for displacement from the initial center. Reopen the outputs to check the sample count, time bounds and requested files. Report solver tolerances, measured errors, initial conditions/source and units. State when the trajectories lie in a tilted or axis-aligned plane.

For other equations, generate a problem-specific integrator in the workspace. Validate the time span, finite outputs and physically meaningful invariants. Keep the inputs and scripts needed to reproduce the result.
