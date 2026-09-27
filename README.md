# PharmaSuite

PharmaSuite tracks pill-bottle handling in a pharmacy. A wrist-worn IMU detects
pickups and put-downs, pose estimation on ordinary camera footage works out which
shelf was involved, and a MongoDB-backed dashboard keeps pooled tablet stock,
shelf counts, expiry and alerts up to date. Built at HackGT.

[Watch the demo video](https://github.com/mao-christopher/Pharma/releases/tag/hackgt-demo-v4)

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

## What's in this repository

The write-up above describes the whole vision. This is what the code does today:

- **Inventory engine and API** (`backend/`): FastAPI service that runs YOLO11 pose on
  recorded or live footage, matches the wrist to configured shelf, counter and
  disposal regions at each pickup/put-down signal, and applies pharmacy rules
  exactly once per signal: pooled tablets per medication, bottles on and off
  shelves, wrong-shelf alerts, counter parking, disposal forms, expiry alerts, and
  one-time prescription deductions. Uncertain locations wait for an employee.
- **Dashboard** (`backend/dashboard/`): React app with the player and signal log,
  notifications, inventory, prescriptions, shipment intake, room setup (LiDAR scan
  import, 3D shelf tags, camera registration) and live capture.
- **Wristband firmware** (`wristband/`): ESP32-S3 + MPU6050 with an on-device Edge
  Impulse classifier that sends pickup/put-down events over BLE.
- **Unity simulation** (`simulation/`): a synthetic pharmacy with a rigged technician
  that renders test footage with synchronized mock sensor events, and re-enacts real
  recordings from the dashboard's decisions.
- **Synthetic data** (`demo/synthetic_shipments/`): eight fictitious supplier
  deliveries in CSV, XML, EDI, JSON and PDF.

Accuracy has only been measured on rendered footage (see
[simulation/VALIDATION.md](simulation/VALIDATION.md)); nothing has been validated in
a real pharmacy. [plan.md](plan.md) records the product rules, design and open work.

## Repository layout

```
backend/
  src/pharma/        Python package: API, inventory engine, pose, rooms, storage
  dashboard/         React (Vite) dashboard
  scripts/           CLIs: run_server.py, pose.py, live_pose.py, import_sim_room.py, ...
  tests/             pytest suite (in-memory MongoDB by default)
  data/              catalog, camera views and a bundled demo recording
  docker/            docker-compose for local MongoDB
simulation/          Unity 6 project, render/evaluation tools and their tests
wristband/           PlatformIO firmware for the BLE wristband
demo/                synthetic shipment documents
plan.md              product rules, design and status
AGENTS.md            guidelines for AI coding agents working in this repo
```

## Run it locally

You need Python 3.10+, Node 20+, and Docker (for MongoDB).

```bash
# 1. MongoDB. The API refuses to start without it.
docker compose -f backend/docker/docker-compose.yml up -d mongo

# 2. Backend API on http://127.0.0.1:8000
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt && pip install -e . --no-deps
python scripts/run_server.py --reload

# 3. Dashboard on http://localhost:3000 (in another terminal; proxies /api to :8000)
cd backend/dashboard
npm ci && npm run dev
```

The defaults work with the MongoDB container above. To change them, copy
`backend/.env.example` to `backend/.env` (`MONGO_URI`, `MONGO_DB_NAME`, `PHARMACY_ID`,
`DATA_DIR`, `POSE_IMGSZ`, `UNITY_PATH`). Ultralytics downloads `yolo11n-pose.pt` on
first use. To point the dashboard at another backend, run
`API_URL=http://127.0.0.1:8001 npm run dev`.

The repository ships only a scripted signal-only demo (`demo_scenario_01`, no video),
since recordings are not committed. Upload your own footage with a pickup/put-down
timestamps file from **Recordings**, or render footage with the Unity simulation.

### Live capture

Open the dashboard in Chrome and select **Go live**. Pick a camera (a webcam or an
iPhone through Continuity Camera), its registered view and the wearing wrist, then
connect the wristband from Chrome's Bluetooth picker. Only the 10 s around each band
event are kept (9 s before, since the band notifies late); each clip is analyzed and
applied to inventory once. Add `?dev=1` to the URL to send pickups and put-downs with
the Space bar when no band is connected.

### Tests

```bash
pip install -r simulation/requirements.txt   # some backend tests import simulation tools
python -m pytest backend/tests simulation/tests -q
cd backend/dashboard && npm test && npm run build
```

Run pytest from the repository root. Set `TEST_MONGO_URI` to test against a real
MongoDB server instead of the in-memory default.

## Unity simulation

See [simulation/README.md](simulation/README.md) for opening the project (Unity
6000.6.3f1), rendering footage and mock sensor events, and running pose evaluation.
Character art is fetched from a pinned, MIT-licensed Microsoft Rocketbox source:

```sh
python3 simulation/tools/fetch_character.py
```

## Pose CLIs

With the backend environment activated:

```bash
cd backend
python scripts/pose.py path/to/video.mp4   # 17-point skeletons on an image or video
python scripts/live_pose.py                # webcam pose estimation
```

## More documentation

- [plan.md](plan.md): product rules, architecture and status
- [backend/MONGODB.md](backend/MONGODB.md): storage, migration and recovery
- [backend/MULTICAMERA.md](backend/MULTICAMERA.md): camera switching and multi-camera recordings
- [backend/SHIPMENTS.md](backend/SHIPMENTS.md): shipment import and stocking
- [backend/WORKSPACE_SNAPSHOTS.md](backend/WORKSPACE_SNAPSHOTS.md): moving a demo workspace between computers
- [simulation/README.md](simulation/README.md) and [simulation/VALIDATION.md](simulation/VALIDATION.md)
- [wristband/README.md](wristband/README.md): firmware, BLE protocol and model

All medications, prescriptions, suppliers and shipments in this repository are
synthetic. Do not commit `.env` files, model weights, recordings or room scans.
