from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json

STRATEGIES = {"builtin", "insertion", "merge", "quicksort", "heapsort", "shellsort", "hybrid"}
PIVOTS = {"middle", "first", "last", "median3"}


@dataclass(frozen=True, slots=True)
class Candidate:
    strategy: str
    pivot: str = "middle"
    insertion_cutoff: int = 16

    def __post_init__(self) -> None:
        if self.strategy not in STRATEGIES:
            raise ValueError("Strategy is not allow-listed")
        if self.strategy in {"quicksort", "hybrid"}:
            if self.pivot not in PIVOTS:
                raise ValueError("Pivot is not allow-listed")
        elif self.pivot != "middle":
            raise ValueError("Pivot applies only to quicksort and hybrid")
        if self.strategy == "hybrid":
            if type(self.insertion_cutoff) is not int or not 4 <= self.insertion_cutoff <= 64:
                raise ValueError("Hybrid insertion_cutoff must be 4..64")
        elif self.insertion_cutoff != 16:
            raise ValueError("insertion_cutoff applies only to hybrid")

    @property
    def fingerprint(self) -> str:
        raw = json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()

    def to_dict(self) -> dict[str, str | int]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "Candidate":
        if not isinstance(value, dict) or set(value) - {"strategy", "pivot", "insertion_cutoff"}:
            raise ValueError("Invalid candidate descriptor")
        return cls(**value)
