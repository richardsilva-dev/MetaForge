from __future__ import annotations
from metaforge.candidates.models import Candidate

def _insertion(a: list[int]) -> list[int]:
    out = a.copy()
    for i in range(1, len(out)):
        value = out[i]; j = i - 1
        while j >= 0 and out[j] > value:
            out[j + 1] = out[j]; j -= 1
        out[j + 1] = value
    return out

def _merge(a: list[int]) -> list[int]:
    if len(a) < 2: return a.copy()
    mid = len(a) // 2
    left, right = _merge(a[:mid]), _merge(a[mid:])
    out=[]; i=j=0
    while i < len(left) and j < len(right):
        if left[i] <= right[j]: out.append(left[i]); i += 1
        else: out.append(right[j]); j += 1
    return out + left[i:] + right[j:]

def _quicksort(a: list[int], policy: str) -> list[int]:
    if len(a) < 2: return a.copy()
    if policy == "first": pivot=a[0]
    elif policy == "last": pivot=a[-1]
    elif policy == "median3": pivot=sorted((a[0], a[len(a)//2], a[-1]))[1]
    else: pivot=a[len(a)//2]
    lo=[x for x in a if x < pivot]
    eq=[x for x in a if x == pivot]
    hi=[x for x in a if x > pivot]
    return _quicksort(lo, policy) + eq + _quicksort(hi, policy)

def run_candidate(candidate: Candidate, values: list[int]) -> list[int]:
    if candidate.strategy == "builtin": return sorted(values)
    if candidate.strategy == "insertion": return _insertion(values)
    if candidate.strategy == "merge": return _merge(values)
    if candidate.strategy == "quicksort": return _quicksort(values, candidate.pivot)
    raise ValueError("Candidate strategy is not allow-listed")
