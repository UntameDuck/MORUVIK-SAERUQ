import math
import json

import pytest

from saeruq.backends.mock import MockBackend
from saeruq.contracts import Request
from saeruq.runtime import Engine, verify_run


def test_cleanup_failure_does_not_erase_inference_failure(tmp_path):
    class BrokenBackend(MockBackend):
        def infer(self, *args):
            raise MemoryError("original failure")
        def unload(self):
            raise RuntimeError("cleanup failure")
    record = Engine(BrokenBackend(), tmp_path).run(Request("test", width=64, height=64))
    assert record["status"] == "failed"
    assert record["phase"] == "inference"
    assert record["error"]["message"] == "original failure"
    assert record["error"]["code"] == "out_of_memory"
    assert record["cleanup_error"]["message"] == "cleanup failure"
    assert record["metrics"]["inference_seconds"] >= 0
    assert verify_run(tmp_path / record["id"])["status"] == "failed"


def test_describe_failure_is_persisted(tmp_path):
    class BadDescribe(MockBackend):
        def describe(self):
            raise ValueError("metadata unavailable")
    record = Engine(BadDescribe(), tmp_path).run(Request("test", width=64, height=64))
    assert record["status"] == "failed"
    assert record["error"]["message"] == "metadata unavailable"


def test_load_failure_keeps_timing_and_error_phase(tmp_path):
    class BrokenLoad(MockBackend):
        def load(self):
            record = json.loads(next(tmp_path.glob("*/run.json")).read_text(encoding="utf-8"))
            assert record["phase"] == "load"
            raise RuntimeError("load failed")
    record = Engine(BrokenLoad(), tmp_path).run(Request("test", width=64, height=64))
    assert record["phase"] == "load"
    assert record["metrics"]["load_seconds"] >= 0


@pytest.mark.parametrize("progress", [math.nan, math.inf, -1, 2])
def test_invalid_backend_progress_is_persisted_as_failure(tmp_path, progress):
    class BadProgress(MockBackend):
        def infer(self, request, image, token, callback):
            callback(progress)
    record = Engine(BadProgress(), tmp_path).run(Request("test", width=64, height=64))
    assert record["status"] == "failed"
    assert record["error"]["type"] == "ValueError"


def test_caller_run_id_cannot_escape_or_overwrite(tmp_path):
    engine = Engine(MockBackend(), tmp_path)
    with pytest.raises(ValueError):
        engine.run(Request("test", width=64, height=64), run_id="../outside")
    first = engine.run(Request("first", width=64, height=64), run_id="fixed-id")
    with pytest.raises(FileExistsError):
        engine.run(Request("second", width=64, height=64), run_id="fixed-id")
    assert first["artifacts"] == engine.run(Request("first", width=64, height=64))["artifacts"]
