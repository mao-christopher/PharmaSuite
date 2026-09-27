# MongoDB inventory store

Live pharmacy state lives in MongoDB. There is no local-file fallback: if MongoDB is
unavailable, the API refuses to start or pauses updates.

## Run locally

1. Start MongoDB: `docker compose -f backend/docker/docker-compose.yml up -d mongo`.
2. Optionally copy `backend/.env.example` to `backend/.env` and set `MONGO_URI`,
   `MONGO_DB_NAME` and a stable `PHARMACY_ID`. The defaults are a local MongoDB,
   database `pharma` and pharmacy `default`. Use credentials for any remote
   deployment, and keep them in `.env`, never in Git.
3. Install `backend/requirements.txt` and run `pip install -e backend --no-deps`.
4. From `backend/`, run `python scripts/run_server.py`. For the development UI run
   `npm ci && npm run dev` in `backend/dashboard`, or `npm run build` to have the
   API serve the built dashboard.

Runtime storage failures pause replay and return 503; conflicting writers return
409. The dashboard shows a storage error, and counts are never shown as saved until
MongoDB acknowledges the write. After recovery, refresh: reads load MongoDB's state,
not a cached copy. Run a single API worker: separate workers would have separate
media clocks, even though revision checks stop them overwriting each other.

## One authoritative document

The `pharmacy_state` collection has one document per `PHARMACY_ID`. It stores
inventory, receipts, prescription deduction flags, movement sessions, disposals,
alerts, shipments, applied signals, per-recording deduplication markers, and an
append-only audit history. Every write replaces the whole document with a revision
check (majority, journaled), so an event and its stock change always land together
and a stale process can never silently overwrite newer state. See MongoDB's
[atomicity documentation](https://www.mongodb.com/docs/manual/core/write-operations-atomicity/).

`data/catalog.json` and the camera view files remain configuration on disk, and
videos and pose files remain media on disk. Editing opening stock never overwrites
live stock; only the explicit reset restores opening values. Switching or replaying
recordings keeps the same inventory and applied-event history.

On the very first startup, an existing legacy `data/state/pharmacy.json` is validated
and imported if MongoDB has no document for this pharmacy. Its checksum and import
time are recorded and the file is left in place as a backup. From then on MongoDB
always wins, even if the JSON file is edited later. Missing or invalid state during
a running session is an error, never a reason to reseed.

## Size limit

One document suits a bounded demo. Writes fail explicitly at 14 MiB, before
MongoDB's 16 MiB document limit; history is never silently truncated. A long-running
deployment should split the ledger into transactional collections before reaching
that size.

## Tests

`python -m pytest backend/tests simulation/tests -q` runs against in-memory MongoDB
(`mongomock`). Set `TEST_MONGO_URI=mongodb://127.0.0.1:27017` to run the same
persistence and API tests against a real server. Each test uses a throwaway
`pharma_test_*` database that is dropped afterwards, so live data is untouched.
