from __future__ import annotations
from typing import Any

def render_report(result:dict[str,Any])->str:
    lines=["# MetaForge experiment report","",f"- Problem: {result['problem']}",
           f"- Dataset size: {result['dataset_size']}",
           f"- Generations: {len(result['generations'])}",""]
    best=result.get("best_candidate")
    if best:
        lines += ["## Best valid candidate","",f"- Strategy: {best['candidate']['strategy']}",
                  f"- Pivot: {best['candidate']['pivot']}",
                  f"- Median time: {best['median_seconds']:.8f} s",
                  f"- Median traced peak: {best['median_peak_bytes']} bytes",""]
    else: lines += ["No candidate passed correctness validation.",""]
    lines += ["## Generations","","| Generation | Valid | Unique |","|---:|---:|---:|"]
    for g in result["generations"]:
        lines.append(f"| {g['generation']} | {g['valid_count']} | {g['population_diversity']} |")
    lines += ["","Benchmark values are exploratory and hardware-dependent.",""]
    return "\n".join(lines)
