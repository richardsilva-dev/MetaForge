from __future__ import annotations

import random

from metaforge.candidates.models import Candidate


class CandidateGenerator:
    """Seeded candidate proposal, mutation, and crossover operators."""

    def __init__(self, seed: int = 7) -> None:
        self.random = random.Random(seed)
        self._base_population = [
            Candidate("builtin"),
            Candidate("insertion"),
            Candidate("merge"),
            Candidate("heapsort"),
            Candidate("shellsort"),
            Candidate("quicksort", "middle"),
            Candidate("quicksort", "first"),
            Candidate("quicksort", "last"),
            Candidate("quicksort", "median3"),
            Candidate("hybrid", "middle", 8),
            Candidate("hybrid", "middle", 16),
            Candidate("hybrid", "middle", 32),
            Candidate("hybrid", "median3", 8),
            Candidate("hybrid", "median3", 16),
            Candidate("hybrid", "median3", 32),
        ]

    def initial_population(self, size: int = 15) -> list[Candidate]:
        if type(size) is not int or not 2 <= size <= 32:
            raise ValueError("Population size must be 2..32")
        population = self._base_population[:size]
        while len(population) < size:
            population.append(self.mutate(self.random.choice(population)))
        return population

    def mutate(self, parent: Candidate) -> Candidate:
        alternatives = [candidate for candidate in self._base_population if candidate != parent]
        if not alternatives:
            return parent
        return self.random.choice(alternatives)

    def crossover(self, first: Candidate, second: Candidate) -> Candidate:
        strategy = self.random.choice((first.strategy, second.strategy))
        parents = (first, second)
        if strategy == "quicksort":
            pivots = [p.pivot for p in parents if p.strategy in {"quicksort", "hybrid"}]
            return Candidate("quicksort", self.random.choice(pivots or ["middle", "median3"]))
        if strategy == "hybrid":
            hybrid_parents = [p for p in parents if p.strategy == "hybrid"]
            pivots = [p.pivot for p in parents if p.strategy in {"quicksort", "hybrid"}]
            cutoffs = [p.insertion_cutoff for p in hybrid_parents]
            return Candidate(
                "hybrid",
                self.random.choice(pivots or ["middle", "median3"]),
                self.random.choice(cutoffs or [8, 16, 32]),
            )
        return Candidate(strategy)
