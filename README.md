# PharmaSuite

## Inspiration

Every year, thousands of Americans die due to pharmaceutical drug mishandling. Dr. Marv Shepard, the former Chairman of the Pharmacy Administration at the University of Texas, claims that the typical pharmacy makes 2 to 4 mistakes a day, which is an alarming rate for such a high-stakes action. After speaking with peers that worked as pharma technicians at Walgreens, CVS, and local stores, we began to better understand the issues that afflict the drug handling process. By developing our own edge compute model, we were confident we could create an automated system to reduce error. With tools like PharmaSuite, we hope to build a new tomorrow where everyone can trust healthcare professionals.

## What it does

PharmaSuite minimizes drug handling errors by monitoring all activities relating to prescription drug handling within a pharmacy. From the moment an item is shipped to the pharmacy, PharmaSuite keeps track of the quantities, expiry dates, and locations of every drug and pill bottle. When a pharmacy technician makes a transaction, throws away expired pills, or picks up a new product, PharmaSuite’s MongoDB database is automatically updated. If a pharmacist attempts to place a pill bottle on the wrong shelf or sell an expired product, they are alerted and the transaction is blocked. Replay data is collected for each transaction to maximize transparency. Whenever a pharmacy is low on stock, PharmaSuite will automatically ask providers for a new shipment. Through the full stack, almost all possible sources of error in a pharmacy would be automated.

## How we use AI?

We used the Meta API ecosystem in order to take shipping data of incoming medications, convert them into stock information, and sort out medications that commonly get mixed up in order to separate them. We have the model return a specific schema with specific prompting to ensure pharmacists will be alerted if the prescription doesn’t match the product. We also utilized a computer vision model (YOLO) to create skeletons of pharmacists so our model can better identify where a drug is being placed.

However, the most ambitious part of our project was designing our own edge compute model gesture recognition, specifically to understand when a pharmacist places or picks up a pill bottle. We manually collected sample data of different states while wearing a watch with an Inertial Measurement Unit (IMU). Then, we did spectral analysis to determine features and created a classifier with 3 layers of 256 neurons, quantizing it to run on a ESP-32 with minimal ram. By doing this, we only track the pharmacist when they perform an action that involves the medication to reduce privacy concerns in the workplace.

## How we built it

Part 1: We developed synthetic MongoDB Atlas databases to mimic shipments of goods into a pharmacy. Then, we made a model that decides where each drug should be placed based on quantity, expiry date, and name to prevent handling mistakes. We used the Meta API ecosystem to transport the data onto the pharmacy’s database to ensure minimal data loss during transfer. In addition, during the setup phase we use LiDAR to map out the pharmacy and to allow for the model to create the custom shelving and organization. This means that behind the scenes throughout the entire process we create a parallel 3D environment to the real world.

Part 2: We trained a custom model on IMU data so it correctly distinguishes between idle, random, putting down, and picking up actions with 98.21% accuracy over a custom 5,000 data point set that we collected through manual data collection over a couple of hours. As the pharmacist places bottles down on a shelf, our computer vision (CV) model, which can be connected to a standard surveillance camera, checks that the location the pharmacist placed the bottle matches the location PharmaSuite chose earlier. A CV model, YOLO, creates skeletons of people to confirm that the arm location matches where the bottle is supposed to be placed. When the pharmacist finishes stocking the pharmacy, the aggregated data of every placement is checked against the shipment data to confirm all of the pill bottles were appropriately placed and the inventory management system is updated to reflect the new quantities with new expiration dates.

Part 3: Every individual transaction with clients is also tracked. When the pharmacist picks up a pill bottle from a shelf, the model takes a snapshot of the last 10 seconds so that the transaction can be tracked without excessively interfering with the pharmacist’s privacy in the workplace. When the pharmacist moves product from the pill bottle to the prescription bottle and the sale is made, the database updates to consider the change in quantity. Then, once again, it confirms that the product is being placed in the right section.

## Challenges we ran into

We needed more custom data than we expected to allow a neural network to recognize when a person was picking up or placing down an object with only sensor data. Although the model was successful at recognizing the difference between placing objects and being idle, it struggled to initially differentiate normal active movement patterns against placing/picking up items. After providing more data and doing hyperparameter optimization, we achieved a 40% improvement in our success rate.

## Accomplishments that we're proud of

We’re proud to develop such an extensive pipeline that covers so many aspects of technology from hardware, edge computing, computer vision, and software. We didn’t go into the project expecting to create something that encompassed so many stages of the pharmaceutical process, but we’re coming out of it thinking that it may be our first step to making a contribution to increased trust between patients and healthcare professionals.

## What's next for PharmaSuite

In the future, we plan to train the edge compute model to better classify a wider range of tasks. We recognize that pharmacists may not always place items in the exact same way, or that other tasks may have a similar range of motion to it. However, with a wider range of training data, it would be possible for the model to better isolate the tasks we want it to recognize. In addition, we haven’t configured YOLO to consider multiple pharmacy technicians, which would be necessary in many real world pharmacies. However, past a 36-hour hackathon, we can definitely implement these improvements.

## Development and setup

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

Live capture: open the dashboard in Chrome and select **Go live**. Pick the Mac webcam or
an iPhone (Continuity Camera), its registered view and wrist, then connect the wristband.
Only the 10 s around each band event are saved (9 s before, since the band notifies late); each clip is analyzed and applied to
inventory once. Add `?dev=1` to the URL to trigger pickups and put-downs with Space and
no band. Clips are encoded with OpenCV, so ffmpeg is not needed.

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

## Restore the saved workspace on another computer

The current recordings, photos, room scans, camera calibration and MongoDB state
are packaged as a private GitHub release asset. Follow
[the restore guide](backend/WORKSPACE_RESTORE.md) to reopen the same recording and
player position without rerunning Unity or YOLO. This is a snapshot, not automatic
synchronization between computers.

Shipment files can be reviewed and imported from the dashboard **Shipments** page.
See [shipment intake and stocking sessions](backend/SHIPMENTS.md) for supported
formats, staged inventory, CV placements, and reconciliation.
