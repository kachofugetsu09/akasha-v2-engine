#!/usr/bin/env python3
"""Compare persisted recall sets with the frozen Akasha V8 captures."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def main() -> None:
    """Load both artifacts, compare every target, and fail on any drift."""

    # 1. Resolve the frozen expected sets and the rebuilt actual sets.
    arguments = _parser().parse_args()
    expected = _load_expected(arguments.baseline)
    actual = _load_actual(arguments.memory_db)

    # 2. Report every set difference before enforcing exact parity.
    queries = sorted(set(expected) | set(actual))
    comparisons = {
        str(query): {
            "expected_count": len(expected.get(query, set())),
            "actual_count": len(actual.get(query, set())),
            "missing": sorted(
                expected.get(query, set()) - actual.get(query, set())
            ),
            "unexpected": sorted(
                actual.get(query, set()) - expected.get(query, set())
            ),
        }
        for query in queries
    }
    passed = all(
        not item["missing"] and not item["unexpected"]
        for item in comparisons.values()
    )
    print(
        json.dumps(
            {"exact_parity": passed, "queries": comparisons},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    if not passed:
        raise SystemExit(1)


def _load_expected(path: Path) -> dict[int, set[int]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    captures = payload["captures"]["baseline"]
    return {
        int(query): {
            int(turn["user_seq"])
            for turn in capture["turns"]
        }
        for query, capture in captures.items()
    }


def _load_actual(path: Path) -> dict[int, set[int]]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            """
            SELECT q.user_seq, candidate.user_seq
            FROM recall_items AS item
            JOIN turn_nodes AS q
              ON q.node_id = item.query_turn_node_id
            JOIN turn_nodes AS candidate
              ON candidate.node_id = item.candidate_turn_node_id
            ORDER BY q.user_seq, candidate.user_seq
            """
        ).fetchall()
    finally:
        connection.close()
    result: dict[int, set[int]] = {}
    for query, candidate in rows:
        result.setdefault(int(query), set()).add(int(candidate))
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--memory-db", type=Path, required=True)
    return parser


if __name__ == "__main__":
    main()
