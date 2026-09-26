# Pharma YOLO

Object detection pipeline built on [Ultralytics YOLO](https://docs.ultralytics.com/).

## Quick start (local)

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate      # macOS / Linux
.venv\Scripts\activate         # Windows

# 2. Install dependencies
pip install -r requirements-dev.txt

# 3. (Optional) install the package in editable mode
pip install -e .

# 4. Copy and edit env config
cp .env.example .env

# 5. Run detection on any image
python scripts/detect.py path/to/image.jpg

# 6. Fine-tune on your own data
#    a. Copy datasets/dataset.yaml.example → datasets/dataset.yaml and edit it
#    b. Put images + labels under data/raw/{train,val}/
python scripts/train.py train datasets/dataset.yaml --epochs 50

# 7. Run tests
pytest
```

## Quick start (Docker)

```bash
# Build image
docker compose -f docker/docker-compose.yml build

# Run detection
docker compose -f docker/docker-compose.yml run --rm yolo \
  scripts/detect.py /app/data/raw/test/images/sample.jpg

# Launch Jupyter Lab
docker compose -f docker/docker-compose.yml up notebook
# then open http://localhost:8888
```

## Project layout

```
.
├── src/pharma/         # Python package
│   ├── config.py       # Central settings (reads .env)
│   ├── detect.py       # Inference helper
│   └── train.py        # Training + export helpers
├── scripts/            # Thin CLI entry points
├── tests/              # pytest suite
├── docker/             # Dockerfile + docker-compose.yml
├── data/               # Raw and processed data (NOT committed)
├── datasets/           # Dataset YAML configs
├── models/             # Model weights (NOT committed — download at runtime)
└── runs/               # YOLO output artefacts (NOT committed)
```

## Model weights

YOLO weights are downloaded automatically by Ultralytics on first use.
They are listed in `.gitignore` — do **not** commit large `.pt` files.
Store shared weights in a shared drive or object storage and reference the path in `.env`.

## Team workflow

1. Branch off `main` for each feature/experiment.
2. Keep `datasets/*.yaml` (not `datasets/*.yaml.example`) in git once they're stable.
3. Log experiments with [Weights & Biases](https://wandb.ai) — set `WANDB_API_KEY` in `.env`.
4. Run `pytest` before opening a PR.
