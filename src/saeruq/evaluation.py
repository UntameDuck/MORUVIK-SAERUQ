import json
from pathlib import Path

from saeruq.contracts import CancellationToken, Request
from saeruq.runtime import Engine, verify_run
from saeruq.storage import atomic_json, confined


def evaluate(engine: Engine, manifest: Path, report: Path, token: CancellationToken | None = None) -> dict:
    data = json.loads(manifest.read_text(encoding="utf-8"))
    tasks = data.get("tasks", [])
    if data.get("schema_version") != 1 or not tasks or not isinstance(tasks, list):
        raise ValueError("schema_version=1의 비어 있지 않은 평가 목록이 필요합니다.")
    ids = [t.get("id") for t in tasks]
    if any(not isinstance(i, str) or not i for i in ids) or len(ids) != len(set(ids)):
        raise ValueError("평가 ID는 중복 없는 문자열이어야 합니다.")
    if report.exists():
        raise ValueError("평가 보고서가 이미 있습니다. 새 경로를 사용하세요.")
    requests = []
    for task in tasks:
        if task.get("kind") not in {"generate", "edit"}:
            raise ValueError("평가 종류는 generate 또는 edit이어야 합니다.")
        if task["kind"] == "edit" and not task.get("source"):
            raise ValueError("편집 평가에는 source가 필요합니다.")
        if task["kind"] == "generate" and task.get("source"):
            raise ValueError("생성 평가에는 source를 지정할 수 없습니다.")
        request = Request(prompt=task["prompt"], seed=task.get("seed", 0), width=task.get("width", 768), height=task.get("height", 768),
                          source=confined(manifest.parent, task["source"]) if task.get("source") else None,
                          mask=confined(manifest.parent, task["mask"]) if task.get("mask") else None,
                          edit_mode=task.get("edit_mode", "full"), context=task.get("context", 64))
        request.validate()
        requests.append(request)
    result = {"schema_version": 1, "backend": engine.backend.describe(), "fixture_notice": data.get("fixture_notice"), "tasks": [], "human_quality_review": "pending"}
    for task, request in zip(tasks, requests):
        record = engine.run(request, token)
        integrity = verify_run(Path(record["directory"]))
        result["backend"] = record["backend"]
        result["tasks"].append({"id": task["id"], "run_id": record["id"], "directory": record["directory"], "status": record["status"], "metrics": record["metrics"], "integrity": integrity, "error": record.get("error"), "human_review": None})
        result["succeeded"] = sum(t["status"] == "succeeded" for t in result["tasks"])
        result["failed"] = sum(t["status"] == "failed" for t in result["tasks"])
        result["cancelled"] = sum(t["status"] == "cancelled" for t in result["tasks"])
        atomic_json(report, result)
        if record["status"] == "cancelled":
            break
    return result
