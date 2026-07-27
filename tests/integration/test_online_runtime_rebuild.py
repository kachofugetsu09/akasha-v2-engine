from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from akasha.application.rebuild import rebuild_memory
from akasha.application.runtime import OnlineMemoryRuntime
from akasha.domain.model import MemoryConfig
from akasha.infrastructure.loader import load_turns
from akasha.infrastructure.persistence import (
    load_memory_state,
    sha256_file,
)


def test_online_commit_matches_clean_rebuild(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions.db"
    online_index = tmp_path / "online-index.db"
    online_memory = tmp_path / "online-memory.db"
    replay_memory = tmp_path / "replay-memory.db"
    _create_sessions(sessions)
    _append_turn(sessions, 0, "alpha start", [1.0, 0.0])
    _append_turn(sessions, 2, "alpha follows", [0.9, 0.1])
    config = MemoryConfig()
    runtime = OnlineMemoryRuntime(
        sessions_path=sessions,
        index_path=online_index,
        memory_path=online_memory,
        embedding_model="text-embedding-v4",
        embedding_dimension=2,
        config=config,
    )

    started = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(
        minutes=4
    )
    _, ticket = runtime.query_turn(
        text="alpha returns",
        dense=np.asarray([0.8, 0.2], dtype=np.float32),
        session_key="test:one",
        timestamp=started,
    )
    _append_turn(sessions, 4, "alpha returns", [0.8, 0.2])
    runtime.commit_from_source(
        user_message_id="message:4",
        assistant_message_id="message:5",
        ticket=ticket,
    )
    rebuild_memory(
        online_index,
        replay_memory,
        target_sequences=(),
    )

    online = _logical_state(online_memory, online_index, config)
    replay = _logical_state(replay_memory, online_index, config)
    assert online == replay
    runtime.close()


def test_online_runtime_rejects_a_second_writer(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions.db"
    index = tmp_path / "online-index.db"
    memory = tmp_path / "online-memory.db"
    _create_sessions(sessions)
    first = OnlineMemoryRuntime(
        sessions_path=sessions,
        index_path=index,
        memory_path=memory,
        embedding_model="text-embedding-v4",
        embedding_dimension=2,
        config=MemoryConfig(),
    )

    with pytest.raises(RuntimeError, match="already has a writer"):
        OnlineMemoryRuntime(
            sessions_path=sessions,
            index_path=index,
            memory_path=memory,
            embedding_model="text-embedding-v4",
            embedding_dimension=2,
            config=MemoryConfig(),
        )

    first.close()
    replacement = OnlineMemoryRuntime(
        sessions_path=sessions,
        index_path=index,
        memory_path=memory,
        embedding_model="text-embedding-v4",
        embedding_dimension=2,
        config=MemoryConfig(),
    )
    replacement.close()


def _logical_state(
    memory: Path,
    index: Path,
    config: MemoryConfig,
) -> dict[str, object]:
    turns = load_turns(index)
    graph, events, evidence, context, _, burst_members = (
        load_memory_state(
            memory,
            turns=turns,
            config=config,
            source_index_sha256=sha256_file(index),
        )
    )
    return {
        "turns": [turn.turn_id for turn in turns],
        "source": graph.source,
        "target": graph.target,
        "kind": graph.kind,
        "weights": graph.weight,
        "hubs": [asdict(hub) for hub in graph.hubs],
        "events": [asdict(event) for event in events],
        "evidence": [
            {
                **asdict(item),
                "channels": {
                    name: sorted(members)
                    for name, members in item.channels.items()
                },
            }
            for item in evidence
        ],
        "context": {
            "members": context.members,
            "dense": (
                None
                if context.dense is None
                else context.dense.tolist()
            ),
            "terms": context.terms,
        },
        "burst_members": burst_members,
    }


def _create_sessions(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE messages (
                id TEXT PRIMARY KEY,
                session_key TEXT NOT NULL,
                seq INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                extra TEXT,
                ts TEXT NOT NULL,
                UNIQUE(session_key, seq)
            );
            CREATE TABLE message_embeddings (
                message_id TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                model TEXT NOT NULL,
                embedding BLOB NOT NULL,
                dim INTEGER NOT NULL,
                PRIMARY KEY(message_id, model)
            );
            """
        )


def _append_turn(
    path: Path,
    sequence: int,
    text: str,
    raw_vector: list[float],
) -> None:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    user_time = base + timedelta(minutes=sequence)
    assistant_time = user_time + timedelta(seconds=10)
    vector = np.asarray(raw_vector, dtype=np.float32)
    with sqlite3.connect(path) as connection:
        connection.executemany(
            "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    f"message:{sequence}",
                    "test:one",
                    sequence,
                    "user",
                    text,
                    None,
                    user_time.isoformat(),
                ),
                (
                    f"message:{sequence + 1}",
                    "test:one",
                    sequence + 1,
                    "assistant",
                    f"answer {text}",
                    None,
                    assistant_time.isoformat(),
                ),
            ],
        )
        connection.executemany(
            "INSERT INTO message_embeddings VALUES (?, ?, ?, ?, ?)",
            [
                (
                    f"message:{sequence}",
                    hashlib.sha256(text.encode()).hexdigest(),
                    "text-embedding-v4",
                    vector.tobytes(),
                    vector.size,
                ),
                (
                    f"message:{sequence + 1}",
                    hashlib.sha256(
                        f"answer {text}".encode()
                    ).hexdigest(),
                    "text-embedding-v4",
                    vector.tobytes(),
                    vector.size,
                ),
            ],
        )
