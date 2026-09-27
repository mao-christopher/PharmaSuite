# Workspace snapshots

Source code does not include recordings, photos, room scans or the MongoDB state.
`scripts/workspace_bundle.py` packages all of that into one archive so a demo can be
reopened on another computer, exactly where it was left, without re-running Unity or
YOLO. A snapshot is a point-in-time copy, not ongoing synchronization.

A snapshot contains the recordings and their extracted poses, room scans and 3D
tags, camera photos and registrations, completed re-enactment renders, the catalog,
the full inventory state (receipts, alerts, history and deduplication markers), and
the selected recording, player source and timestamp. It restores **paused** and
applies no new signals. Credentials, `.env` files, Unity caches, unfinished uploads
and running renders are excluded.

Snapshots can contain footage of real people and rooms. Share them only with people
who should see that footage, and never commit them to Git.

## Export

Pause the player, let uploads and renders finish, and avoid edits during export.
Use the running server's `DATA_DIR`, `MONGO_URI`, `MONGO_DB_NAME` and `PHARMACY_ID`:

```sh
python backend/scripts/workspace_bundle.py export \
  --data-dir "$DATA_DIR" --api http://127.0.0.1:8000 \
  --output /path/to/pharma-workspace.zip
```

Export refuses if files or the MongoDB revision change while it runs.

## Restore

Set up the backend and dashboard as in the main README (MongoDB, the Python
environment, and `npm run build` in `backend/dashboard`). Then restore into a **new**
data directory and database: restore refuses to overwrite existing data.

```sh
export MONGO_URI='mongodb://127.0.0.1:27017'
export MONGO_DB_NAME='pharma_restored'
export PHARMACY_ID='restored-demo'
export DATA_DIR="$PWD/backend/restored-data"
python backend/scripts/workspace_bundle.py restore \
  --archive /path/to/pharma-workspace.zip --data-dir "$DATA_DIR"
python backend/scripts/run_server.py --host 127.0.0.1 --port 8011
```

Open http://localhost:8011. Keep those variables when restarting the server, or put
them in an untracked `backend/.env` (with an absolute `DATA_DIR`). Every file is
hash-verified before any inventory is written.

Saved recordings and renders play without Unity or a new YOLO pass, and camera
calibration and pose caches stay valid after the move. Renders that were stale when
exported stay stale. To create **new** re-enactments on the restored machine,
install Unity 6000.6.3f1, run `python simulation/tools/fetch_character.py`, and set
`UNITY_PATH` to that machine's Unity executable.
