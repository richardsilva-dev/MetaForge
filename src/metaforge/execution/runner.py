from __future__ import annotations
import json, os, subprocess, sys
from dataclasses import dataclass
from pathlib import Path
from metaforge.candidates.models import Candidate

@dataclass(frozen=True, slots=True)
class ExecutionResult:
    success: bool
    output: list[int] | None
    elapsed_seconds: float | None
    peak_bytes: int | None
    error: str | None = None

class CandidateRunner:
    """Runs trusted allow-listed code in a worker process; not a hostile-code sandbox."""
    def __init__(self, timeout_seconds: float=3.0, max_input_items: int=100_000,
                 max_output_bytes: int=2_000_000) -> None:
        if timeout_seconds<=0 or max_input_items<1 or max_output_bytes<256:
            raise ValueError("Runner limits must be positive")
        self.timeout_seconds=timeout_seconds; self.max_input_items=max_input_items
        self.max_output_bytes=max_output_bytes

    def run_repeated(self, candidate: Candidate, values: list[int], repeats: int=1) -> list[ExecutionResult]:
        if type(repeats) is not int or not 1<=repeats<=20:
            return [ExecutionResult(False,None,None,None,"Repeats must be 1..20")]
        if len(values)>self.max_input_items:
            return [ExecutionResult(False,None,None,None,"Input limit exceeded")]
        if any(type(x) is not int for x in values):
            return [ExecutionResult(False,None,None,None,"Only integer inputs are accepted")]
        payload=json.dumps({"candidate":candidate.to_dict(),"values":values,"repeats":repeats})
        root=Path(__file__).resolve().parents[2]
        env={"PATH":os.environ.get("PATH",""),"PYTHONIOENCODING":"utf-8","PYTHONHASHSEED":"0"}
        command=[sys.executable,"-I","-c",
                 "import sys; sys.path.insert(0,sys.argv[1]); from metaforge.execution.worker import main; raise SystemExit(main())",
                 str(root)]
        try:
            proc=subprocess.run(command,input=payload,text=True,capture_output=True,
                timeout=self.timeout_seconds,env=env,cwd=root,check=False)
        except subprocess.TimeoutExpired:
            return [ExecutionResult(False,None,None,None,"Execution timed out")]
        if len(proc.stdout.encode("utf-8"))>self.max_output_bytes:
            return [ExecutionResult(False,None,None,None,"Output limit exceeded")]
        try: result=json.loads(proc.stdout)
        except json.JSONDecodeError:
            return [ExecutionResult(False,None,None,None,"Worker returned invalid output")]
        if proc.returncode or not result.get("ok"):
            return [ExecutionResult(False,None,None,None,result.get("error","Worker failed"))]
        return [ExecutionResult(True,item["output"],item["elapsed_seconds"],item["peak_bytes"])
                for item in result["results"]]

    def run(self,candidate:Candidate,values:list[int])->ExecutionResult:
        return self.run_repeated(candidate,values,1)[0]
