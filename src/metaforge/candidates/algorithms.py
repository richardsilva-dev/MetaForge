from __future__ import annotations

from metaforge.candidates.models import Candidate


def _insertion(a: list[int]) -> list[int]:
    out = a.copy()
    for i in range(1, len(out)):
        value = out[i]
        j = i - 1
        while j >= 0 and out[j] > value:
            out[j + 1] = out[j]
            j -= 1
        out[j + 1] = value
    return out


def _merge(a: list[int]) -> list[int]:
    if len(a) < 2:
        return a.copy()
    mid = len(a) // 2
    left, right = _merge(a[:mid]), _merge(a[mid:])
    out = []
    i = j = 0
    while i < len(left) and j < len(right):
        if left[i] <= right[j]:
            out.append(left[i])
            i += 1
        else:
            out.append(right[j])
            j += 1
    return out + left[i:] + right[j:]


def _quicksort(a: list[int], policy: str, insertion_cutoff: int = 1) -> list[int]:
    # Explicit work stack prevents recursion-depth crashes on adversarial pivots.
    output: list[int] = []
    stack: list[tuple[str, list[int]]] = [("sort", a.copy())]
    while stack:
        action, values = stack.pop()
        if action == "emit":
            output.extend(values)
            continue
        if len(values) < 2:
            output.extend(values)
            continue
        if len(values) <= insertion_cutoff:
            output.extend(_insertion(values))
            continue
        if policy == "first":
            pivot = values[0]
        elif policy == "last":
            pivot = values[-1]
        elif policy == "median3":
            pivot = sorted((values[0], values[len(values) // 2], values[-1]))[1]
        else:
            pivot = values[len(values) // 2]
        lo = [x for x in values if x < pivot]
        eq = [x for x in values if x == pivot]
        hi = [x for x in values if x > pivot]
        stack.append(("sort", hi))
        stack.append(("emit", eq))
        stack.append(("sort", lo))
    return output


def _heapsort(values: list[int]) -> list[int]:
    out = values.copy()
    size = len(out)

    def sift_down(root: int, end: int) -> None:
        while (child := 2 * root + 1) < end:
            if child + 1 < end and out[child] < out[child + 1]:
                child += 1
            if out[root] >= out[child]:
                return
            out[root], out[child] = out[child], out[root]
            root = child

    for root in range(size // 2 - 1, -1, -1):
        sift_down(root, size)
    for end in range(size - 1, 0, -1):
        out[0], out[end] = out[end], out[0]
        sift_down(0, end)
    return out


def _shellsort(values: list[int]) -> list[int]:
    out = values.copy()
    gap = len(out) // 2
    while gap > 0:
        for index in range(gap, len(out)):
            value = out[index]
            cursor = index
            while cursor >= gap and out[cursor - gap] > value:
                out[cursor] = out[cursor - gap]
                cursor -= gap
            out[cursor] = value
        gap //= 2
    return out


def run_candidate(candidate: Candidate, values: list[int]) -> list[int]:
    if candidate.strategy == "builtin":
        return sorted(values)
    if candidate.strategy == "insertion":
        return _insertion(values)
    if candidate.strategy == "merge":
        return _merge(values)
    if candidate.strategy == "quicksort":
        return _quicksort(values, candidate.pivot)
    if candidate.strategy == "heapsort":
        return _heapsort(values)
    if candidate.strategy == "shellsort":
        return _shellsort(values)
    if candidate.strategy == "hybrid":
        return _quicksort(values, candidate.pivot, candidate.insertion_cutoff)
    raise ValueError("Candidate strategy is not allow-listed")
