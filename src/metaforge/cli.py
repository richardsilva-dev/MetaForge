from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
from pathlib import Path
import statistics
from typing import Any
from uuid import uuid4

from metaforge.evolution.engine import EvolutionEngine
from metaforge.evolution.metadata import runtime_metadata
from metaforge.candidates.models import Candidate
from metaforge.backends.python import PythonBackend
from metaforge.reporting.markdown import render_report
from metaforge.storage.json_store import archive_run, save_run


def _persist(result: dict[str, Any], output: Path) -> None:
    save_run(result, output)
    archive_path = archive_run(result, output.parent / "runs")
    report = render_report(result)
    output.with_suffix(".md").write_text(report, encoding="utf-8")
    archive_path.with_suffix(".md").write_text(report, encoding="utf-8")
    print(f"JSON: {output}")
    print(f"Archive: {archive_path}")
    print(f"Report: {output.with_suffix('.md')}")


def _dataset(seed: int, size: int) -> list[int]:
    rng = random.Random(seed)
    return [rng.randint(-10_000, 10_000) for _ in range(size)]


def _candidate_signature(result: dict[str, Any]) -> list[tuple[str, bool]]:
    return [
        (item["fingerprint"], item["valid"])
        for generation in result["generations"]
        for item in generation["evaluations"]
    ]


