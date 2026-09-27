# Restore the saved app on another computer

The current app workspace is saved separately from source code as a private GitHub
release asset. Cloning alone does not include videos, photos, scans, or MongoDB.
The snapshot contains both recordings (including IMG_3515), room scans and 3D tags,
camera photos/registrations, extracted poses, completed re-enactments, catalog,
inventory/receipts/alerts/history/deduplication markers, and the selected recording,
player mode and timestamp. It resumes **paused**, without applying any new signals.

This is a point-in-time snapshot, not ongoing cross-computer synchronization. Local
credentials, environment files, Unity logs/caches, unfinished uploads and running
renders are excluded. The snapshot belongs to this private repository; use an
account with repository access when downloading it.

## One-time setup (macOS/Linux shell)

Install Python 3.12, Node.js, MongoDB 7 (or Docker), and GitHub CLI. From the clone:

```sh
git switch main
git pull --ff-only
python3 -m venv backend/.venv
source backend/.venv/bin/activate
python -m pip install -r backend/requirements.txt
npm ci --prefix backend/dashboard
npm run build --prefix backend/dashboard
```

Start local MongoDB, for example `docker run -d --name pharma-mongo -p
127.0.0.1:27017:27017 -v pharma-mongo-data:/data/db mongo:7.0`. If MongoDB is already
running, use its URI instead; do not start a second server on the same port.

Download the saved workspace:

```sh
gh release download workspace-2026-09-26 --repo mao-christopher/Pharma \
  --pattern 'pharma-workspace-2026-09-26.zip*' --dir ../pharma-snapshot
```

Set the following variables in the same terminal. Use a **new** directory and
pharmacy identity/database: restore deliberately refuses to overwrite existing data.

```sh
export MONGO_URI='mongodb://127.0.0.1:27017'
export MONGO_DB_NAME='pharma_restored'
export PHARMACY_ID='restored-demo'
export DATA_DIR="$PWD/backend/restored-data"
python backend/scripts/workspace_bundle.py restore \
  --archive ../pharma-snapshot/pharma-workspace-2026-09-26.zip \
  --data-dir "$DATA_DIR"
python backend/scripts/run_server.py --host 127.0.0.1 --port 8011
```

Open http://localhost:8011. Keep those environment variables when restarting the
server, or put them in your own untracked `backend/.env` (use an absolute DATA_DIR).
On Windows, activate the venv from `backend\.venv\Scripts` and set the equivalent
PowerShell `$env:...` variables; the restore Python command is the same.

Existing recordings and saved renders play without Unity or a new YOLO pass.
To process new videos, model weights download through the existing pose helper.
To generate **new** re-enactments, install/license Unity 6000.6.3f1, run
`python simulation/tools/fetch_character.py`, set `UNITY_PATH` to that machine's
Unity executable, and restart the app. Do not copy the old computer's Unity path.

## What this snapshot preserves

The September 26 snapshot restores the current IMG_3515 recording at 1.686 seconds
in Real mode. Both saved renders were already marked stale on the source app and
remain stale after restore; they are still available for playback. Use Render only
when you deliberately want a new version. Camera/room calibration and cached pose
file timestamps are preserved, so moving computers alone does not invalidate them.
Room-page selections are URL parameters; all saved rooms and camera views are
available from the selectors. Browser-specific theme, orbit angle and unsaved form
edits are not part of the saved backend workspace.

Archive SHA-256 and asset identity are tracked in `demos/current-workspace.json`.
Every included file is also hash-verified before restore writes any inventory.

## Save a later snapshot

Pause the player, finish uploads/renders, and avoid edits during export. Use the
running app's DATA_DIR, MONGO_URI, MONGO_DB_NAME and PHARMACY_ID:

```sh
python backend/scripts/workspace_bundle.py export --data-dir "$DATA_DIR" \
  --api http://127.0.0.1:8011 --output /new/path/pharma-workspace.zip
```

Export refuses if the files or Mongo revision change during capture. Upload the new
archive as a new private release asset when you want to share it. Do not commit
large recordings or credentials into Git.
