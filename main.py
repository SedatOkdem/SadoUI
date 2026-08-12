#!/usr/bin/env python3
"""AeroShield Yer Kontrol İstasyonu entry point."""

from __future__ import annotations

import argparse
import multiprocessing
import sys
from pathlib import Path


def main() -> int:
    # Windows spawn safety
    multiprocessing.freeze_support()

    parser = argparse.ArgumentParser(description="AeroShield PyQt5 GCS")
    parser.add_argument(
        "--config",
        type=str,
        default=str(Path(__file__).resolve().parent / "config.yaml"),
        help="Path to config.yaml",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from aeroshield.app import AeroShieldApp

    app = AeroShieldApp(args.config)
    return app.run()


if __name__ == "__main__":
    raise SystemExit(main())
