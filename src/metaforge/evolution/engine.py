from __future__ import annotations
import random, statistics
from dataclasses import dataclass
from typing import Any
from metaforge.candidates.generator import CandidateGenerator
from metaforge.candidates.models import Candidate
from metaforge.execution.runner import CandidateRunner

@dataclass(frozen=True, slots=True)
class Evaluation:
    candidate: Candidate
    valid: bool
    median_seconds: float | None
    median_peak_bytes: int | None
    error: str | None = None
    @property
    def score(self) -> float:
        return self.median_seconds if self.valid and self.median_seconds is not None else float("inf")
    def to_dict(self) -> dict[str,Any]:
        return {"candidate":self.candidate.to_dict(),"fingerprint":self.candidate.fingerprint,
                "valid":self.valid,"median_seconds":self.median_seconds,
                "median_peak_bytes":self.median_peak_bytes,"error":self.error}

class EvolutionEngine:
    def __init__(self,runner:CandidateRunner|None=None,seed:int=7,repeats:int=3,population_size:int=8):
        if repeats<1 or population_size<2: raise ValueError("Invalid evolution parameters")
        self.runner=runner or CandidateRunner(); self.generator=CandidateGenerator(seed)
        self.random=random.Random(seed); self.repeats=repeats; self.population_size=population_size

    def evaluate(self,candidate:Candidate,dataset:list[int])->Evaluation:
        runs=self.runner.run_repeated(candidate,dataset,self.repeats)
        expected=sorted(dataset); times=[]; peaks=[]
        for run in runs:
            if not run.success: return Evaluation(candidate,False,None,None,run.error)
            if run.output!=expected: return Evaluation(candidate,False,None,None,"Correctness check failed")
            times.append(run.elapsed_seconds); peaks.append(run.peak_bytes)
        return Evaluation(candidate,True,statistics.median(times),int(statistics.median(peaks)))

    def run(self,dataset:list[int],generations:int=3)->dict[str,Any]:
        if generations<1 or generations>20: raise ValueError("Generations must be 1..20")
        population=self.generator.initial_population(self.population_size); history=[]
        for generation in range(generations):
            unique={c.fingerprint:c for c in population}
            evaluations=[self.evaluate(c,dataset) for c in unique.values()]
            valid=sorted((e for e in evaluations if e.valid),key=lambda e:e.score)
            history.append({"generation":generation,"evaluations":[e.to_dict() for e in evaluations],
                            "valid_count":len(valid),"population_diversity":len(unique)})
            if generation<generations-1:
                parents=valid[:max(1,min(3,len(valid)))]
                if not parents: population=self.generator.initial_population(self.population_size)
                else:
                    nxt=[e.candidate for e in parents]
                    while len(nxt)<self.population_size:
                        nxt.append(self.generator.mutate(self.random.choice(parents).candidate))
                    population=nxt
        all_valid=[e for gen in history for e in gen["evaluations"] if e["valid"]]
        best=min(all_valid,key=lambda e:e["median_seconds"]) if all_valid else None
        return {"schema_version":"1.0","problem":"integer_sort","seed":7,
                "dataset_size":len(dataset),"generations":history,"best_candidate":best,
                "note":"Exploratory local microbenchmark; repeat under controlled conditions."}
