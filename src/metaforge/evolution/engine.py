from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
import random, statistics, time
from dataclasses import dataclass
from typing import Any
from metaforge.agents.orchestrator import AgentOrchestrator
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
    def __init__(self,runner:CandidateRunner|None=None,seed:int=7,repeats:int=3,
                 population_size:int=8,max_evaluations:int=64,max_workers:int=2,
                 global_timeout_seconds:float=120.0):
        if not 1<=repeats<=20 or not 2<=population_size<=32: raise ValueError("repeats must be 1..20 and population_size 2..32")
        if not 1<=max_evaluations<=1000: raise ValueError("max_evaluations must be 1..1000")
        if not 1<=max_workers<=8: raise ValueError("max_workers must be 1..8")
        if global_timeout_seconds<=0: raise ValueError("global_timeout_seconds must be positive")
        self.runner=runner or CandidateRunner(); self.generator=CandidateGenerator(seed)
        self.agents=AgentOrchestrator(seed); self.random=random.Random(seed); self.seed=seed
        self.repeats=repeats; self.population_size=population_size
        self.max_evaluations=max_evaluations; self.max_workers=max_workers
        self.global_timeout_seconds=global_timeout_seconds

    def evaluate(self,candidate:Candidate,dataset:list[int],timeout_seconds:float|None=None)->Evaluation:
        runs=self.runner.run_repeated(candidate,dataset,self.repeats,timeout_seconds)
        expected=sorted(dataset); times=[]; peaks=[]
        for run in runs:
            if not run.success: return Evaluation(candidate,False,None,None,run.error)
            if run.output!=expected: return Evaluation(candidate,False,None,None,"Correctness check failed")
            times.append(run.elapsed_seconds); peaks.append(run.peak_bytes)
        return Evaluation(candidate,True,statistics.median(times),int(statistics.median(peaks)))

    def run(self,dataset:list[int],generations:int=3)->dict[str,Any]:
        if generations<1 or generations>20: raise ValueError("Generations must be 1..20")
        deadline=time.monotonic()+self.global_timeout_seconds
        population=self.agents.design_population(self.population_size,"generation-0-design")
        history=[]; evaluations_used=0; stop_reason="generation_limit"
        for generation in range(generations):
            unique={c.fingerprint:c for c in population}
            candidates=list(unique.values())
            evaluations:list[Evaluation]=[]
            index=0
            while index<len(candidates) and evaluations_used<self.max_evaluations:
                remaining=deadline-time.monotonic()
                if remaining<=0:
                    stop_reason="global_timeout"; break
                batch=candidates[index:index+self.max_workers]
                batch=batch[:self.max_evaluations-evaluations_used]
                futures={}
                with ThreadPoolExecutor(max_workers=len(batch)) as pool:
                    for candidate in batch:
                        futures[pool.submit(self.evaluate,candidate,dataset,remaining)]=candidate
                    try:
                        for future in as_completed(futures,timeout=remaining):
                            candidate=futures[future]
                            try: evaluations.append(future.result())
                            except Exception as exc:
                                evaluations.append(Evaluation(candidate,False,None,None,
                                    f"Evaluation worker failed: {type(exc).__name__}: {exc}"))
                    except FuturesTimeout:
                        stop_reason="global_timeout"
                        for future,candidate in futures.items():
                            if not future.done():
                                future.cancel()
                                evaluations.append(Evaluation(candidate,False,None,None,"Global time budget exceeded"))
                evaluations_used+=len(batch); index+=len(batch)
                if stop_reason=="global_timeout": break
            valid=sorted((e for e in evaluations if e.valid),key=lambda e:e.score)
            records=[e.to_dict() for e in evaluations]
            analysis=self.agents.analyze(records,f"generation-{generation}-analysis")
            history.append({"generation":generation,"evaluations":records,
                            "valid_count":len(valid),"population_diversity":len(unique),
                            "agent_analysis":analysis})
            if stop_reason=="global_timeout": break
            if evaluations_used>=self.max_evaluations:
                stop_reason="evaluation_budget_exhausted"; break
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
        return {"schema_version":"1.0","problem":"integer_sort","seed":self.seed,
                "dataset_size":len(dataset),"generations":history,"best_candidate":best,
                "budget":{"max_evaluations":self.max_evaluations,"evaluations_used":evaluations_used,
                          "max_workers":self.max_workers,"global_timeout_seconds":self.global_timeout_seconds,
                          "stop_reason":stop_reason},
                "note":"Exploratory local microbenchmark; repeat under controlled conditions."}
