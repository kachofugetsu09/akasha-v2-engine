#!/usr/bin/env python3
"""Check the adapter against one Akasic Agent source checkout."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> None:
    """Load both source trees and enforce their runtime protocols."""

    # 1. Resolve the two explicit source boundaries before importing adapters.
    arguments = _parser().parse_args()
    project_root = Path(__file__).resolve().parents[1]
    host_root = arguments.host.resolve(strict=True)
    sys.path[:0] = [str(project_root / "src"), str(host_root)]

    # 2. Check structural contracts without constructing network resources.
    from akasha.engine import AkashaMemoryEngine
    from akasha.memory_plugin import MemoryPlugin as AkashaPlugin
    from core.memory.engine import MemoryEngine
    from core.memory.plugin import MemoryPlugin

    engine = object.__new__(AkashaMemoryEngine)
    plugin = AkashaPlugin()
    if not isinstance(engine, MemoryEngine):
        raise TypeError("AkashaMemoryEngine violates MemoryEngine")
    if not isinstance(plugin, MemoryPlugin):
        raise TypeError("Akasha MemoryPlugin violates host protocol")
    print(
        json.dumps(
            {
                "engine_contract": True,
                "plugin_contract": True,
                "plugin_id": plugin.plugin_id,
            },
            sort_keys=True,
        )
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--host",
        type=Path,
        required=True,
        help="Path to an Akasic Agent source checkout.",
    )
    return parser


if __name__ == "__main__":
    main()
