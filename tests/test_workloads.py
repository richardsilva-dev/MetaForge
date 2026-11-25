import pytest

from metaforge.evolution.workloads import (
    INTEGER_SORT_WORKLOAD_NAMES,
    build_integer_sort_workloads,
)


@pytest.mark.parametrize("dataset", [
    [],
    [1],
    [3, 1],
    [5, 5, 5, 5],
    [-(2**63), 0, 2**63 - 1, -1, 1],
    list(range(500)),
])
def test_workload_suite_is_deterministic_and_preserves_cardinality(dataset):
    first = build_integer_sort_workloads(dataset)
    second = build_integer_sort_workloads(dataset)

    assert tuple(first) == INTEGER_SORT_WORKLOAD_NAMES
    assert first == second
    assert all(len(values) == len(dataset) for values in first.values())
    assert all(all(type(value) is int for value in values) for values in first.values())


def test_workload_suite_covers_ordering_and_cardinality_profiles():
    dataset = [9, 1, 8, 2, 7, 3, 6, 4, 5, 0]
    workloads = build_integer_sort_workloads(dataset)

    assert workloads["input"] == dataset
    assert workloads["ascending"] == sorted(dataset)
    assert workloads["descending"] == sorted(dataset, reverse=True)
    assert workloads["duplicate_heavy"] == [(value % 31) - 15 for value in dataset]
    assert workloads["low_cardinality"] == [value % 4 for value in dataset]
    assert len(set(workloads["all_equal"])) <= 1
    assert sorted(workloads["organ_pipe"]) == sorted(dataset)


def test_workload_builder_rejects_non_integer_and_out_of_range_values():
    with pytest.raises(ValueError):
        build_integer_sort_workloads([1, True, 3])
    with pytest.raises(ValueError):
        build_integer_sort_workloads([2**80])
    with pytest.raises(TypeError):
        build_integer_sort_workloads("not-a-dataset")
