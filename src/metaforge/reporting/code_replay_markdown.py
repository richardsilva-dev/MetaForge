from __future__ import annotations

from typing import Any
from metaforge.reporting.code_markdown import _fence


def render_code_replay_report(result: dict[str, Any]) -> str:
    benchmark = result.get("benchmark", {})
    lines = [
        "# MetaForge — Code Replay Report",
        "",
        f"- Replay run ID: `{result.get('run_id', 'n/a')}`",
        f"- Original run ID: `{result.get('replay_of', 'n/a')}`",
        f"- Dataset size: {result.get('dataset_size', 0)}",
        f"- Dataset SHA-256: `{result.get('dataset_sha256', 'n/a')}`",
        f"- Benchmark suite: {benchmark.get('suite', 'legacy-single-input')} / {benchmark.get('suite_version', '0')}",
        f"- Workloads: {', '.join(benchmark.get('workload_names', ['input']))}",
        f"- Sandbox smoke passed: {result.get('sandbox', {}).get('smoke_passed', False)}",
        f"- Candidates re-evaluated: {result.get('budget', {}).get('candidates_evaluated', 0)}",
        "",
        "## Candidate results",
        "",
    ]
    for item in result.get("candidate_results", []):
        candidate = item["candidate"]
        lines.extend([
            f"### `{candidate['fingerprint'][:16]}`",
            "",
            f"- Original generation: {candidate.get('generation')}",
            f"- Parent: `{candidate.get('parent_fingerprint') or 'initial'}`",
            f"- Static review approved: {item['review']['approved']}",
            f"- Correctness passed across all workloads: {item['correctness_passed']}",
            f"- Aggregate median seconds: {item.get('median_algorithm_seconds')}",
            f"- Total algorithm samples: {len(item.get('algorithm_samples_seconds', []))}",
            f"- Total container wall seconds: {item.get('container_wall_seconds')}",
            "",
            "| Workload | Correct | Median seconds | Samples |",
            "|---|---:|---:|---|",
        ])
        for name, measurement in item.get("workload_results", {}).items():
            lines.append(
                f"| {name} | {measurement.get('correctness_passed', False)} "
                f"| {measurement.get('median_algorithm_seconds')} "
                f"| {measurement.get('algorithm_samples_seconds', [])} |"
            )
        if item.get("error"):
            lines.append(f"- Error: {item['error']}")
        lines.extend(["", "Source:", "", f"{_fence(candidate['source_code'])}python",
                      candidate["source_code"], _fence(candidate["source_code"]), ""])
    selection = result.get("selection", {})
    if selection:
        lines.extend([
            "## Selection gate",
            "",
            f"- Policy: {selection.get('policy', 'n/a')}",
            f"- Selected source: {selection.get('selected_source', 'n/a')}",
            f"- Candidate promoted: {selection.get('candidate_promoted', False)}",
            f"- Minimum improvement: {selection.get('minimum_improvement_ratio', 'n/a')}",
            f"- Baseline median: {selection.get('baseline_median_seconds', 'n/a')} s",
            f"- Selected median: {selection.get('selected_median_seconds', 'n/a')} s",
            f"- Measured improvement: {selection.get('improvement_ratio', 'n/a')}",
            "",
        ])
    best = result.get("best_candidate")
    if best:
        lines.extend([
            "## Best replayed candidate",
            "",
            f"- Fingerprint: `{best['candidate']['fingerprint']}`",
            f"- Aggregate median seconds: {best['median_algorithm_seconds']}",
            "",
        ])
    lines.extend(["## Reproducibility note", "", result.get("note", ""), ""])
    return "\n".join(lines)
