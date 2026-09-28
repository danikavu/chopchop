"""`python -m chopper` / `chopper` — run the local server."""

import argparse
import logging
import webbrowser

import uvicorn

from .app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Chopper local server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if not args.no_browser:
        webbrowser.open(f"http://{args.host}:{args.port}")
    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
