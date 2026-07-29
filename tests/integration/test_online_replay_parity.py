from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pytest

import akasha.application.cycle as cycle_module
from akasha.application.cycle import MemoryCycle
from akasha.domain.features import BurstAwareFeaturePool
from akasha.domain.graph import MEMBERSHIP
from akasha.domain.model import MemoryConfig, Turn, TurnFeedback
from akasha.infrastructure.persistence import (
    load_memory_state,
    write_memory_database,
)


def test_restored_online_growth_matches_uninterrupted_replay(
    tmp_path: Path,
) -> None:
    turns = [_turn(index) for index in range(8)]
    config = MemoryConfig()

    uninterrupted = _replay(turns, config)
    prefix = _replay(turns[:5], config)
    snapshot = tmp_path / "akasha.db"
    assert prefix.context is not None
    write_memory_database(
        snapshot,
        turns=prefix.turns,
        graph=prefix.graph,
        events=prefix.events,
        evidence=prefix.evidence,
        captures=[],
        context=prefix.context,
        burst_members=prefix.burst_members,
        config=config,
        metadata={"source_index_sha256": "fixture"},
        recalls=prefix.recalls,
    )
    (
        graph,
        events,
        evidence,
        context,
        recalls,
        burst_members,
    ) = load_memory_state(
        snapshot,
        turns=turns[:5],
        config=config,
        source_index_sha256="fixture",
    )
    restored = MemoryCycle.restore(
        config=config,
        turns=turns[:5],
        graph=graph,
        context=context,
        events=events,
        evidence=evidence,
        recalls=recalls,
        burst_members=burst_members,
    )
    for turn in turns[5:]:
        ticket = restored.retrieve(turn, capture_paths=True)
        restored.commit(turn, ticket)

    assert _cycle_state(restored) == _cycle_state(uninterrupted)
    query = _turn(8)
    restored_ticket = restored.retrieve(query, capture_paths=True)
    replay_ticket = uninterrupted.retrieve(query, capture_paths=True)
    assert restored_ticket.evidence == replay_ticket.evidence
    assert restored_ticket.completion == replay_ticket.completion
    np.testing.assert_array_equal(
        restored_ticket.diffusion.reserve,
        replay_ticket.diffusion.reserve,
    )


def test_stale_ticket_is_recomputed_on_latest_state() -> None:
    cycle = _replay([_turn(0), _turn(1)], MemoryConfig())
    stale_turn = _turn(2)
    stale = cycle.retrieve(stale_turn)
    inserted = _turn(2, suffix="inserted")
    cycle.commit(inserted, cycle.retrieve(inserted))
    delayed = _turn(3, suffix="delayed")

    committed = cycle.commit(delayed, stale)

    assert committed.retrieval_recomputed is True
    assert cycle.turns[-1].node_id == 3
    assert cycle.turns[-1].turn_id == delayed.turn_id


def test_retrieve_restores_the_published_graph_in_place() -> None:
    """Discard a speculative read frame without copying or advancing the graph."""

    cycle = _replay([_turn(0), _turn(1)], MemoryConfig())
    before = _cycle_state(cycle)
    graph_identity = id(cycle.graph)

    ticket = cycle.retrieve(_turn(2), capture_paths=True)

    assert id(cycle.graph) == graph_identity
    assert _cycle_state(cycle) == before
    assert ticket.state_version == 2
    assert ticket.prepared_turn_capacity == 3
    assert ticket.prepared_state.current_event == 2


