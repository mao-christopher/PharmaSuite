# IMG_3537 presentation scene

This is an authored, illustrative reconstruction of IMG_3537, not registered
motion capture or inventory/CV evidence. The GLB's approximate 1.85 x 8.08 m room
extent supplies the footprint. Shelf locations and body route are stylized from
video review. Two shelf levels have two medication positions each; a counter is
separate. Medication names are real (Metformin, Atorvastatin, Ibuprofen and Amoxicillin); stock and observed bottle identities are demo fixtures.

Reuses the existing Rocketbox character and bottle assets. In Unity use
**Pharma > IMG_3537 > Build reviewed demo** to inspect the scene at the first pickup.
Regenerate plan.json with `python simulation/tools/build_demo3537.py`.

To export, launch the licensed editor in batch mode with the simulation project,
`-executeMethod Pharma.Simulation.Editor.ReenactmentExporter.Export`,
`-pharmaPlan` pointing to this plan.json and `-pharmaOutput` to a fresh directory.
Then run `python simulation/tools/encode_demo3537.py EXPORT_DIRECTORY OUTPUT.mp4`.
The encoder labels simulation footage and burns in the rig overlay only inside
pre-action windows. The rig JSON stays under evaluator_only and never enters CV.
The source clip is 44.48 seconds; this export is 667 frames at 15 FPS (44.47 s).

Validated render: 6 shown actions, 0 downgraded reaches, 0 guard holds, 4 labels,
4 bottles, 9 solid colliders. This is deterministic scene validation, not a claim
of measured camera reconstruction accuracy.
