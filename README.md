# Pharma YOLO & Inventory Platform

Pharmacy inventory project with a working YOLO pose-estimation scaffold and a Unity simulation that exports camera footage and synchronized mock IMU events. Event fusion, inventory management, MongoDB persistence, and the dashboard remain planned work.

## Project Structure

```
.
├── backend/                  # Backend CV pipeline, event association & inventory service
│   ├── src/pharma/           # Core Python package (config, detect, pose, train)
│   ├── scripts/              # CLI entrypoints (detect.py, live_pose.py, pose.py, train.py)
│   ├── tests/                # pytest test suite
│   ├── docker/               # Dockerfile and docker-compose configurations
│   ├── pyproject.toml        # Package setup and build configuration
│   ├── requirements.txt      # Production dependencies
│   └── requirements-dev.txt  # Development dependencies
├── simulation/               # Unity pharmacy scene, offline capture, fixtures & evaluation
├── plan.md                   # System architecture and product specification
├── AGENTS.md                 # Agent guidelines and conventions
└── README.md                 # Project overview and quickstart
```

## Unity Simulation

See [simulation/README.md](simulation/README.md) for opening the Unity project,
playing the scene, exporting a recording, and running pose evaluation. The demo
contains one technician, labeled shelves, a dispensing counter, and a disposal bin.
It records a 44-second pickup/counter/wrong-return/correction/disposal sequence
with explicit agent/bottle states, paths around obstacles, and per-tick collision guards.

```sh
python3 simulation/tools/fetch_character.py
# Add simulation/ in Unity Hub; open Assets/Pharma/Generated/Pharmacy.unity and press Play.
```

The simulator uses Unity 6000.6.3f1. Large character assets are fetched from a pinned
MIT-licensed source; recordings and model weights are not committed. Physical IMU
hardware/firmware remain separate from this project change.

## Quick Start (Local Backend)

```bash
# 1. Navigate to backend directory
cd backend

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate      # macOS / Linux
.venv\Scripts\activate         # Windows

# 3. Install dependencies and package in editable mode
pip install -r requirements-dev.txt
pip install -e .

# 4. Copy environment config
cp .env.example .env

# 5. Run detection on an image or video
python scripts/detect.py path/to/image.jpg

# 6. Run live pose estimation
python scripts/live_pose.py

# 7. Run test suite
pytest
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
