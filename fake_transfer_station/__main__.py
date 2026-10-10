import argparse
import os
from pathlib import Path

import uvicorn
import yaml

from .app import create_app
from .config import load_config


def main():
    parser = argparse.ArgumentParser(description="CPA-compatible canned-response server")
    parser.add_argument("--config", default=os.environ.get("PRANK_CONFIG"), help="YAML configuration path")
    parser.add_argument("--host", help="Override configured bind address")
    parser.add_argument("--port", type=int, help="Override configured port")
    parser.add_argument("--check-config", action="store_true", help="Validate configuration and exit")
    args = parser.parse_args()
    path = args.config or ("config.yaml" if Path("config.yaml").exists() else "config.example.yaml")
    try:
        config = load_config(path)
        app = create_app(config)
    except (ValueError, OSError, yaml.YAMLError) as exc:
        parser.exit(2, f"Configuration error: {exc}\n")
    if args.check_config:
        print(f"Configuration OK: {len(config.prank.models)} models, {len(config.prank.content_pools)} content pools")
        return
    # Access logs include query strings; CPA also accepts keys in query parameters.
    uvicorn.run(app, host=args.host if args.host is not None else config.server.host or "0.0.0.0",
                port=args.port if args.port is not None else config.server.port,
                access_log=False, server_header=False, ws_max_size=config.prank.max_request_bytes)


if __name__ == "__main__":
    main()
