from __future__ import annotations
from typing import Any, TypedDict
from metaforge.agents.contracts import AgentTask, AgentResult
from metaforge.agents.registry import AgentRegistry

def execute_with_langgraph(registry: AgentRegistry,
                           steps: list[tuple[str, AgentTask]]) -> list[AgentResult]:
    """Execute registered specialists as a LangGraph state graph.

    LangGraph is optional. This adapter only orchestrates existing agents;
    it does not grant agents execution privileges or bypass MetaForge gates.
    """
    try:
        from langgraph.graph import StateGraph, START, END
    except ImportError as exc:
        raise RuntimeError(
            "Install MetaForge with the 'orchestration' extra to use LangGraph"
        ) from exc

    class WorkflowState(TypedDict):
        results: list[AgentResult]

    graph = StateGraph(WorkflowState)
    node_names=[]
    for index,(role,task) in enumerate(steps):
        node_name=f"specialist_{index}"
        node_names.append(node_name)
        def make_node(selected_role: str, selected_task: AgentTask):
            def run_node(state: WorkflowState) -> dict[str, Any]:
                previous=state["results"]
                if previous and not previous[-1].success:
                    return {"results": previous}
                result=registry.get(selected_role).run(selected_task)
                return {"results": previous+[result]}
            return run_node
        graph.add_node(node_name,make_node(role,task))
    if not node_names:
        return []
    graph.add_edge(START,node_names[0])
    for current,next_node in zip(node_names,node_names[1:]):
        graph.add_edge(current,next_node)
    graph.add_edge(node_names[-1],END)
    compiled=graph.compile()
    return compiled.invoke({"results":[]})["results"]
