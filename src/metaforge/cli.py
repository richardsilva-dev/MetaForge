from __future__ import annotations
import argparse, random
from pathlib import Path
from metaforge.evolution.engine import EvolutionEngine
from metaforge.reporting.markdown import render_report
from metaforge.storage.json_store import save_run

def main()->int:
    parser=argparse.ArgumentParser(prog="metaforge")
    sub=parser.add_subparsers(dest="command",required=True)
    run=sub.add_parser("run",help="run a controlled integer sorting experiment")
    run.add_argument("--size",type=int,default=200)
    run.add_argument("--generations",type=int,default=3)
    run.add_argument("--seed",type=int,default=7)
    run.add_argument("--max-evaluations",type=int,default=64)
    run.add_argument("--max-workers",type=int,default=2)
    run.add_argument("--global-timeout",type=float,default=120.0)
    run.add_argument("--output",type=Path,default=Path(".artifacts/latest.json"))
    args=parser.parse_args()
    if not 1<=args.size<=100_000: parser.error("--size must be 1..100000")
    if not 1<=args.generations<=20: parser.error("--generations must be 1..20")
    rng=random.Random(args.seed)
    dataset=[rng.randint(-10_000,10_000) for _ in range(args.size)]
    result=EvolutionEngine(seed=args.seed,max_evaluations=args.max_evaluations,\n        max_workers=args.max_workers,global_timeout_seconds=args.global_timeout).run(dataset,args.generations)
    result["seed"]=args.seed
    save_run(result,args.output)
    report_path=args.output.with_suffix(".md")
    report_path.write_text(render_report(result),encoding="utf-8")
    print(f"JSON: {args.output}\nReport: {report_path}")
    if result["best_candidate"]:
        print("Best:",result["best_candidate"]["candidate"])
    return 0

if __name__=="__main__": raise SystemExit(main())
