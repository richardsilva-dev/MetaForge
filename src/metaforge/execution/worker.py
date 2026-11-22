from __future__ import annotations
import json, sys, time, tracemalloc
from metaforge.candidates.algorithms import run_candidate
from metaforge.candidates.models import Candidate

def main() -> int:
    try:
        request=json.load(sys.stdin)
        candidate=Candidate.from_dict(request["candidate"])
        values=request["values"]
        repeats=request.get("repeats", 1)
        if not isinstance(values,list) or len(values)>100_000: raise ValueError("Input limit exceeded")
        if any(type(x) is not int for x in values): raise ValueError("Only integer inputs are accepted")
        if type(repeats) is not int or not 1 <= repeats <= 20: raise ValueError("Repeats must be 1..20")
        results=[]
        for _ in range(repeats):
            tracemalloc.start(); start=time.perf_counter()
            output=run_candidate(candidate, values)
            elapsed=time.perf_counter()-start
            _,peak=tracemalloc.get_traced_memory(); tracemalloc.stop()
            results.append({"output":output,"elapsed_seconds":elapsed,"peak_bytes":peak})
        json.dump({"ok":True,"results":results},sys.stdout); return 0
    except Exception as exc:
        if tracemalloc.is_tracing(): tracemalloc.stop()
        json.dump({"ok":False,"error":f"{type(exc).__name__}: {exc}"},sys.stdout); return 2

if __name__ == "__main__": raise SystemExit(main())
