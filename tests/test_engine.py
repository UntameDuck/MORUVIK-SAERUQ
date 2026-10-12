import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from saeruq.backends.mock import MockBackend
from saeruq.contracts import CancellationToken, Request
from saeruq.evaluation import evaluate
from saeruq.fixtures import make_fixtures
from saeruq.imaging import composite, outside_changed
from saeruq.models import MODEL_ID, prepare_model, verify_model
from saeruq.runtime import Engine, verify_run
from saeruq.storage import atomic_json, digest


@pytest.fixture
def source_and_mask(tmp_path):
    source, mask = tmp_path / "source.png", tmp_path / "mask.png"
    Image.new("RGB", (96, 80), (120, 80, 40)).save(source)
    selection = Image.new("L", (96, 80))
    ImageDraw.Draw(selection).rectangle((15, 10, 50, 60), fill=255)
    selection.putpixel((16, 11), 128)
    selection.save(mask)
    return source, mask


def test_generate_is_reproducible_and_runs_are_independent(tmp_path):
    engine = Engine(MockBackend(), tmp_path)
    request = Request("a vase", seed=42, width=64, height=80)
    first, second = engine.run(request), engine.run(request)
    assert first["status"] == second["status"] == "succeeded"
    assert first["id"] != second["id"]
    assert first["artifacts"]["result.png"] == second["artifacts"]["result.png"]
    assert first["backend"]["is_ai"] is False
    assert verify_run(Path(first["directory"]))["verified_files"] == 2


@pytest.mark.parametrize("mode", ["full", "crop"])
def test_mask_edit_preserves_inputs_and_unselected_pixels(tmp_path, source_and_mask, mode):
    source, mask = source_and_mask
    before = digest(source), digest(mask)
    record = Engine(MockBackend(), tmp_path / "runs").run(Request("blue vase", width=64, height=64, source=source, mask=mask, edit_mode=mode, context=8))
    assert record["status"] == "succeeded"
    assert record["metrics"]["outside_mask_changed_pixels"] == 0
    assert (digest(source), digest(mask)) == before
    folder = Path(record["directory"])
    assert verify_run(folder)["verified_files"] == 4
    with Image.open(folder / "result.png") as result:
        assert result.size == (96, 80)
        assert result.getpixel((0, 0)) == (120, 80, 40)
    assert bool(record["geometry"]["crop_box"]) == (mode == "crop")


def test_soft_mask_composite_has_exact_protected_pixels():
    original = Image.new("RGB", (3, 1), (10, 10, 10))
    edited = Image.new("RGB", (3, 1), (250, 250, 250))
    mask = Image.new("L", (3, 1))
    mask.putdata([0, 128, 255])
    output = composite(original, edited, mask)
    assert output.getpixel((0, 0)) == (10, 10, 10)
    assert 10 < output.getpixel((1, 0))[0] < 250
    assert output.getpixel((2, 0)) == (250, 250, 250)
    assert outside_changed(original, output, mask) == 0


@pytest.mark.parametrize("kwargs", [{"width": 65}, {"height": 4096}, {"prompt": " "}, {"seed": -1}, {"seed": True}, {"context": -1}, {"edit_mode": "crop"}, {"mask": Path("missing.png")}])
def test_invalid_requests_are_rejected_before_backend_load(tmp_path, kwargs):
    values = {"prompt": "test", "width": 64, "height": 64, **kwargs}
    with pytest.raises(ValueError):
        Engine(MockBackend(), tmp_path).run(Request(**values))
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("size,empty", [((64, 64), False), ((96, 80), True)])
def test_bad_masks_produce_persistent_failures(tmp_path, source_and_mask, size, empty):
    source, mask = source_and_mask
    Image.new("L", size, 0 if empty else 255).save(mask)
    record = Engine(MockBackend(), tmp_path / "runs").run(Request("edit", width=64, height=64, source=source, mask=mask))
    assert record["status"] == "failed"
    assert record["error"]["type"] == "ValueError"
    assert (Path(record["directory"]) / "run.json").exists()


def test_cancellation_and_recovery(tmp_path):
    token = CancellationToken()
    token.cancel()
    engine = Engine(MockBackend(), tmp_path)
    cancelled = engine.run(Request("test", width=64, height=64), token)
    assert cancelled["status"] == "cancelled"
    assert "result.png" not in cancelled["artifacts"]
    assert engine.run(Request("next", width=64, height=64))["status"] == "succeeded"


