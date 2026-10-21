from __future__ import annotations

import statistics
from dataclasses import dataclass

from enterprise_prd.errors import ContractError


@dataclass(frozen=True)
class PilotObservation:
    document_id: str
    participant_id: str
    quality_score: float
    revision_rounds: int
    blocker_escape: int
    cycle_minutes: float
    baseline_cycle_minutes: float
    willing_to_continue: bool


def summarize_pilot(
    baseline_scores: tuple[float, ...],
    observations: tuple[PilotObservation, ...],
) -> dict:
    if len(baseline_scores) < 2 or not observations:
        raise ContractError("pilot metrics need baseline and observations")
    scores = tuple(item.quality_score for item in observations)
    baseline_dispersion = statistics.pstdev(baseline_scores)
    pilot_dispersion = statistics.pstdev(scores) if len(scores) > 1 else 0.0
    dispersion_reduction = (
        0.0
        if baseline_dispersion == 0
        else round(
            (baseline_dispersion - pilot_dispersion) / baseline_dispersion,
            4,
        )
    )
    pass_rate = round(
        sum(item.revision_rounds <= 1 for item in observations)
        / len(observations),
        4,
    )
    blocker_escapes = sum(item.blocker_escape for item in observations)
    cycle_reductions = tuple(
        (item.baseline_cycle_minutes - item.cycle_minutes)
        / item.baseline_cycle_minutes
        for item in observations
        if item.baseline_cycle_minutes > 0
    )
    if not cycle_reductions:
        raise ContractError("pilot observations need positive baseline cycle time")
    cycle_reduction = round(statistics.median(cycle_reductions), 4)
    continuation = round(
        sum(item.willing_to_continue for item in observations)
        / len(observations),
        4,
    )
    go = (
        len(observations) >= 8
        and pass_rate >= 0.8
        and blocker_escapes == 0
        and dispersion_reduction >= 0.3
        and cycle_reduction >= 0.3
        and continuation >= 0.7
    )
    return {
        "documents": len(observations),
        "pass_within_one_revision": pass_rate,
        "blocker_escapes": blocker_escapes,
        "dispersion_reduction": dispersion_reduction,
        "cycle_reduction": cycle_reduction,
        "continuation_rate": continuation,
        "go_candidate": go,
        "interpretation": "internal_directional_only",
    }
