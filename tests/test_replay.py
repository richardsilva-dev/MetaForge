import json

from metaforge.cli import main


def test_cli_run_archives_and_replays_candidate_sequence(tmp_path, monkeypatch):
    output = tmp_path / ".artifacts" / "latest.json"
    monkeypatch.setattr("sys.argv", [
        "metaforge", "run", "--size", "8", "--generations", "2",
        "--seed", "23", "--repeats", "1", "--population-size", "4",
        "--max-evaluations", "8", "--max-workers", "1",
        "--global-timeout", "30", "--output", str(output),
    ])
    assert main() == 0
    original = json.loads(output.read_text(encoding="utf-8"))
    archive = tmp_path / ".artifacts" / "runs" / f"{original['run_id']}.json"
    assert archive.exists()

    replay_output = tmp_path / ".artifacts" / "replay.json"
    monkeypatch.setattr("sys.argv", [
        "metaforge", "replay", "--run", str(archive), "--output", str(replay_output),
    ])
    assert main() == 0
    replayed = json.loads(replay_output.read_text(encoding="utf-8"))
    assert replayed["replay_of"]["candidate_schedule_replayed"] is True
    assert replayed["replay_of"]["candidate_sequence_matches"] is True
    assert replayed["dataset_sha256"] == original["dataset_sha256"]
