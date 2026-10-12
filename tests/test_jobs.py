import json
import subprocess
import sys
import time
from pathlib import Path
from threading import Event, Thread

import pytest
from PIL import Image

from saeruq.backends.mock import MockBackend
from saeruq.contracts import Request
from saeruq.jobs import JobStore, WorkerBusyError, WorkerLock, work
from saeruq.runtime import Engine


def test_queue_persists_and_processes_fifo(tmp_path):
    store = JobStore(tmp_path / "queue")
    first = store.submit(Request("first", width=64, height=64), "mock")
    second = store.submit(Request("second", width=64, height=64), "mock")
    reopened = JobStore(store.root)
    result = work(reopened, Engine(MockBackend(), tmp_path / "runs"), "mock", max_jobs=1)
    assert result["succeeded"] == 1
    assert reopened.get(first["id"])["status"] == "succeeded"
    assert reopened.get(second["id"])["status"] == "queued"
    assert reopened.inspect(first["id"])["execution"]["progress"] == 1
    assert work(reopened, Engine(MockBackend(), tmp_path / "runs"), "mock")["succeeded"] == 1


def test_edit_uses_enqueued_snapshot_after_original_is_changed(tmp_path):
    source = tmp_path / "original.png"
    Image.new("RGB", (64, 64), (120, 80, 40)).save(source)
    store = JobStore(tmp_path / "queue")
    job = store.submit(Request("edit", width=64, height=64, source=source), "mock")
    Image.new("RGB", (64, 64), (255, 0, 0)).save(source)
    work(store, Engine(MockBackend(), tmp_path / "runs"), "mock")
    job = store.get(job["id"])
    assert job["status"] == "succeeded"
    with Image.open(Path(job["directory"]) / "source.png") as saved:
        assert saved.getpixel((0, 0)) == (120, 80, 40)


def test_corrupt_snapshot_fails_before_backend_loading(tmp_path):
    class NoLoad(MockBackend):
        def load(self):
            pytest.fail("corrupt inputs must never reach GPU loading")
    source = tmp_path / "source.png"
    Image.new("RGB", (64, 64)).save(source)
    store = JobStore(tmp_path / "queue")
    job = store.submit(Request("edit", width=64, height=64, source=source), "mock")
    Path(job["request"]["source"]).write_bytes(b"tampered")
    assert work(store, Engine(NoLoad(), tmp_path / "runs"), "mock")["failed"] == 1
    assert "무결성" in store.get(job["id"])["error"]["message"]
    with pytest.raises(ValueError, match="무결성"):
        store.retry(job["id"])


def test_cancel_queued_and_retry_preserve_history(tmp_path):
    store = JobStore(tmp_path / "queue")
    job = store.submit(Request("test", width=64, height=64), "mock")
    assert store.cancel(job["id"])["status"] == "cancelled"
    assert work(store, Engine(MockBackend(), tmp_path / "runs"), "mock")["processed"] == 0
    retried = store.retry(job["id"])
    assert retried["parent_id"] == job["id"]
    assert retried["id"] != job["id"]
    assert work(store, Engine(MockBackend(), tmp_path / "runs"), "mock")["succeeded"] == 1
    assert store.get(job["id"])["status"] == "cancelled"
    with pytest.raises(ValueError):
        store.retry(retried["id"])
    with pytest.raises(ValueError):
        store.cancel(retried["id"])


def test_cancel_running_from_another_store_connection(tmp_path):
    entered, release = Event(), Event()
    class WaitingBackend(MockBackend):
        def infer(self, request, image, token, progress):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test release timeout")
            token.check()
            return super().infer(request, image, token, progress)
    store = JobStore(tmp_path / "queue")
    job = store.submit(Request("test", width=64, height=64), "mock")
    output = []
    thread = Thread(target=lambda: output.append(work(store, Engine(WaitingBackend(), tmp_path / "runs"), "mock")))
    thread.start()
    try:
        assert entered.wait(5)
        cancelled = JobStore(store.root).cancel(job["id"])
        assert cancelled["status"] == "running"
        assert cancelled["cancel_requested"]
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert output[0]["cancelled"] == 1
    result = store.get(job["id"])
    assert result["status"] == "cancelled"
    assert "result.png" not in result["result"]["artifacts"]


def test_backend_exception_does_not_block_the_next_job(tmp_path):
    class OnceFailing(MockBackend):
        failed = False
        def infer(self, *args):
            if not self.failed:
                self.failed = True
                raise MemoryError("simulated allocation failure")
            return super().infer(*args)
    store = JobStore(tmp_path / "queue")
    first = store.submit(Request("first", width=64, height=64), "mock")
    second = store.submit(Request("next", width=64, height=64), "mock")
    result = work(store, Engine(OnceFailing(), tmp_path / "runs"), "mock")
    assert result["failed"] == result["succeeded"] == 1
    assert store.get(first["id"])["error"]["code"] == "out_of_memory"
    assert store.get(second["id"])["status"] == "succeeded"


def test_worker_lock_prevents_a_second_worker(tmp_path):
    store = JobStore(tmp_path / "queue")
    with WorkerLock(store.root / "worker.lock"):
        with pytest.raises(WorkerBusyError):
            work(store, Engine(MockBackend(), tmp_path / "runs"), "mock")


