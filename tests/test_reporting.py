from metaforge.evolution.engine import EvolutionEngine
from metaforge.reporting.markdown import render_report
from metaforge.storage.json_store import save_run

def test_report_and_persistence(tmp_path):
    result=EvolutionEngine(repeats=1,population_size=4).run([3,1,2],generations=1)
    assert "MetaForge experiment report" in render_report(result)
    path=save_run(result,tmp_path/"runs"/"run.json")
    assert path.exists() and path.read_text().startswith("{")
