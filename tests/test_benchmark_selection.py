from metaforge.evolution.benchmark_selection import (
    median_absolute_deviation,
    passes_baseline_gate,
)


def _workload(median, samples):
    return {
        "correctness_passed": True,
        "median_algorithm_seconds": median,
        "algorithm_samples_seconds": samples,
    }


def test_median_absolute_deviation_is_robust_and_defined():
    assert median_absolute_deviation([1.0, 1.0, 1.0, 100.0]) == 0.0
    assert median_absolute_deviation([]) is None
    assert median_absolute_deviation([1.0, float("nan")]) is None


def test_baseline_gate_requires_aggregate_gain_and_no_material_workload_regression():
    baseline = {
        "small": _workload(1.0, [1.0, 1.0, 1.0, 1.0, 1.0]),
        "large": _workload(10.0, [10.0, 10.0, 10.0, 10.0, 10.0]),
    }
    improved = {
        "small": _workload(0.8, [0.8, 0.8, 0.8, 0.8, 0.8]),
        "large": _workload(8.0, [8.0, 8.0, 8.0, 8.0, 8.0]),
    }
    regression = {
        "small": _workload(0.1, [0.1, 0.1, 0.1, 0.1, 0.1]),
        "large": _workload(11.0, [11.0, 11.0, 11.0, 11.0, 11.0]),
    }
    assert passes_baseline_gate(improved, baseline, 0.05)
    assert not passes_baseline_gate(regression, baseline, 0.05)


def test_baseline_gate_rejects_gain_with_overlapping_robust_spread():
    baseline = {"main": _workload(1.0, [0.8, 1.0, 1.2, 0.8, 1.2])}
    candidate = {"main": _workload(0.95, [0.75, 0.95, 1.15, 0.75, 1.15])}
    assert not passes_baseline_gate(candidate, baseline, 0.05)


def test_baseline_gate_fails_closed_on_missing_or_invalid_workloads():
    baseline = {"main": _workload(1.0, [1.0, 1.0, 1.0, 1.0, 1.0])}
    assert not passes_baseline_gate({}, baseline, 0.05)
    assert not passes_baseline_gate(
        {"main": _workload(0.5, [0.5, 0.5, 0.5, 0.5, 0.5]), "extra": _workload(0.5, [0.5])},
        baseline,
        0.05,
    )
    invalid = {"main": _workload(float("nan"), [0.5, 0.5, 0.5])}
    assert not passes_baseline_gate(invalid, baseline, 0.05)


def test_baseline_gate_rejects_fewer_than_five_samples():
    baseline = {"main": _workload(1.0, [1.0, 1.0, 1.0])}
    candidate = {"main": _workload(0.5, [0.5, 0.5, 0.5])}
    assert not passes_baseline_gate(candidate, baseline, 0.05)