def test_process_death_releases_lock_and_marks_interrupted(tmp_path):
    store = JobStore(tmp_path / "queue")
    first = store.submit(Request("first", width=64, height=64), "mock")
    second = store.submit(Request("second", width=64, height=64), "mock")
    ready = tmp_path / "ready.txt"
    script = """from pathlib import Path
from threading import Event
import sys
from saeruq.jobs import JobStore, WorkerLock
store = JobStore(Path(sys.argv[1]))
with WorkerLock(store.root / 'worker.lock'):
    store._claim('mock', Path(sys.argv[2]))
    Path(sys.argv[3]).write_text('ready')
    Event().wait(30)
"""
    child = subprocess.Popen([sys.executable, "-c", script, str(store.root), str(tmp_path / "runs"), str(ready)])
    try:
        deadline = time.monotonic()+5
        while not ready.exists() and time.monotonic() < deadline and child.poll() is None:
            time.sleep(0.02)
        assert ready.exists()
        with pytest.raises(WorkerBusyError):
            work(store, Engine(MockBackend(), tmp_path / "runs"), "mock")
        child.terminate()
        child.wait(timeout=5)
        summary = work(store, Engine(MockBackend(), tmp_path / "runs"), "mock")
        assert summary["recovered"] == 1
        assert summary["recovery"]["interrupted"] == 1
        assert summary["succeeded"] == 1
        assert store.get(first["id"])["status"] == "interrupted"
        assert store.get(second["id"])["status"] == "succeeded"
        assert store.retry(first["id"])["parent_id"] == first["id"]
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


def test_completed_run_is_recovered_without_duplicate_inference(tmp_path):
    store = JobStore(tmp_path / "queue")
    job = store.submit(Request("test", width=64, height=64), "mock")
    claimed = store._claim("mock", tmp_path / "runs")
    # Simulate death after publishing run.json but before queue finalization.
    record = Engine(MockBackend(), tmp_path / "runs").run(store.request(claimed), run_id=claimed["run_id"])
    summary = work(store, Engine(MockBackend(), tmp_path / "runs"), "mock")
    assert summary["recovered"] == 1
    assert summary["processed"] == 0
    assert summary["recovery"]["succeeded"] == 1
    assert store.get(job["id"])["status"] == "succeeded"
    assert store.get(job["id"])["result"]["artifacts"] == record["artifacts"]


def test_tampered_completed_output_is_not_recovered_as_success(tmp_path):
    store = JobStore(tmp_path / "queue")
    job = store.submit(Request("test", width=64, height=64), "mock")
    claimed = store._claim("mock", tmp_path / "runs")
    record = Engine(MockBackend(), tmp_path / "runs").run(store.request(claimed), run_id=claimed["run_id"])
    (Path(record["directory"]) / "result.png").write_bytes(b"damaged")
    work(store, Engine(MockBackend(), tmp_path / "runs"), "mock")
    assert store.get(job["id"])["status"] == "interrupted"


def test_worker_only_consumes_matching_backend(tmp_path):
    store = JobStore(tmp_path / "queue")
    flux = store.submit(Request("flux", width=64, height=64), "flux")
    store.submit(Request("mock", width=64, height=64), "mock")
    assert work(store, Engine(MockBackend(), tmp_path / "runs"), "mock")["succeeded"] == 1
    assert store.get(flux["id"])["status"] == "queued"
    with pytest.raises(ValueError, match="backend"):
        work(store, Engine(MockBackend(), tmp_path / "runs"), "flux")


def test_job_cli_across_separate_processes(tmp_path):
    def cli(*args):
        completed = subprocess.run([sys.executable, "-m", "saeruq", *args], capture_output=True, text=True, encoding="utf-8")
        assert completed.returncode == 0, completed.stderr
        return json.loads(completed.stdout)
    queue = str(tmp_path / "queue")
    job = cli("enqueue", "--backend", "mock", "--prompt", "한글 작업", "--width", "64", "--height", "64", "--queue", queue)
    assert cli("jobs", "--queue", queue)["jobs"][0]["id"] == job["id"]
    assert cli("cancel", job["id"], "--queue", queue)["status"] == "cancelled"
    retry = cli("retry", job["id"], "--queue", queue)
    assert cli("worker", "--backend", "mock", "--queue", queue, "--runs", str(tmp_path / "runs"))["succeeded"] == 1
    completed = cli("job", retry["id"], "--queue", queue)
    assert completed["status"] == "succeeded"
    assert completed["execution"]["phase"] == "complete"


def test_watch_accepts_later_jobs_and_stops_when_idle(tmp_path):
    store = JobStore(tmp_path / "queue")
    stop, entered = Event(), Event()
    output = []
    class NotifyingBackend(MockBackend):
        def infer(self, *args):
            entered.set()
            return super().infer(*args)
    thread = Thread(target=lambda: output.append(work(store, Engine(NotifyingBackend(), tmp_path / "runs"), "mock", watch=True, stop=stop)))
    thread.start()
    try:
        job = store.submit(Request("arrived later", width=64, height=64), "mock")
        assert entered.wait(5)
        deadline = time.monotonic()+5
        while store.get(job["id"])["status"] == "running" and time.monotonic() < deadline:
            time.sleep(.02)
        assert store.get(job["id"])["status"] == "succeeded"
    finally:
        stop.set()
        thread.join(5)
    assert not thread.is_alive()
    assert output[0]["succeeded"] == 1
