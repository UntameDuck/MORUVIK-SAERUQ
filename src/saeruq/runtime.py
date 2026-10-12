import json
import platform
import re
import time
import uuid
from dataclasses import asdict, replace
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from threading import Lock

from PIL import Image

from saeruq.contracts import Backend, CancellationToken, CancelledError, Request
from saeruq.imaging import composite, crop_box, outside_changed, read_image, read_mask
from saeruq.storage import atomic_image, atomic_json, confined, digest


def environment() -> dict:
    packages = {}
    for name in ("saeruq", "Pillow", "torch", "diffusers", "transformers", "accelerate", "huggingface-hub"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    return {"python": platform.python_version(), "platform": platform.platform(), "packages": packages}


class Engine:
    """A serial runtime. Applications own their queue/UI, not the engine."""

    def __init__(self, backend: Backend, runs: Path):
        self.backend, self.runs = backend, runs
        self._lock = Lock()

    def run(self, request: Request, token: CancellationToken | None = None, *, run_id: str | None = None) -> dict:
        request.validate()
        if run_id is not None and (not isinstance(run_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", run_id)):
            raise ValueError("실행 ID는 영문·숫자·밑줄·하이픈 1~80자여야 합니다.")
        token = token or CancellationToken()
        with self._lock:
            return self._run(request, token, run_id)

    def _run(self, request: Request, token: CancellationToken, run_id: str | None) -> dict:
        run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:12]
        folder = self.runs.resolve() / run_id
        folder.mkdir(parents=True, exist_ok=False)
        record = {"schema_version": 1, "id": run_id, "created_at": datetime.now(timezone.utc).isoformat(),
                  "status": "running", "progress": 0, "request": {k: str(v) if isinstance(v, Path) else v for k, v in asdict(request).items()},
                  "backend": {"id": type(self.backend).__name__, "is_ai": None}, "environment": environment(), "artifacts": {}, "metrics": {}, "phase": "inputs"}
        record_path = folder / "run.json"
        atomic_json(record_path, record)
        started = time.perf_counter()
        def save_image(name: str, image: Image.Image) -> None:
            atomic_image(folder / name, image)
            record["artifacts"][name] = digest(folder / name)
        try:
            token.check()
            record["backend"] = self.backend.describe()
            original = read_image(request.source) if request.source else None
            mask = read_mask(request.mask, original.size) if request.mask else None
            if original:
                record["source_file_sha256"] = digest(request.source)
                save_image("source.png", original)
            if mask:
                record["mask_file_sha256"] = digest(request.mask)
                save_image("mask.png", mask)
            effective = request
            box = None
            working_image = original
            if request.edit_mode == "crop":
                box = crop_box(mask, request.context)
                working_image = original.crop(box)
                scale = max(request.width, request.height) / max(working_image.size)
                w, h = [max(64, min(2048, round(n*scale/16)*16)) for n in working_image.size]
                effective = replace(request, width=w, height=h)
            record["geometry"] = {"original_size": list(original.size) if original else None, "crop_box": list(box) if box else None, "inference_size": [effective.width, effective.height]}
            token.check()
            record["phase"] = "load"
            record["metrics"]["cold_load"] = not record["backend"]["loaded"] if "loaded" in record["backend"] else None
            atomic_json(record_path, record)
            loading = time.perf_counter()
            try:
                self.backend.load()
            finally:
                record["metrics"]["load_seconds"] = time.perf_counter()-loading
            for key, value in self.backend.metrics().items():
                if key.startswith("peak_vram_"):
                    record["metrics"]["load_"+key] = value
            record["backend"] = self.backend.describe()
            token.check()
            def progress(value: float):
                token.check()
                if not isinstance(value, (int, float)) or not 0 <= value <= 1:
                    raise ValueError("진행률은 0~1의 유한한 값이어야 합니다.")
                record["progress"] = value
                atomic_json(record_path, record)
            inference = time.perf_counter()
            record["phase"] = "inference"
            atomic_json(record_path, record)
            try:
                edited = self.backend.infer(effective, working_image, token, progress).convert("RGB")
            finally:
                record["metrics"]["inference_seconds"] = time.perf_counter()-inference
            token.check()
            record["phase"] = "postprocess"
            atomic_json(record_path, record)
            if edited.size != (effective.width, effective.height):
                raise ValueError("백엔드 출력 해상도가 요청과 다릅니다.")
            save_image("raw.png", edited)
            if mask:
                if box:
                    candidate = original.copy()
                    candidate.paste(edited.resize((box[2]-box[0], box[3]-box[1]), Image.Resampling.LANCZOS), box[:2])
                else:
                    candidate = edited.resize(original.size, Image.Resampling.LANCZOS)
                result = composite(original, candidate, mask)
                record["metrics"]["outside_mask_changed_pixels"] = outside_changed(original, result, mask)
                record["edit_semantics"] = "reference edit then mask composite; not native inpainting"
            else:
                result = edited
            token.check()
            save_image("result.png", result)
            record["metrics"].update(self.backend.metrics())
            record["status"], record["progress"] = "succeeded", 1
            record["phase"] = "complete"
        except CancelledError as error:
            record["status"], record["error"] = "cancelled", {"type": type(error).__name__, "message": str(error)}
        except Exception as error:
            record["status"], record["error"] = "failed", {"type": type(error).__name__, "message": str(error)}
            record["error"]["code"] = "out_of_memory" if isinstance(error, MemoryError) or type(error).__name__ == "OutOfMemoryError" else "backend_or_input_error"
        finally:
            if record["status"] != "succeeded":
                try:
                    record["metrics"].update(self.backend.metrics())
                except Exception as error:
                    record["metrics_error"] = str(error)
                try:
                    self.backend.unload()
                except Exception as error:
                    record["cleanup_error"] = {"type": type(error).__name__, "message": str(error)}
            record["metrics"]["total_seconds"] = time.perf_counter()-started
            atomic_json(record_path, record)
        return {"directory": str(folder), **record}

    def close(self) -> None:
        with self._lock:
            self.backend.unload()


def verify_run(folder: Path) -> dict:
    record = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    if record.get("schema_version") != 1:
        raise ValueError("지원되지 않는 실행 기록입니다.")
    artifacts = record.get("artifacts", {})
    if not isinstance(artifacts, dict):
        raise ValueError("잘못된 실행 파일 목록입니다.")
    if record.get("status") == "succeeded" and "result.png" not in artifacts:
        raise ValueError("성공 기록에 결과 이미지가 없습니다.")
    for name, expected in artifacts.items():
        path = confined(folder, name)
        if not path.is_file() or digest(path) != expected:
            raise ValueError(f"결과 파일 무결성 확인 실패: {name}")
    return {"id": record["id"], "status": record["status"], "verified_files": len(artifacts), "is_ai": record["backend"]["is_ai"]}
