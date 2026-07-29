"""Application entry point.

The UI callbacks and pipeline services are intentionally kept out of this
module; this file only validates configuration and launches the application.
"""

from __future__ import annotations

from app_config import (
    ASSETS_DIR,
    DEBUG_ENGINE_ROOT,
    DEFAULT_CONCURRENCY_LIMIT,
    EMBODICHAIN_ROOT,
    SERVER_NAME,
    SERVER_PORT,
)
from app_services import build_demo


def main() -> None:
    if not EMBODICHAIN_ROOT.is_dir():
        raise FileNotFoundError(f"EmbodiChain root not found: {EMBODICHAIN_ROOT}")
    demo = build_demo()
    demo.queue(default_concurrency_limit=DEFAULT_CONCURRENCY_LIMIT)
    demo.launch(
        server_name=SERVER_NAME,
        server_port=SERVER_PORT,
        allowed_paths=[
            str(EMBODICHAIN_ROOT),
            str(ASSETS_DIR),
            str(DEBUG_ENGINE_ROOT),
        ],
    )


if __name__ == "__main__":
    main()
