#!/usr/bin/env python3
"""Export persisted Akasha recall traces with canonical source messages."""

from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path


def main() -> None:
    """Resolve recall bindings and atomically write a readable report."""

    # 1. Read derived recall rows and canonical source messages.
    arguments = _parser().parse_args()
    recalls = _load_recalls(arguments.memory_db)
    messages = _load_messages(
        arguments.sessions_db,
        {
            message_id
            for recall in recalls
            for message_id in (
                recall["query_user_message_id"],
                recall["user_message_id"],
                recall["assistant_message_id"],
            )
        },
    )

    # 2. Render private content only to the caller-selected output.
    report = _render(
        recalls,
        messages,
        arguments.assistant_chars,
    )
    _atomic_write(arguments.output, report)


def _load_recalls(path: Path) -> list[dict[str, object]]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT q.user_seq AS query_seq,
                   q.user_message_id AS query_user_message_id,
                   i.rank,
                   i.score,
                   i.sources_json,
                   t.user_seq,
                   t.user_message_id,
                   t.assistant_message_id
            FROM recall_items AS i
            JOIN turn_nodes AS q
              ON q.node_id = i.query_turn_node_id
            JOIN turn_nodes AS t
              ON t.node_id = i.candidate_turn_node_id
            ORDER BY i.query_turn_node_id, i.rank
            """
        ).fetchall()
    finally:
        connection.close()
    if not rows:
        raise ValueError("memory database contains no persisted recall items")
    return [dict(row) for row in rows]


def _load_messages(
    path: Path,
    message_ids: set[object],
) -> dict[str, str]:
    ordered = sorted(str(message_id) for message_id in message_ids)
    placeholders = ",".join("?" for _ in ordered)
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            f"SELECT id, content FROM messages WHERE id IN ({placeholders}) "
            "ORDER BY id",
            ordered,
        ).fetchall()
    finally:
        connection.close()
    result = {str(message_id): str(content or "") for message_id, content in rows}
    missing = sorted(set(ordered) - result.keys())
    if missing:
        raise ValueError(f"sessions database is missing messages: {missing[:3]}")
    return result


def _render(
    recalls: list[dict[str, object]],
    messages: dict[str, str],
    assistant_chars: int,
) -> str:
    lines = ["# Akasha V2 重放召回明细", ""]
    current_query: int | None = None
    for row in recalls:
        query_seq = int(row["query_seq"])
        if query_seq != current_query:
            current_query = query_seq
            query_id = str(row["query_user_message_id"])
            lines.extend(
                (
                    f"## Query `{query_seq}`",
                    "",
                    f"> {messages[query_id]}",
                    "",
                )
            )
        user = messages[str(row["user_message_id"])]
        assistant = _truncate(
            messages[str(row["assistant_message_id"])],
            assistant_chars,
        )
        lines.extend(
            (
                (
                    f"### [ ] {row['rank']}. seq `{row['user_seq']}` "
                    f"· score `{float(row['score']):.8f}`"
                ),
                "",
                f"- 来源：`{row['sources_json']}`",
                f"- 用户：{user}",
                f"- 助手：{assistant}",
                "",
            )
        )
    return "\n".join(lines)


def _truncate(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[:limit] + "…"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--memory-db", type=Path, required=True)
    parser.add_argument("--sessions-db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--assistant-chars", type=int, default=50)
    return parser


if __name__ == "__main__":
    main()
