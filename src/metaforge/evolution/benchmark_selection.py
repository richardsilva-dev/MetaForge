from __future__ import annotations

import math
import statistics
from typing import Any


def median_absolute_deviation(samples: list[float] | tuple[float, ...]) -> float | None:
    if not samples or any(
        type(value) not in (int, float) or not math.isfinite(value) or value < 0
        for value in samples
    ):
        return None
    center = statistics.median(samples)
    return statistics.median(abs(value - center) for value in samples)


def passes_baseline_gate(
    candidate_workloads: dict[str, dict[str, Any]],
    baseline_workloads: dict[str, dict[str, Any]],
    minimum_improvement_ratio: float,
    maximum_workload_regression_ratio: float = 0.02,
) -> bool:
    """Require aggregate improvement and prevent material per-workload regressions."""
    if (
        not candidate_workloads
        or candidate_workloads.keys() != baseline_workloads.keys()
        or not 0 <= minimum_improvement_ratio < 1
        or not 0 <= maximum_workload_regression_ratio < 1
    ):
        return False

    candidate_medians: list[float] = []
    baseline_medians: list[float] = []
    candidate_mads: list[float] = []
    baseline_mads: list[float] = []
    for name in baseline_workloads:
        candidate = candidate_workloads[name]
        baseline = baseline_workloads[name]
        if not candidate.get("correctness_passed") or not baseline.get("correctness_passed"):
            return False
        candidate_median = candidate.get("median_algorithm_seconds")
        baseline_median = baseline.get("median_algorithm_seconds")
        candidate_samples = candidate.get("algorithm_samples_seconds")
        baseline_samples = baseline.get("algorithm_samples_seconds")
        candidate_mad = median_absolute_deviation(candidate_samples) if isinstance(candidate_samples, list) else None
        baseline_mad = median_absolute_deviation(baseline_samples) if isinstance(baseline_samples, list) else None
        if (
            type(candidate_median) not in (int, float)
            or type(baseline_median) not in (int, float)
            or not math.isfinite(candidate_median)
            or not math.isfinite(baseline_median)
            or candidate_median < 0
            or baseline_median <= 0
            or not isinstance(candidate_samples, list)
            or not isinstance(baseline_samples, list)
            or candidate_mad is None
            or baseline_mad is None
        ):
            return False
        if candidate_median > baseline_median * (1 + maximum_workload_regression_ratio):
            return False
        candidate_medians.append(candidate_median)
        baseline_medians.append(baseline_median)
        candidate_mads.append(candidate_mad)
        baseline_mads.append(baseline_mad)

    candidate_score = statistics.median(candidate_medians)
    baseline_score = statistics.median(baseline_medians)
    candidate_spread = statistics.median(candidate_mads)
    baseline_spread = statistics.median(baseline_mads)
    # The aggregate gain must clear both the required margin and observed robust spread.
    return (
        candidate_score <= baseline_score * (1 - minimum_improvement_ratio)
        and candidate_score + candidate_spread < baseline_score - baseline_spread
    )
