from __future__ import annotations

from typing import Any


def _fence(source: str) -> str:
    longest = 0
    current = 0
    for char in source:
        if char == "`":
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return "`" * max(3, longest + 1)


def render_code_report(result: dict[str, Any]) -> str:
    best = result.get("best_candidate")
    budget = result.get("budget", {})
    sandbox = result.get("sandbox", {})
    benchmark = result.get("benchmark", {})
    lines = [
        "# MetaForge — Code Evolution Report",
        "",
        f"- Run ID: `{result.get('run_id', 'n/a')}`",
        f"- Created: {result.get('created_at', 'n/a')}",
        f"- Problem: {result.get('problem', 'n/a')}",
        f"- Dataset: {result.get('dataset_size', 0)} integers",
        f"- Dataset SHA-256: `{result.get('dataset_sha256', 'n/a')}`",
        f"- Benchmark suite: {benchmark.get('suite', 'legacy-single-input')} / {benchmark.get('suite_version', '0')}",
        f"- Workloads: {', '.join(benchmark.get('workload_names', ['input']))}",
        f"- Aggregation: {benchmark.get('aggregation', 'single_workload_median')}",
        f"- Sandbox: {sandbox.get('runtime', 'n/a')} / {sandbox.get('runtime_version', 'n/a')}",
        f"- Image: `{sandbox.get('image', 'n/a')}`",
        f"- Sandbox smoke passed: {sandbox.get('smoke_passed', False)}",
        f"- Sandbox smoke wall time: {sandbox.get('smoke_wall_seconds', 'n/a')} s",
        "",
        "## Budget",
        "",
        f"- Agent calls: {budget.get('agent_calls_used', 0)} / {budget.get('max_agent_calls', 0)}",
        f"- Generations: {len(result.get('generations', []))} / {budget.get('generations_requested', 0)}",
        f"- Repeats per workload: {budget.get('repeats', 0)}",
        f"- Workloads per candidate: {budget.get('workload_count', 1)}",
        f"- Stop reason: {budget.get('stop_reason', 'n/a')}",
        "",
    ]
    if best:
        candidate = best["candidate"]
        source = candidate["source_code"]
        fence = _fence(source)
        lines.extend([
            "## Best candidate",
            "",
            f"- Fingerprint: `{candidate['fingerprint']}`",
            f"- Parent: `{candidate.get('parent_fingerprint') or 'initial'}`",
            f"- Generation: {candidate['generation']}",
            f"- Aggregate median (median of workload medians): {best.get('median_algorithm_seconds')} s",
            f"- Raw algorithm samples across workloads: {best.get('algorithm_samples_seconds')}",
            f"- Total container wall time: {best.get('container_wall_seconds')} s",
            f"- Rationale: {candidate.get('rationale', '')}",
            "",
            "### Per-workload measurements",
            "",
            "| Workload | Correct | Median seconds | Samples | Container wall seconds |",
            "|---|---:|---:|---|---:|",
        ])
        for name, measurement in best.get("workload_results", {}).items():
            lines.append(
                f"| {name} | {measurement.get('correctness_passed', False)} "
                f"| {measurement.get('median_algorithm_seconds')} "
                f"| {measurement.get('algorithm_samples_seconds', [])} "
                f"| {measurement.get('container_wall_seconds')} |"
            )
        lines.extend([
            "",
            "### Source",
            "",
            f"{fence}python",
            source,
            fence,
            "",
        ])
    else:
        lines.extend(["## Best candidate", "", "No candidate passed static review and the complete sandbox workload suite.", ""])

    lines.extend(["## Generations", ""])
    for generation in result.get("generations", []):
        lines.extend([
            f"### Generation {generation['generation']}",
            "",
            f"- Proposals: {len(generation.get('proposals', []))}",
            f"- Evaluated: {len(generation.get('evaluations', []))}",
            f"- Correct candidates across all workloads: {generation.get('valid_count', 0)}",
            "",
        ])
        for proposal in generation.get("proposals", []):
            if not proposal.get("success"):
                lines.append(
                    f"- Rejected proposal `{proposal.get('task_id', 'unknown')}`: "
                    f"{proposal.get('error') or proposal.get('review_findings')}"
                )
        for evaluation in generation.get("evaluations", []):
            candidate = evaluation["candidate"]
            lines.append(
                f"- `{candidate['fingerprint'][:12]}`: "
                f"correct={evaluation['correctness_passed']}, "
                f"aggregate_median={evaluation.get('median_algorithm_seconds')}, "
                f"parent={candidate.get('parent_fingerprint') or 'initial'}"
            )
            if evaluation.get("error"):
                lines.append(f"  - Error: {evaluation['error']}")
            for name, measurement in evaluation.get("workload_results", {}).items():
                lines.append(
                    f"  - {name}: correct={measurement.get('correctness_passed')}, "
                    f"median={measurement.get('median_algorithm_seconds')}"
                )
            findings = evaluation.get("review", {}).get("findings", [])
            for finding in findings:
                lines.append(f"  - Review: {finding}")
        lines.append("")
    lines.extend([
        "## Measurement boundary",
        "",
        result.get("measurement_note", "No measurement note supplied."),
        "",
        "Generated source is untrusted. It was statically reviewed and executed only inside the configured rootless container. The container wall time is not used for candidate ranking.",
        "",
    ])
    return "\n".join(lines)
