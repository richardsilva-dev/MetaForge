from __future__ import annotations
from dataclasses import asdict, dataclass
import hashlib
import json

STRATEGIES = {"builtin", "insertion", "merge", "quicksort"}
PIVOTS = {"middle", "first", "last", "median3"}

@dataclass(frozen=True, slots=True)
class Candidate:
    strategy: str
    pivot: str = "middle"

    def __post_init__(self) -> None:
        if self.strategy not in STRATEGIES:
            raise ValueError("Strategy is not allow-listed")
        if self.strategy == "quicksort" and self.pivot not in PIVOTS:
            raise ValueError("Pivot is not allow-listed")
        if self.strategy != "quicksort" and self.pivot != "middle":
            raise ValueError("Pivot applies only to quicksort")

    @property
    def fingerprint(self) -> str:
        raw = json.dumps(asdict(self), sort_keys=True).encode()
        return hashlib.sha256(raw).hexdigest()

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "Candidate":
        if not isinstance(value, dict) or set(value) - {"strategy", "pivot"}:
            raise ValueError("Invalid candidate descriptor")
        return cls(**value)
