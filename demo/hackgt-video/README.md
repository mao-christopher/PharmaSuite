# HackGT video edit

The final 163-second, 1920×1080, 30 fps video and shelf-transition preview are attached to the `hackgt-demo-v4` GitHub release. Large media stays out of Git, per AGENTS.md.

The existing narration is retained. Captions sit at the bottom. The new Unity transition starts at 1:34.5, expands over the real shelf crossing, then uses editorial cuts between synchronized camera renders. It is a staged continuation, not an exact reconstruction or motion capture. Skeleton overlays appear only in the transaction section.

## Source and rebuilding

`render_demo.py` preserves the final edit timeline. Install NumPy, OpenCV, Pillow and imageio-ffmpeg. Set `PHARMA_VIDEO_WORKSPACE` to the original workspace root containing `work/video-source`, `work/video-review` and `outputs`, and `PHARMA_LABCOAT_VIDEO` to IMG_3537.mov. The source media and previous dashboard captures are external prerequisites; this checkout alone is not a self-contained render package. The script currently uses macOS system fonts and the workspace's sim-venv ffmpeg binary.

`simulation/Assets/Pharma/Editor/DemoBridgeExporter.cs` exports 330 synchronized frames per camera. Set `PHARMA_BRIDGE_OUT` to a new output directory, then invoke Unity with `-batchmode -quit -projectPath simulation -executeMethod Pharma.Simulation.Editor.DemoBridgeExporter.Export`. The generated pharmacy scene and fetched character assets must already be present.

The compositor outputs a silent video. Mux it with the supplied narration master using ffmpeg, preserving video frames and encoding AAC audio at 48 kHz. Narration order is 203, 201, 202, 205. See render-notes.md for the render scope and validation.
