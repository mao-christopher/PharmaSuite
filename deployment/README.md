# Linux demo deployment

From the repository root, copy `deployment/Dockerfile.server` and
`deployment/compose.server.yaml` into the root, then run
`docker compose -f compose.server.yaml build`.

For the saved workspace, place its archive at `/opt/server-workspace.zip`, start
MongoDB, then restore before starting the API:

```sh
docker compose -f compose.server.yaml up -d mongo
docker compose -f compose.server.yaml run --rm api python scripts/workspace_bundle.py restore --archive /restore/workspace.zip --data-dir /data/workspace
docker compose -f compose.server.yaml up -d api
```

The nginx example proxies HTTP/WebSocket/video traffic to loopback port 8000.
Set DNS and configure a valid TLS certificate before using browser camera/Bluetooth
features. The app is an unauthenticated shared demo, not a patient-data deployment.

For the September 27 scan/video setup, place the supplied original files under
`persistent/incoming/`, copy `provision_demo.py` into the API container, and run it
there after the API is ready. It converts the video, imports the scan, and uses
six visually reviewed annotations; it does not infer events for arbitrary videos.
Open the original dashboard at `/`; the supplied recording is auto-loaded. Use the Room page to inspect or adjust its shelf boxes. Keep the original files.

The Linux Unity editor and licensed-user environment must be installed and
activated separately. Set `UNITY_PATH` only after testing its graphics-enabled
batch execution in the actual API environment. Never copy another machine's
license or credentials into the image. Existing simulation videos play without
the editor. Calibration and shelf setup are still needed for new re-enactments.


For the IMG_3537-only fixture, `single_demo.py` archives unrelated recordings,
rooms and views outside the served workspace, installs four medication fixtures,
and preserves the original dashboard. The currently deployed pharmacy ID is
`img-3537-demo`; earlier inventory documents remain in MongoDB.

`restore_ui.py` is a one-off migration from Demo A-D labels to named medications.
It preserves geometry, quantities, event IDs and audit history, backs up the
prior files and state, and connects the authored `presentation.mp4` to the normal
player. Stop the API while running fixture migrations to avoid concurrent writes.

The Unity presentation is generated on a licensed machine and copied to
`persistent/workspace/demo/simulation.mp4` and the recording's `presentation.mp4`.
It is explicitly illustrative, separate from registered-camera evidence. Camera
rectangles in recording metadata only draw overlays and cannot mutate stock.
