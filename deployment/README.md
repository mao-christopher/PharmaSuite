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
The deployed domain is `pharmasuite.tech` (also `www.pharmasuite.tech`), with both
DNS A records pointing to `64.177.51.214`. The separate domain vhost lets Certbot
redirect domain HTTP traffic to HTTPS while preserving the IP-based HTTP demo.
On a fresh server, install `certbot python3-certbot-nginx`, enable the bootstrap
nginx config, then run `certbot --nginx -d pharmasuite.tech -d www.pharmasuite.tech --redirect`.
Do not overwrite a live Certbot-managed config with the bootstrap template.
The production certificate and automatic renewal timer are installed.
The app is an unauthenticated shared demo, not a patient-data deployment.

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

For the recorded wristband demo, copy `configure_img3537_events.py` and
`img3537-camera-regions.json` into the API container's `/tmp/` directory, then
run `python /tmp/configure_img3537_events.py`. It only runs with pharmacy ID
`img-3537-demo`. This one-time migration pauses and backs up the existing state,
preserves existing camera registration (or installs manually annotated fallback regions), and resets the
earlier unconfigured replay to opening stock. Do not run it routinely: it is a
demo reset. The notifications label the video-reviewed times as simulated
wristband events; medication/location are resolved using YOLO wrist evidence.

Set `reset_on_replay: true` in the supplied recording's scenario.json to enable
the Reset demo button and automatic reset when restarting or returning to zero.
The app archives prior run activity in its audit history before restoring opening
stock. Leave this flag absent for ordinary recordings that must remain replay-only.

The current registered camera is retained. The supplied recording uses
`min_region_margin: 0.01` to ask for confirmation when two candidate storage
regions are separated by less than 1% of the frame diagonal in wrist distance.
This provisional setting catches its final shelf-boundary placement. It does not
change the annotations, infer drug identity from pixels, or override a review.
