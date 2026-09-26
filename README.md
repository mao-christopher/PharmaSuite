# Pharma YOLO & Inventory Platform

Pharmacy inventory demo that tracks bottles from camera footage. Upload one or more
videos plus a file of pickup and put-down times. YOLO pose estimation finds the
technician's hand at each signal, matches it to the shelf, counter or disposal region
drawn for that camera, and updates a MongoDB-backed inventory that a React dashboard
shows live. A Unity simulation renders the same workflows, so the pipeline can be
tested on footage with known answers.

What works today, and what is still planned, is tracked in [plan.md](plan.md).
Results on rendered footage are in [simulation/VALIDATION.md](simulation/VALIDATION.md);
nothing has been validated on real pharmacy footage yet.

## Project Structure

```
.
├── backend/
│   ├── src/pharma/           # Python package: pose/detect helpers, API, inventory rules, services
│   ├── dashboard/            # React dashboard (Vite): player, recordings, inventory, camera setup
│   ├── data/                 # catalog.json (medications, opening stock), layouts/ (camera views),
│   │                         # scenarios/ (bundled demo; uploads land here, gitignored)
│   ├── scripts/              # CLI entrypoints (run_server.py, pose.py, detect.py, …)
│   ├── tests/                # pytest suite (uses an in-memory MongoDB)
│   ├── docker/               # Dockerfile and docker-compose (MongoDB service)
│   ├── MONGODB.md            # Storage, migration and recovery
│   └── MULTICAMERA.md        # Camera switching and the multi-camera recording format
├── simulation/               # Unity pharmacy scene, offline capture, fixtures & evaluation
├── plan.md                   # Product rules, decisions, milestones and open risks
├── AGENTS.md                 # Agent guidelines and conventions
└── README.md                 # Project overview and quickstart
```

## Run the dashboard locally

You need Python 3.10+, Node 20+, and Docker (for MongoDB).

```bash
# 1. MongoDB. The API refuses to start without it.
docker compose -f backend/docker/docker-compose.yml up -d mongo

# 2. Backend API on http://127.0.0.1:8000
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt && pip install -e . --no-deps
python scripts/run_server.py --host 127.0.0.1 --reload

# 3. Dashboard on http://localhost:3000 (in another terminal; proxies /api to :8000)
cd backend/dashboard
npm ci && npm run dev
```

`backend/.env.example` lists the settings (`MONGO_URI`, `MONGO_DB_NAME`, `PHARMACY_ID`,
`DATA_DIR`, `POSE_IMGSZ`); the defaults work with the MongoDB container above. Pose
extraction uses `yolo11n-pose.pt`, which Ultralytics downloads on first use, at 960 px
inference size (`POSE_IMGSZ=640` is faster but misses more distant people). To point the dashboard at
another backend, set `API_URL`, e.g. `API_URL=http://127.0.0.1:8001 npm run dev`.

Tests: install `simulation/requirements.txt` as well (some backend tests import the
simulation tools), then run `python -m pytest backend/tests simulation/tests -q` from the
repository root. `backend/tests/test_detect.py` downloads weights and a sample image, so
skip it offline with `--ignore=backend/tests/test_detect.py`.

## Unity Simulation

See [simulation/README.md](simulation/README.md) for opening the Unity project,
playing the scene, exporting a recording, and running pose evaluation. The demo
contains one technician, three shelf banks with aisles, a dispensing counter, and a disposal bin.
It records an 106-second aisle walkthrough and pickup/counter/wrong-return/correction/disposal
sequence with explicit agent/bottle states, paths around obstacles, and per-tick collision guards.
The CV presentation tool exports an actual YOLO overlay, skeleton-only video, side-by-side
comparison, and timestamped keypoint/confidence observations from the rendered pixels.
A separate labeled simulation X-ray view keeps the Unity skeleton visible behind geometry.
Animation includes planted feet, smoother turns, arm swing, and eased reach/handling motion.

```sh
python3 simulation/tools/fetch_character.py
# Add simulation/ in Unity Hub; open Assets/Pharma/Generated/Pharmacy.unity and press Play.
```

The simulator uses Unity 6000.6.3f1. Large character assets are fetched from a pinned
MIT-licensed source; recordings and model weights are not committed. Physical IMU
hardware/firmware remain separate from this project change.

## Pose and detection CLIs

With the backend environment from above activated:

```bash
cd backend
python scripts/pose.py path/to/video.mp4      # 17-point skeletons on an image or video
python scripts/live_pose.py                   # webcam pose estimation
python scripts/detect.py path/to/image.jpg    # object detection
```

## Quick Start (Docker)

```bash
# Build backend image
docker compose -f backend/docker/docker-compose.yml build

# Run detection inside container
docker compose -f backend/docker/docker-compose.yml run --rm yolo \
  scripts/detect.py /app/data/raw/test/images/sample.jpg

# Launch Jupyter Lab
docker compose -f backend/docker/docker-compose.yml up notebook
# then open http://localhost:8888
```

## Model Weights

YOLO weights are downloaded automatically by Ultralytics on first use.
They are listed in `.gitignore` — do **not** commit large `.pt` files.
Store shared weights in a shared drive or object storage and reference the path in `.env`.

## Team Workflow

1. Branch off `main` for each feature/experiment.
2. Keep `datasets/*.yaml` (not `datasets/*.yaml.example`) in git once they're stable.
3. Log experiments with [Weights & Biases](https://wandb.ai) — set `WANDB_API_KEY` in `.env`.
4. Run `pytest` inside `backend/` before opening a PR.
