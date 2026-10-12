"""Local persistent jobs; SQLite plus one OS-locked worker per queue."""

import json
import os
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

from saeruq.contracts import CancellationToken, CancelledError, Request
from saeruq.runtime import Engine, verify_run
from saeruq.storage import digest

TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkerBusyError(RuntimeError):
    pass


class WorkerLock:
    def __init__(self, path: Path):
        self.path = path
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            self.stream.write(b"0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.stream.close()
            self.stream = None
            raise WorkerBusyError("이 대기열의 실행기가 이미 작동 중입니다.") from error
        return self

    def __exit__(self, *_):
        # Closing releases the OS lock, including when the process is killed.
        # Keep the file to avoid a second inode/handle becoming a new lock.
        self.stream.close()
        self.stream = None


class JobStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "jobs.sqlite3"
        with self._connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("지원되지 않는 작업 DB 버전입니다.")
            connection.execute("""CREATE TABLE IF NOT EXISTS jobs (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed','cancelled','interrupted')),
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                request TEXT NOT NULL, inputs TEXT NOT NULL,
                backend TEXT NOT NULL, parent_id TEXT,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                run_id TEXT, directory TEXT, result TEXT, error TEXT
            )""")
            connection.execute("CREATE INDEX IF NOT EXISTS jobs_status_sequence ON jobs(status, sequence)")
            connection.execute("PRAGMA user_version=1")

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _decode(row) -> dict:
        job = dict(row)
        for key in ("request", "inputs", "result", "error"):
            job[key] = json.loads(job[key]) if job[key] else None
        job["cancel_requested"] = bool(job["cancel_requested"])
        return job

    def get(self, job_id: str) -> dict:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise ValueError(f"작업을 찾을 수 없습니다: {job_id}")
            return self._decode(row)

    def list(self, status: str | None = None, limit: int = 100) -> list[dict]:
        if status and status not in TERMINAL | {"queued", "running"}:
            raise ValueError("지원되지 않는 작업 상태입니다.")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise ValueError("조회 개수는 1~1000이어야 합니다.")
        with self._connect() as connection:
            if status:
                rows = connection.execute("SELECT * FROM jobs WHERE status=? ORDER BY sequence DESC LIMIT ?", (status, limit)).fetchall()
            else:
                rows = connection.execute("SELECT * FROM jobs ORDER BY sequence DESC LIMIT ?", (limit,)).fetchall()
            return [self._decode(row) for row in rows]

    def inspect(self, job_id: str) -> dict:
        job = self.get(job_id)
        job["execution"] = None
        if job["directory"]:
            try:
                record = json.loads((Path(job["directory"]) / "run.json").read_text(encoding="utf-8"))
                if record.get("id") == job["run_id"]:
                    job["execution"] = {key: record.get(key) for key in ("status", "phase", "progress")}
            except (OSError, ValueError, AttributeError):
                pass
        return job

    def submit(self, request: Request, backend: str, parent_id: str | None = None) -> dict:
        request.validate()
        if backend not in {"mock", "flux"}:
            raise ValueError("backend는 mock 또는 flux여야 합니다.")
        job_id = uuid.uuid4().hex
        assets = self.root / "inputs" / job_id
        inputs = {}
        paths = {}
        for field in ("source", "mask"):
            original = getattr(request, field)
            if original:
                assets.mkdir(parents=True, exist_ok=True)
                destination = assets / (field + ".image")
                shutil.copyfile(original, destination)
                paths[field] = destination
                inputs[str(destination)] = digest(destination)
        snapshot = replace(request, **paths)
        payload = {key: str(value) if isinstance(value, Path) else value for key, value in asdict(snapshot).items()}
        with self._connect() as connection:
            connection.execute("INSERT INTO jobs(id,status,created_at,updated_at,request,inputs,backend,parent_id) VALUES(?,?,?,?,?,?,?,?)",
                               (job_id, "queued", now(), now(), json.dumps(payload, ensure_ascii=False), json.dumps(inputs), backend, parent_id))
        return self.get(job_id)

    def cancel(self, job_id: str) -> dict:
        with self._connect() as connection:
            cursor = connection.execute("UPDATE jobs SET cancel_requested=1, status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END, updated_at=? WHERE id=? AND status IN ('queued','running')", (now(), job_id))
            if cursor.rowcount == 0:
                row = connection.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
                if row is None:
                    raise ValueError(f"작업을 찾을 수 없습니다: {job_id}")
                raise ValueError("완료된 작업은 취소할 수 없습니다.")
        return self.get(job_id)

    def retry(self, job_id: str) -> dict:
        job = self.get(job_id)
        if job["status"] not in {"failed", "cancelled", "interrupted"}:
            raise ValueError("실패·취소·중단 작업만 재시도할 수 있습니다.")
        self.verify_inputs(job)
        return self.submit(self.request(job), job["backend"], parent_id=job_id)

    @staticmethod
    def request(job: dict) -> Request:
        fields = dict(job["request"])
        for name in ("source", "mask"):
            if fields.get(name):
                fields[name] = Path(fields[name])
        return Request(**fields)

    def verify_inputs(self, job: dict) -> None:
        for name, expected in job["inputs"].items():
            path = Path(name).resolve()
            if not path.is_relative_to(self.root / "inputs") or not path.is_file() or digest(path) != expected:
                raise ValueError("대기열 입력 사본의 무결성 확인에 실패했습니다.")
        # Ensure both requested paths are covered by the recorded hashes.
        for name in ("source", "mask"):
            path = job["request"].get(name)
            if path and path not in job["inputs"]:
                raise ValueError("요청 입력에 대한 해시가 없습니다.")

    def _claim(self, backend: str, runs: Path) -> dict | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT id FROM jobs WHERE status='queued' AND backend=? ORDER BY sequence LIMIT 1", (backend,)).fetchone()
            if row is None:
                return None
            run_id = "job-" + uuid.uuid4().hex
            connection.execute("UPDATE jobs SET status='running',run_id=?,directory=?,updated_at=? WHERE id=?", (run_id, str(runs.resolve() / run_id), now(), row["id"]))
        return self.get(row["id"])

    def _finish(self, job_id: str, status: str, result: dict | None = None, error: dict | None = None) -> None:
        if status not in TERMINAL:
            raise ValueError("작업 종료 상태가 잘못되었습니다.")
        with self._connect() as connection:
            cursor = connection.execute("UPDATE jobs SET status=?,result=?,error=?,updated_at=? WHERE id=? AND status='running'",
                                        (status, json.dumps(result, ensure_ascii=False) if result else None, json.dumps(error, ensure_ascii=False) if error else None, now(), job_id))
            if cursor.rowcount != 1:
                raise RuntimeError("실행 중 작업만 종료할 수 있습니다.")

    def _recover(self) -> dict:
        # Caller holds WorkerLock: no previous worker can still be running.
        with self._connect() as connection:
            jobs = [self._decode(row) for row in connection.execute("SELECT * FROM jobs WHERE status='running'").fetchall()]
        counts = {status: 0 for status in sorted(TERMINAL)}
        for job in jobs:
            try:
                folder = Path(job["directory"])
                record = json.loads((folder / "run.json").read_text(encoding="utf-8"))
                if record["id"] != job["run_id"] or record["status"] not in {"succeeded", "failed", "cancelled"}:
                    raise ValueError("완료된 실행 기록이 없습니다.")
                verify_run(folder)
                self._finish(job["id"], record["status"], {"directory": str(folder), **record}, record.get("error"))
                counts[record["status"]] += 1
            except (OSError, ValueError, KeyError, TypeError) as error:
                self._finish(job["id"], "interrupted", error={"type": "WorkerInterrupted", "message": "이전 실행기가 종료되었습니다. 자동 재실행하지 않습니다.", "detail": str(error)})
                counts["interrupted"] += 1
        return counts


class JobCancellationToken(CancellationToken):
    def __init__(self, store: JobStore, job_id: str, stop: Event):
        super().__init__()
        self.store, self.job_id, self.stop = store, job_id, stop

    def check(self) -> None:
        if self.stop.is_set() or self.store.get(self.job_id)["cancel_requested"]:
            self.cancel()
        super().check()


def work(store: JobStore, engine: Engine, backend: str, *, stop: Event | None = None,
         watch: bool = False, max_jobs: int | None = None) -> dict:
    if backend not in {"mock", "flux"}:
        raise ValueError("backend가 잘못되었습니다.")
    if engine.backend.describe().get("id") != {"mock": "mock", "flux": "flux-klein-4b"}[backend]:
        raise ValueError("실행기의 실제 backend와 작업 종류가 다릅니다.")
    if max_jobs is not None and (isinstance(max_jobs, bool) or not isinstance(max_jobs, int) or max_jobs < 1):
        raise ValueError("max_jobs는 양의 정수여야 합니다.")
    stop = stop or Event()
    summary = {"processed": 0, "succeeded": 0, "failed": 0, "cancelled": 0, "recovered": 0}
    with WorkerLock(store.root / "worker.lock"):
        summary["recovery"] = store._recover()
        summary["recovered"] = sum(summary["recovery"].values())
        try:
            while not stop.is_set() and (max_jobs is None or summary["processed"] < max_jobs):
                job = store._claim(backend, engine.runs)
                if job is None:
                    if not watch:
                        break
                    stop.wait(0.25)
                    continue
                token = JobCancellationToken(store, job["id"], stop)
                try:
                    token.check()
                    store.verify_inputs(job)
                    record = engine.run(store.request(job), token, run_id=job["run_id"])
                    store._finish(job["id"], record["status"], record, record.get("error"))
                    summary[record["status"]] += 1
                except Exception as error:
                    status = "cancelled" if isinstance(error, CancelledError) else "failed"
                    store._finish(job["id"], status, error={"type": type(error).__name__, "message": str(error)})
                    summary[status] += 1
                summary["processed"] += 1
        finally:
            try:
                engine.close()
            except Exception as error:
                summary["cleanup_error"] = {"type": type(error).__name__, "message": str(error)}
    return summary