def test_cancellation_during_inference_does_not_publish_a_result(tmp_path):
    class CancellingBackend(MockBackend):
        def infer(self, request, image, token, progress):
            token.cancel()
            return super().infer(request, image, token, progress)
    record = Engine(CancellingBackend(), tmp_path).run(Request("test", width=64, height=64))
    assert record["status"] == "cancelled"
    assert "result.png" not in record["artifacts"]


def test_backend_failure_is_recorded_and_unloaded(tmp_path):
    class FailingBackend(MockBackend):
        unloaded = False
        def infer(self, *args):
            raise RuntimeError("simulated OOM")
        def unload(self):
            self.unloaded = True
    backend = FailingBackend()
    record = Engine(backend, tmp_path).run(Request("test", width=64, height=64))
    assert backend.unloaded
    assert record["status"] == "failed"
    assert record["error"]["message"] == "simulated OOM"


def test_wrong_backend_dimensions_are_not_silently_accepted(tmp_path):
    class WrongSize(MockBackend):
        def infer(self, *args):
            return Image.new("RGB", (12, 12))
    record = Engine(WrongSize(), tmp_path).run(Request("test", width=64, height=64))
    assert record["status"] == "failed"


def test_artifact_tampering_is_detected(tmp_path):
    record = Engine(MockBackend(), tmp_path).run(Request("test", width=64, height=64))
    folder = Path(record["directory"])
    (folder / "result.png").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="무결성"):
        verify_run(folder)


def test_manifest_path_escape_is_rejected(tmp_path):
    folder = tmp_path / "run"
    folder.mkdir()
    secret = tmp_path / "outside.txt"
    secret.write_text("outside")
    atomic_json(folder / "run.json", {"schema_version": 1, "status": "failed", "artifacts": {"../outside.txt": digest(secret)}})
    with pytest.raises(ValueError, match="경로"):
        verify_run(folder)


def test_model_checksums_and_revision_without_gpu(tmp_path):
    (tmp_path / "model_index.json").write_text("{}")
    (tmp_path / "weight.safetensors").write_bytes(b"fixture, not real weights")
    atomic_json(tmp_path / "saeruq-model.json", {"schema_version": 1, "model_id": MODEL_ID, "revision": "a"*40, "files": {name: digest(tmp_path / name) for name in ("model_index.json", "weight.safetensors")}})
    assert verify_model(tmp_path)["revision"] == "a"*40
    (tmp_path / "weight.safetensors").write_bytes(b"changed")
    with pytest.raises(ValueError, match="무결성"):
        verify_model(tmp_path)


def test_download_requires_pinned_revision_before_any_network(tmp_path):
    with pytest.raises(ValueError, match="commit SHA"):
        prepare_model(tmp_path, "main")


def test_fixture_evaluation_saves_all_50_runs(tmp_path):
    manifest = make_fixtures(tmp_path / "fixtures")
    report = tmp_path / "report.json"
    result = evaluate(Engine(MockBackend(), tmp_path / "runs"), manifest, report)
    assert result["succeeded"] == 50
    assert result["failed"] == 0
    assert result["backend"]["is_ai"] is False
    assert result["human_quality_review"] == "pending"
    assert len(json.loads(report.read_text(encoding="utf-8"))["tasks"]) == 50
    with pytest.raises(ValueError, match="이미"):
        evaluate(Engine(MockBackend(), tmp_path / "runs"), manifest, report)


def test_evaluation_can_be_cancelled_between_tasks(tmp_path):
    manifest = make_fixtures(tmp_path / "fixtures")
    token = CancellationToken()
    token.cancel()
    result = evaluate(Engine(MockBackend(), tmp_path / "runs"), manifest, tmp_path / "report.json", token)
    assert result["cancelled"] == 1
    assert len(result["tasks"]) == 1


def test_duplicate_evaluation_ids_fail_before_gpu_work(tmp_path):
    manifest = tmp_path / "evaluation.json"
    atomic_json(manifest, {"schema_version": 1, "tasks": [{"id": "same"}, {"id": "same"}]})
    with pytest.raises(ValueError, match="중복"):
        evaluate(Engine(MockBackend(), tmp_path / "runs"), manifest, tmp_path / "report.json")
    assert not (tmp_path / "runs").exists()
