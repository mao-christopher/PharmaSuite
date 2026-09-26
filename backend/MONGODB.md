# MongoDB inventory workflow

## Run locally

1. Start MongoDB: `docker compose -f backend/docker/docker-compose.yml up -d mongo`.
2. Create `backend/.env` from `backend/.env.example`. Set `MONGO_URI`,
   `MONGO_DB_NAME` and a stable `PHARMACY_ID`. The defaults are local MongoDB,
   database `pharma`, pharmacy `default`. Use credentials for remote deployments.
3. Install `backend/requirements.txt` and `pip install -e backend --no-deps`.
4. From `backend`, run `python scripts/run_server.py`. Run
   `npm ci && npm run dev` in `backend/dashboard` for the development UI.
   Alternatively run `npm run build`; the API serves the built dashboard.

MongoDB must be available at startup. Runtime failures pause replay and return
503; conflicting writers return 409. The UI displays a storage error and counts
are not published as successful until the database acknowledges the write.
After recovery, refresh/retry; reads load MongoDB's state rather than an old cache.
Run one API worker for the demo player; different workers have independent media
clocks even though revision checks prevent them from overwriting inventory.

## One authoritative state

`pharmacy_state` has one document per `PHARMACY_ID`. It atomically stores inventory,
receipts, transaction deduction flags, movement-session aliases, disposals, alerts,
raw applied signals, recording deduplication markers, and append-only audit history.
A revision comparison prevents stale processes from silently overwriting newer state.
Acknowledged majority+journal writes keep state and deduplication markers together.
No JSON fallback is used. See MongoDB's
[atomicity documentation](https://www.mongodb.com/docs/manual/core/write-operations-atomicity/).

The existing `data/catalog.json` and view files remain setup/configuration, and
video/pose files remain media assets. Changing the opening stock does not overwrite
live stock. Only the explicit reset action restores opening values. Switching or
replaying recordings keeps the same inventory and applied-event history.

On the first startup only, an existing `data/state/pharmacy.json` is validated and
imported if MongoDB has no pharmacy document. Its checksum and migration timestamp
are saved, and the source file is left intact as a backup. Once initialized,
MongoDB always wins—even if the old JSON file is edited later. Missing or invalid
MongoDB state during a running session is an error, never permission to reseed.
The old unused collection-deleting fixture loader has been removed.

This aggregate is suitable for the bounded demo. At 14 MiB, writes fail explicitly
before MongoDB's document limit; history is never silently truncated. A long-running
production service should split the ledger into transactional collections before
that limit. Re-uploading identical footage under a new recording identity still
represents new events; cross-upload content deduplication is separate work.

## Verify

`python -m pytest backend/tests simulation/tests -q` uses isolated in-memory MongoDB
collections. Set `TEST_MONGO_URI=mongodb://127.0.0.1:27017` to run the same persistence
and API tests against a real MongoDB server. Each test uses a unique throwaway
`pharma_test_*` database, which is dropped afterward; the live pharmacy is untouched.
