#!/usr/bin/env python3
"""CLI entrypoint to start the Pharma FastAPI backend server."""

import argparse
import sys
from pathlib import Path
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main():
    parser = argparse.ArgumentParser(description="Run Pharma Inventory API Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host interface (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Port number (default 8000)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload on code changes")
    args = parser.parse_args()

    print(f"Starting Pharma Inventory API Server on http://{args.host}:{args.port}")
    uvicorn.run("pharma.api.main:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
