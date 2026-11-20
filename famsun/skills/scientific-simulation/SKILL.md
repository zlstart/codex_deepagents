---
name: scientific-simulation
description: Use for physical simulations and differential equations, including three-body orbital trajectories, conservation checks and visualizations.
---

State the coordinate convention, units, initial conditions and integration interval. Use the specified initial conditions and simulation interval. If the user asks for an example and leaves these open, a dimensionless equal-mass figure-eight orbit is one reproducible option. Integrate vector positions and velocities together with scipy.integrate.solve_ivp (DOP853 is suitable for this example). Sample the dense solution on an evenly spaced grid for export and animation.

Validate that the solver reached the end time, all samples are finite, and no unintended collision occurred. Independently calculate relative energy drift and the center-of-mass or momentum drift. Include measured values with the solver tolerances. If the motion is planar, say so even if the viewer supports 3D.

Save requested exports in /workspace using the specified filenames and schema. Use equal spatial aspect when plotting trajectories. Include a self-contained visualization when requested. Check exported files by reopening them and confirming the time range and sample count.
