from __future__ import annotations
import random
from metaforge.candidates.models import Candidate

class CandidateGenerator:
    def __init__(self, seed: int = 7) -> None:
        self.random = random.Random(seed)

    def initial_population(self, size: int = 8) -> list[Candidate]:
        base=[Candidate("builtin"), Candidate("insertion"), Candidate("merge")]
        base += [Candidate("quicksort", p) for p in ("middle","first","last","median3")]
        while len(base) < size: base.append(self.mutate(self.random.choice(base)))
        return base[:size]

    def mutate(self, parent: Candidate) -> Candidate:
        if parent.strategy != "quicksort" and self.random.random() < 0.6:
            return Candidate("quicksort", self.random.choice(("middle","first","last","median3")))
        if parent.strategy == "quicksort":
            return Candidate("quicksort", self.random.choice(("middle","first","last","median3")))
        return Candidate(self.random.choice(("builtin","insertion","merge")))
