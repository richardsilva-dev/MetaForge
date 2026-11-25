from __future__ import annotations

from collections.abc import Sequence


INTEGER_SORT_SUITE_NAME = "integer-sort-standard"
INTEGER_SORT_SUITE_VERSION = "1.0"
INTEGER_SORT_WORKLOAD_NAMES = (
    "input",
    "ascending",
    "descending",
    "nearly_sorted",
    "duplicate_heavy",
    "low_cardinality",
    "organ_pipe",
    "all_equal",
)


def build_integer_sort_workloads(dataset: Sequence[int]) -> dict[str, list[int]]:
    """Build a deterministic, fixed-size workload suite from one source dataset.

    Each workload contains exactly len(dataset) signed integers. The suite
    intentionally covers distinct input orderings and cardinality profiles so
    selection is not based on a single favorable distribution.
    """
    if isinstance(dataset, (str, bytes)) or not isinstance(dataset, Sequence):
        raise TypeError("dataset must be an integer sequence")
    values = list(dataset)
    if any(type(value) is not int or not -(2**63) <= value < 2**63 for value in values):
        raise ValueError("dataset must contain signed 64-bit integers")

    ordered = sorted(values)
    nearly_sorted = ordered.copy()
    if len(nearly_sorted) > 1:
        swap_count = min(100, max(1, len(nearly_sorted) // 100))
        stride = max(1, (len(nearly_sorted) - 1) // swap_count)
        for index in range(0, len(nearly_sorted) - 1, stride):
            if index // stride >= swap_count:
                break
            nearly_sorted[index], nearly_sorted[index + 1] = (
                nearly_sorted[index + 1],
                nearly_sorted[index],
            )

    midpoint = len(ordered) // 2
    organ_pipe = ordered[:midpoint] + list(reversed(ordered[midpoint:]))
    duplicate_heavy = [(value % 31) - 15 for value in values]
    low_cardinality = [value % 4 for value in values]
    equal_value = values[0] if values else 0

    workloads = {
        "input": values,
        "ascending": ordered,
        "descending": list(reversed(ordered)),
        "nearly_sorted": nearly_sorted,
        "duplicate_heavy": duplicate_heavy,
        "low_cardinality": low_cardinality,
        "organ_pipe": organ_pipe,
        "all_equal": [equal_value] * len(values),
    }
    if tuple(workloads) != INTEGER_SORT_WORKLOAD_NAMES:
        raise RuntimeError("Integer-sort workload suite order is inconsistent")
    return workloads
