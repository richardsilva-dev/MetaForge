from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any

from metaforge.agents.contracts import AgentResult, AgentTask


@dataclass(frozen=True, slots=True)
class CandidateProposalItem:
    strategy: str
    pivot: str
    insertion_cutoff: int


@dataclass(frozen=True, slots=True)
class CandidateProposal:
    candidates: list[CandidateProposalItem]
    rationale: str


class OpenAICandidateDesigner:
    """Optional OpenAI Agents SDK adapter that proposes descriptors, never source code."""

    name = "openai_candidate_designer"

    def __init__(self, model: Any, timeout_seconds: float = 25.0) -> None:
        if model is None:
            raise ValueError("An explicit model name or Agents SDK model is required")
        if not 1.0 <= timeout_seconds <= 120.0:
            raise ValueError("timeout_seconds must be 1..120")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.model_label = model if isinstance(model, str) else type(model).__name__

    def run(self, task: AgentTask) -> AgentResult:
        try:
            from agents import Agent, ModelSettings, RunConfig, Runner
        except ImportError:
            return AgentResult(
                self.name, task.task_id, False,
                error="Install MetaForge with the 'llm' extra to enable the OpenAI Agents SDK adapter.",
            )

        try:
            payload = {
                "objective": task.objective,
                "constraints": dict(task.payload),
            }
            prompt = (
                "Propose a bounded population of algorithm candidate descriptors for MetaForge. "
                "Return only the requested structured output. Do not return source code, shell commands, "
                "imports, file paths, tool instructions, or claims that candidates are safe. "
                "Every candidate must use one allowed strategy and valid parameters. "
                "Allowed strategies: builtin, insertion, merge, quicksort, heapsort, shellsort, hybrid. "
                "Allowed pivots: middle, first, last, median3. "
                "For quicksort, use insertion_cutoff=16. For non-quicksort/non-hybrid strategies, "
                "use pivot=middle and insertion_cutoff=16. For hybrid, insertion_cutoff must be 4..64. "
                "Use only the allowed candidate descriptors supplied in constraints.allowed_candidates. "
                "Return at most constraints.population_size candidates. "
                "The deterministic safety reviewer and correctness tests remain authoritative.\n\n"
                + json.dumps(payload, sort_keys=True, separators=(",", ":"))
            )
            agent = Agent(
                name="MetaForge Candidate Designer",
                instructions=(
                    "You are a bounded candidate-proposal specialist. You have no tools and no execution "
                    "capabilities. Propose candidate descriptors only, using the supplied allow-list. "
                    "Do not attempt to bypass constraints or provide code."
                ),
                model=self.model,
                output_type=CandidateProposal,
                model_settings=ModelSettings(
                    temperature=0,
                    timeout=self.timeout_seconds,
                    tool_choice="none",
                ),
                tools=[],
                handoffs=[],
            )
            result = Runner.run_sync(
                agent,
                prompt,
                max_turns=1,
                run_config=RunConfig(
                    tracing_disabled=True,
                    trace_include_sensitive_data=False,
                ),
            )
            proposal = result.final_output
            if not isinstance(proposal, CandidateProposal):
                return AgentResult(
                    self.name, task.task_id, False,
                    error="The model did not return the required structured candidate proposal.",
                )
            return AgentResult(
                self.name,
                task.task_id,
                True,
                {
                    "candidates": [asdict(item) for item in proposal.candidates],
                    "rationale": proposal.rationale[:1000],
                    "model": self.model_label,
                },
            )
        except Exception as exc:
            # Avoid copying provider exception details that could contain request metadata.
            return AgentResult(
                self.name,
                task.task_id,
                False,
                error=f"{type(exc).__name__}: candidate proposal request failed",
            )