def test_failed_retrieve_restores_the_published_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rollback the speculative capacity and clocks when readout fails."""

    cycle = _replay([_turn(0), _turn(1)], MemoryConfig())
    before = _cycle_state(cycle)

    def fail_readout(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise RuntimeError("injected readout failure")

    monkeypatch.setattr(
        cycle_module,
        "read_pattern_completion",
        fail_readout,
    )
    with pytest.raises(RuntimeError, match="injected readout failure"):
        cycle.retrieve(_turn(2), capture_paths=True)

    assert _cycle_state(cycle) == before


def test_preallocated_rebuild_matches_incremental_online_growth() -> None:
    turns = [_turn(index) for index in range(12)]
    config = MemoryConfig()
    online = _replay(turns, config)
    replay = MemoryCycle(
        config,
        turn_capacity=len(turns),
        feature_pool=BurstAwareFeaturePool(turns),
    )

    for turn in turns:
        ticket = replay.retrieve(
            turn,
            capture_paths=True,
        )
        replay.commit(turn, ticket)

    assert _cycle_state(replay) == _cycle_state(online)


def test_feedback_replay_hides_forget_and_reinforces_one_self_edge() -> None:
    """Keep forgotten turns as bridges and potentiate only the target episode."""

    turns = [_turn(index) for index in range(4)]
    neutral = _replay(turns[:3], MemoryConfig())
    turns[2] = replace(
        turns[2],
        feedback=TurnFeedback(
            remember_nodes=(1,),
            forget_nodes=(0,),
            remember_boost=3.0,
        ),
    )
    cycle = _replay(turns[:3], MemoryConfig())

    marker_evidence = cycle.evidence[2]
    assert cycle.inhibited_nodes == {0}
    assert marker_evidence == neutral.evidence[2]
    neutral_edges = [
        edge
        for edge in neutral.graph.adjacency[1]
        if neutral.graph.kind[edge] == MEMBERSHIP
        and neutral.graph.source[edge] == 1
    ]
    target_edge = max(
        neutral_edges,
        key=lambda edge: (neutral.graph.weight[edge], -edge),
    )
    changed_edges = [
        edge
        for edge, (before, after) in enumerate(
            zip(
                neutral.graph.weight,
                cycle.graph.weight,
                strict=True,
            )
        )
        if not np.isclose(before, after)
    ]
    assert changed_edges == [target_edge]
    assert cycle.graph.weight[target_edge] > neutral.graph.weight[target_edge]

    old_cue = replace(
        turns[3],
        user_text=turns[0].user_text,
        user_dense=turns[0].user_dense,
        user_terms=turns[0].user_terms,
    )
    ticket = cycle.retrieve(old_cue, capture_paths=True)
    assert 0 in dict(ticket.evidence.seed)
    assert 0 not in {
        item.node_id for item in ticket.completion.items
    }
    assert float(ticket.diffusion.reserve[0]) > 0.0


def test_feedback_online_growth_matches_preallocated_replay() -> None:
    turns = [_turn(index) for index in range(10)]
    turns[6] = replace(
        turns[6],
        feedback=TurnFeedback(
            remember_nodes=(5,),
            forget_nodes=(1,),
            remember_boost=3.0,
        ),
    )
    config = MemoryConfig()
    online = _replay(turns, config)
    replay = MemoryCycle(
        config,
        turn_capacity=len(turns),
        feature_pool=BurstAwareFeaturePool(turns),
    )

    for turn in turns:
        replay.commit(
            turn,
            replay.retrieve(
                turn,
                capture_paths=True,
                isolate_graph=False,
            ),
        )

    assert _cycle_state(replay) == _cycle_state(online)
    assert replay.inhibited_nodes == {1}


def test_turn_without_dense_still_joins_lexical_temporal_memory() -> None:
    turns = [_turn(index) for index in range(3)]
    unrelated = replace(
        turns[1],
        user_text="unrelated omega",
        assistant_text="unrelated answer",
        user_terms=(("unrelated", 1), ("omega", 1)),
        assistant_terms=(("unrelated", 1), ("answer", 1)),
    )
    missing_dense = replace(
        turns[2],
        user_dense=None,
        assistant_dense=None,
    )
    cycle = _replay(
        [turns[0], unrelated, missing_dense],
        MemoryConfig(),
    )

    assert len(cycle.turns) == 3
    assert cycle.evidence[2].channels["query_bm25"]
    assert 2 in dict(cycle.events[2].integrated)


def _replay(
    turns: list[Turn],
    config: MemoryConfig,
) -> MemoryCycle:
    cycle = MemoryCycle(config)
    for turn in turns:
        cycle.commit(turn, cycle.retrieve(turn, capture_paths=True))
    return cycle


def _cycle_state(cycle: MemoryCycle) -> dict[str, object]:
    graph = cycle.graph
    if cycle.context is None:
        raise RuntimeError("tested cycle must have committed context")
    return {
        "turns": [turn.turn_id for turn in cycle.turns],
        "source": graph.source,
        "target": graph.target,
        "kind": graph.kind,
        "weight": graph.weight,
        "last_updated": graph.last_updated,
        "observed_credit": graph.observed_credit,
        "recurrent_credit": graph.recurrent_credit,
        "resource": graph.resource,
        "threshold": graph.plasticity_threshold,
        "last_support": graph.last_support_seconds,
        "support_credit": graph.support_credit,
        "independent_credit": graph.independent_credit,
        "hubs": [asdict(hub) for hub in graph.hubs],
        "events": [asdict(event) for event in cycle.events],
        "evidence": [
            {
                **asdict(item),
                "channels": {
                    key: sorted(value)
                    for key, value in item.channels.items()
                },
            }
            for item in cycle.evidence
        ],
        "recalls": [asdict(recall) for recall in cycle.recalls],
        "burst_members": cycle.burst_members,
        "context_members": cycle.context.members,
        "elapsed_seconds": graph.elapsed_seconds,
        "recurrence": (
            graph.short_recurrence_log_gap,
            graph.long_recurrence_log_gap,
            graph.short_recurrence_log_m2,
            graph.long_recurrence_log_m2,
            graph.short_recurrence_weight,
            graph.long_recurrence_weight,
            graph.recurrence_log_mean,
            graph.recurrence_log_m2,
            graph.recurrence_weight,
        ),
        "last_external": dict(graph.last_external_seed_seconds),
        "inhibited_nodes": sorted(cycle.inhibited_nodes),
    }


def _turn(index: int, suffix: str = "") -> Turn:
    angle = index * 0.22
    vector = np.asarray(
        [np.cos(angle), np.sin(angle), 0.2 + index * 0.01],
        dtype=np.float32,
    )
    vector /= np.linalg.norm(vector)
    topic = "alpha" if index < 5 else "beta"
    text = f"{topic} shared step {index} {suffix}".strip()
    return Turn(
        node_id=index,
        turn_id=f"turn:{index}:{suffix}",
        session_key="test:one",
        user_seq=index * 2,
        user_message_id=f"user:{index}:{suffix}",
        assistant_message_id=f"assistant:{index}:{suffix}",
        started_at=f"2026-01-01T00:{index:02d}:00+00:00",
        committed_at=f"2026-01-01T00:{index:02d}:30+00:00",
        user_text=text,
        assistant_text=f"answer {topic} {index}",
        user_dense=vector,
        assistant_dense=vector,
        user_terms=tuple((term, 1) for term in text.split()),
        assistant_terms=(("answer", 1), (topic, 1)),
        inter_gap_seconds=None if index == 0 else 60.0,
    )
