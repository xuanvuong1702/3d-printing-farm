
from __future__ import annotations

import argparse

import uvicorn

from app.moonraker.http_client import DEFAULT_MOONRAKER_PORT
from tools.moonraker_simulator.app import app

def main() -> None:
    parser = argparse.ArgumentParser(description="QIDI Moonraker Simulator")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_MOONRAKER_PORT)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)

if __name__ == "__main__":
    main()