def main() -> int:
    parser = argparse.ArgumentParser(prog="metaforge")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run a controlled integer sorting experiment")
    run.add_argument("--size", type=int, default=200)
    run.add_argument("--generations", type=int, default=3)
    run.add_argument("--seed", type=int, default=7)
    run.add_argument("--repeats", type=int, default=3)
    run.add_argument("--population-size", type=int, default=15)
    run.add_argument("--max-evaluations", type=int, default=64)
    run.add_argument("--max-workers", type=int, default=2)
    run.add_argument("--global-timeout", type=float, default=120.0)
    run.add_argument("--llm-proposals", action="store_true", help="use an optional OpenAI Agents SDK candidate designer")
    run.add_argument("--llm-model", default=os.environ.get("METAFORGE_AGENT_MODEL"))
    run.add_argument("--llm-timeout", type=float, default=25.0)
    run.add_argument("--output", type=Path, default=Path(".artifacts/latest.json"))

    backend_check = sub.add_parser("backend-check", help="compare correctness across Python and compiled backends")
    backend_check.add_argument("--backend", choices=["rust", "c", "both"], default="both")
    backend_check.add_argument("--strategy", choices=["builtin", "heapsort", "shellsort"], default="heapsort")
    backend_check.add_argument("--size", type=int, default=1000)
    backend_check.add_argument("--seed", type=int, default=7)
    backend_check.add_argument("--repeats", type=int, default=3)
    backend_check.add_argument("--timeout", type=float, default=10.0)
    backend_check.add_argument("--output", type=Path, default=Path(".artifacts/backend-check.json"))

    sandbox_check = sub.add_parser("sandbox-check", help="verify a rootless runtime and pinned sandbox image without executing code")
    sandbox_check.add_argument("--runtime", choices=["podman", "docker"], default=os.environ.get("METAFORGE_SANDBOX_RUNTIME", "podman"))
    sandbox_check.add_argument("--image", default=os.environ.get("METAFORGE_SANDBOX_IMAGE"))
    sandbox_smoke = sub.add_parser("sandbox-smoke", help="run a fixed trusted isolation probe inside the configured sandbox")
    sandbox_smoke.add_argument("--runtime", choices=["podman", "docker"], default=os.environ.get("METAFORGE_SANDBOX_RUNTIME", "podman"))
    sandbox_smoke.add_argument("--image", default=os.environ.get("METAFORGE_SANDBOX_IMAGE"))

    replay = sub.add_parser("replay", help="re-run an archived experiment using its stored dataset")
    replay.add_argument("--run", type=Path, required=True, help="path to a prior experiment JSON")
    replay.add_argument("--output", type=Path, default=Path(".artifacts/replay-latest.json"))

    args = parser.parse_args()

    if args.command == "run":
        if not 1 <= args.size <= 100_000:
            parser.error("--size must be 1..100000")
        if not 1 <= args.generations <= 20:
            parser.error("--generations must be 1..20")
        candidate_designer = None
        if args.llm_proposals:
            if not os.environ.get("OPENAI_API_KEY"):
                parser.error("--llm-proposals requires OPENAI_API_KEY in the environment")
            if not args.llm_model:
                parser.error("set --llm-model or METAFORGE_AGENT_MODEL when using --llm-proposals")
            try:
                from metaforge.agents.openai_adapter import OpenAICandidateDesigner
                candidate_designer = OpenAICandidateDesigner(args.llm_model, args.llm_timeout)
            except (ImportError, ValueError) as exc:
                parser.error(str(exc))
        dataset = _dataset(args.seed, args.size)
        engine = EvolutionEngine(
            seed=args.seed,
            repeats=args.repeats,
            population_size=args.population_size,
            max_evaluations=args.max_evaluations,
            max_workers=args.max_workers,
            global_timeout_seconds=args.global_timeout,
            candidate_designer=candidate_designer,
        )
        result = engine.run(dataset, args.generations)
        result["replay"] = {
            "dataset": dataset,
            "dataset_storage": "embedded_synthetic_cli_dataset",
        }
        _persist(result, args.output)
        if result["best_candidate"]:
            print("Best:", result["best_candidate"]["candidate"])
        return 0

    if args.command in {"sandbox-check", "sandbox-smoke"}:
        if not args.image:
            parser.error("set --image or METAFORGE_SANDBOX_IMAGE to a locally available sha256-pinned image")
        try:
            from metaforge.sandbox.policy import SandboxPolicy
            from metaforge.sandbox.runner import SandboxRunner
            preparation = SandboxRunner(SandboxPolicy(image=args.image, runtime=args.runtime)).prepare()
        except Exception as exc:
            parser.error(f"sandbox preflight failed: {type(exc).__name__}")
        if args.command == "sandbox-check":
            print({
                "runtime": preparation.runtime,
                "runtime_version": preparation.runtime_version,
                "image": preparation.image,
                "rootless": preparation.rootless,
                "code_executed": False,
            })
            return 0
        smoke_source = """
import json, os, resource, socket, sys

def fail(code):
    raise SystemExit(code)

if os.geteuid() == 0:
    fail(10)

mounts = {}
with open("/proc/self/mountinfo", encoding="utf-8") as handle:
    for line in handle:
        fields = line.split()
        if len(fields) > 5:
            mounts[fields[4]] = set(fields[5].split(","))
if "ro" not in mounts.get("/", set()):
    fail(11)
if "ro" not in mounts.get("/workspace", set()):
    fail(12)
if "noexec" not in mounts.get("/tmp", set()):
    fail(13)

status = {}
with open("/proc/self/status", encoding="utf-8") as handle:
    for line in handle:
        if ":" in line:
            key, value = line.split(":", 1)
            status[key] = value.strip()
if status.get("NoNewPrivs") != "1":
    fail(14)
if int(status.get("CapEff", "1"), 16) != 0:
    fail(15)

soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
if soft > 64 or hard > 64:
    fail(16)
soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
if soft > 1_000_000 or hard > 1_000_000:
    fail(17)
soft, hard = resource.getrlimit(resource.RLIMIT_CORE)
if soft != 0 or hard != 0:
    fail(18)

for path, limit in (
    ("/sys/fs/cgroup/memory.max", 256 * 1024 * 1024),
    ("/sys/fs/cgroup/pids.max", 64),
):
    with open(path, encoding="utf-8") as handle:
        value = handle.read().strip()
    if value == "max" or int(value) > limit:
        fail(19)

with open("/sys/fs/cgroup/cpu.max", encoding="utf-8") as handle:
    quota, period = handle.read().split()
if quota == "max" or int(quota) <= 0 or int(period) <= 0 or int(quota) / int(period) > 1.01:
    fail(20)
with open("/sys/fs/cgroup/memory.swap.max", encoding="utf-8") as handle:
    if handle.read().strip() != "0":
        fail(23)
if os.statvfs("/dev/shm").f_frsize * os.statvfs("/dev/shm").f_blocks > 16 * 1024 * 1024:
    fail(24)
if os.statvfs("/tmp").f_frsize * os.statvfs("/tmp").f_blocks > 16 * 1024 * 1024:
    fail(25)

if "OPENAI_API_KEY" in os.environ:
    fail(21)
try:
    socket.create_connection(("1.1.1.1", 53), timeout=0.5)
except OSError:
    pass
else:
    fail(22)

payload = json.load(open(sys.argv[1], encoding="utf-8"))
print(json.dumps(sorted(payload["values"])))
"""
        result = SandboxRunner(SandboxPolicy(image=args.image, runtime=args.runtime)).run_python(
            smoke_source, [7, 2, 9, 1]
        )
        print({
            "runtime": preparation.runtime,
            "image": preparation.image,
            "sandbox_smoke_passed": result.success,
            "measurement_scope": result.measurement_scope,
            "error": result.error,
        })
        return 0 if result.success else 1

    if args.command == "backend-check":
        if not 1 <= args.size <= 100_000 or not 1 <= args.repeats <= 20 or args.timeout <= 0:
            parser.error("backend-check size, repeats, or timeout is outside allowed bounds")
        dataset = _dataset(args.seed, args.size)
        candidate = Candidate(args.strategy)
        expected = sorted(dataset)
        backends = [PythonBackend()]
        if args.backend in {"rust", "both"}:
            from metaforge.backends.rust import RustBackend
            backends.append(RustBackend(timeout_seconds=args.timeout))
        if args.backend in {"c", "both"}:
            from metaforge.backends.c import CBackend
            backends.append(CBackend(timeout_seconds=args.timeout))
        results = []
        for backend in backends:
            try:
                preparation = backend.prepare()
                runs = backend.run_repeated(candidate, dataset, args.repeats, args.timeout)
                correct = len(runs) == args.repeats and all(
                    item.success and item.output == expected for item in runs
                )
                if not correct:
                    results.append({
                        "backend": backend.name,
                        "version": preparation.version,
                        "compile_seconds": preparation.compile_seconds,
                        "correctness_passed": False,
                        "error": next((item.error for item in runs if not item.success), "Output mismatch"),
                    })
                    continue
                times = [item.elapsed_seconds for item in runs if item.elapsed_seconds is not None]
                peaks = [item.peak_bytes for item in runs if item.peak_bytes is not None]
                results.append({
                    "backend": backend.name,
                    "version": preparation.version,
                    "compile_seconds": preparation.compile_seconds,
                    "source_sha256": preparation.source_sha256,
                    "artifact_sha256": preparation.artifact_sha256,
                    "build_flags": list(preparation.build_flags),
                    "correctness_passed": True,
                    "samples_seconds": times,
                    "median_algorithm_seconds": statistics.median(times),
                    "median_peak_bytes": statistics.median(peaks) if peaks else None,
                    "measurement_scope": runs[0].measurement_scope,
                })
            except Exception as exc:
                results.append({
                    "backend": backend.name,
                    "correctness_passed": False,
                    "error": f"{type(exc).__name__}: backend preparation or execution failed",
                })
        report = {
            "schema_version": "1.0",
            "run_id": uuid4().hex,
            "problem": "integer_sort",
            "strategy": args.strategy,
            "dataset_size": len(dataset),
            "dataset_sha256": hashlib.sha256(json.dumps(dataset, separators=(",", ":")).encode()).hexdigest(),
            "seed": args.seed,
            "environment": runtime_metadata(),
            "results": results,
            "comparison_note": "Compile time is separate. Runtime and memory metrics have backend-specific scopes and must not be treated as directly comparable.",
        }
        save_run(report, args.output)
        for item in results:
            print(item)
        print(f"Report: {args.output}")
        return 0 if all(item.get("correctness_passed") for item in results) else 1

    try:
        original = json.loads(args.run.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        parser.error(f"cannot read experiment JSON: {exc}")
    replay_data = original.get("replay", {})
    dataset = replay_data.get("dataset") if isinstance(replay_data, dict) else None
    if not isinstance(dataset, list) or any(type(value) is not int for value in dataset):
        parser.error("the selected run does not contain a valid embedded replay dataset")
    digest = hashlib.sha256(json.dumps(dataset, separators=(",", ":")).encode("utf-8")).hexdigest()
    if digest != original.get("dataset_sha256"):
        parser.error("embedded dataset hash does not match the original experiment")
    config = original.get("configuration", {})
    required = ("generations_requested", "population_size", "repeats",
                "max_evaluations", "max_workers", "global_timeout_seconds")
    if any(key not in config for key in required):
        parser.error("the selected run is missing required configuration metadata")
    engine = EvolutionEngine(
        seed=original["seed"],
        repeats=config["repeats"],
        population_size=config["population_size"],
        max_evaluations=config["max_evaluations"],
        max_workers=config["max_workers"],
        global_timeout_seconds=config["global_timeout_seconds"],
    )
    stored_generations = original.get("generations", [])
    has_schedule = (
        bool(stored_generations)
        and all(isinstance(item, dict) and isinstance(item.get("candidate_population"), list)
                for item in stored_generations)
    )
    replay_generations = len(stored_generations) if has_schedule else config["generations_requested"]
    candidate_schedule = (
        [item["candidate_population"] for item in stored_generations] if has_schedule else None
    )
    result = engine.run(
        dataset,
        replay_generations,
        candidate_schedule=candidate_schedule,
    )
    result["replay"] = replay_data
    result["replay_of"] = {
        "run_id": original.get("run_id"),
        "dataset_sha256": digest,
        "candidate_schedule_replayed": has_schedule,
        "generations_replayed": replay_generations,
        "generations_requested_original": config["generations_requested"],
        "candidate_sequence_matches": _candidate_signature(original) == _candidate_signature(result),
        "performance_comparison": "Timing values are new measurements and are not expected to match.",
    }
    _persist(result, args.output)
    print("Candidate sequence matches:", result["replay_of"]["candidate_sequence_matches"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
