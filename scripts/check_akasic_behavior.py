#!/usr/bin/env python3
"""Exercise the Akasic adapter's state boundary and presentation contract."""

from __future__ import annotations

import argparse
import asyncio
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np


class _Embedder:
    async def embed(self, text: str) -> list[float]:
        _ = text
        return [1.0, 0.0]


class _Runtime:
    def __init__(self, turns: list[object]) -> None:
        self.cycle = SimpleNamespace(turns=turns)

    def query_turn(self, **values: object) -> tuple[object, object]:
        timestamp = values["timestamp"]
        if not isinstance(timestamp, datetime):
            raise TypeError("timestamp fixture must be datetime")
        cue = _turn(
            3,
            "pending",
            "当前查询",
            "",
            timestamp.isoformat(),
            np.asarray([1.0, 0.0], dtype=np.float32),
        )
        completion = SimpleNamespace(
            items=(
                SimpleNamespace(
                    node_id=0,
                    score=0.8,
                    sources=("basin_completion",),
                    basin_ids=("hub:0",),
                ),
                SimpleNamespace(
                    node_id=2,
                    score=0.7,
                    sources=("basin_completion",),
                    basin_ids=("hub:0",),
                ),
            ),
            active_basin_count=1,
            pushes=12,
            residual_l1=1e-8,
        )
        ticket = SimpleNamespace(
            turn_id="pending",
            state_version=3,
            evidence=SimpleNamespace(seed=((0, 1.0),)),
            completion=completion,
        )
        return cue, ticket


def main() -> None:
    """Load the host contract and verify observable adapter behavior."""

    # 1. Import the standalone engine against the explicit host checkout.
    arguments = _parser().parse_args()
    project_root = Path(__file__).resolve().parents[1]
    host_root = arguments.host.resolve(strict=True)
    sys.path[:0] = [str(project_root / "src"), str(host_root)]

    # 2. Run the state and output contract as one deterministic scenario.
    asyncio.run(_verify())
    print(
        '{"answer_effect":"read_only","context_effect":"stateful",'
        '"dense_completion_dedup":true,"presentation":true}'
    )


async def _verify() -> None:
    from akasha.config import AkashaConfig
    from akasha.engine import AkashaMemoryEngine
    from core.memory.engine import MemoryQuery, MemoryScope

    turns = [
        _turn(
            0,
            "turn:0",
            "较早的主题",
            "甲" * 60,
            "2026-07-06T08:00:00+08:00",
            np.asarray([1.0, 0.0], dtype=np.float32),
        ),
        _turn(
            1,
            "turn:1",
            "最近的主题",
            "简短回答",
            "2026-07-07T08:00:00+08:00",
            np.asarray([0.8, 0.2], dtype=np.float32),
        ),
        _turn(
            2,
            "turn:2",
            "图补全主题",
            "补全回答",
            "2026-07-05T08:00:00+08:00",
            None,
        ),
    ]
    engine = object.__new__(AkashaMemoryEngine)
    engine._embedder = _Embedder()  # noqa: SLF001
    engine._runtime = _Runtime(turns)  # noqa: SLF001
    engine._config = AkashaConfig()  # noqa: SLF001
    engine._lock = threading.RLock()  # noqa: SLF001
    engine._pending_changed = threading.Condition(  # noqa: SLF001
        engine._lock  # noqa: SLF001
    )
    engine._commit_gate = asyncio.Lock()  # noqa: SLF001
    engine._publish_task = None  # noqa: SLF001
    engine._pending = {}  # noqa: SLF001
    timestamp = datetime(2026, 7, 27, tzinfo=timezone.utc)

    answer = await engine.query(
        MemoryQuery(
            text="主题",
            intent="answer",
            effect="stateful",
            scope=MemoryScope(session_key="test:one"),
            limit=5,
            timestamp=timestamp,
        )
    )
    assert answer.trace["effect"] == "read_only"
    assert not engine._pending  # noqa: SLF001
    assert [record.signals["lane"] for record in answer.records] == [
        "dense",
        "dense",
        "completion",
    ]
    assert any(
        record.signals["also_completed"]
        for record in answer.records
        if record.signals["lane"] == "dense"
    )

    context = await engine.query(
        MemoryQuery(
            text="主题",
            intent="context",
            effect="stateful",
            scope=MemoryScope(session_key="test:one"),
            limit=5,
            timestamp=timestamp,
            context={"turn_id": "turn:behavior"},
        )
    )
    assert context.trace["effect"] == "stateful"
    assert "test:one" in engine._pending  # noqa: SLF001
    assert "# Akasha memory now=07-27" in context.text_block
    assert "## 左脑记忆：精确回忆" in context.text_block
    assert "## 右脑联想：潜意识第一反应" in context.text_block
    assert 'assistant="' + "甲" * 50 + '..."' in context.text_block
    assert context.text_block.index("最近的主题") < context.text_block.index(
        "较早的主题"
    )
    active = engine.wait_for_active_recall(
        "test:one",
        "turn:behavior",
        timeout=0,
    )
    assert active is not None
    assert active.query_id == "pending"


def _turn(
    node_id: int,
    turn_id: str,
    user_text: str,
    assistant_text: str,
    started_at: str,
    vector: np.ndarray | None,
) -> object:
    from akasha.domain.model import Turn

    return Turn(
        node_id=node_id,
        turn_id=turn_id,
        session_key="test:one",
        user_seq=node_id * 2,
        user_message_id=f"message:{node_id * 2}",
        assistant_message_id=f"message:{node_id * 2 + 1}",
        started_at=started_at,
        committed_at=started_at,
        user_text=user_text,
        assistant_text=assistant_text,
        user_dense=vector,
        assistant_dense=vector,
        user_terms=(),
        assistant_terms=(),
        inter_gap_seconds=None if node_id == 0 else 60.0,
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
