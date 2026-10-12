import json
import platform
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
from saeruq.storage import atomic_json, confined, digest


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

    def run(self, request: Request, token: CancellationToken | None = None) -> dict:
        request.validate()
        token = token or CancellationToken()
        with self._lock:
            return self._run(request, token)

    def _run(self, request: Request, token: CancellationToken) -> dict:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:12]
        folder = self.runs.resolve() / run_id
        folder.mkdir(parents=True, exist_ok=False)
        record = {"schema_version": 1, "id": run_id, "created_at": datetime.now(timezone.utc).isoformat(),
                  "status": "running", "progress": 0, "request": {k: str(v) if isinstance(v, Path) else v for k, v in asdict(request).items()},
                  "backend": self.backend.describe(), "environment": environment(), "artifacts": {}, "metrics": {}}
        record_path = folder / "run.json"
        atomic_json(record_path, record)
        started = time.perf_counter()
        def save_image(name: str, image: Image.Image) -> None:
            image.save(folder / name, format="PNG")
            record["artifacts"][name] = digest(folder / name)
        try:
            token.check()
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
            loading = time.perf_counter()
            self.backend.load()
            record["metrics"]["load_seconds"] = time.perf_counter()-loading
            record["backend"] = self.backend.describe()
            token.check()
            def progress(value: float):
                token.check()
                record["progress"] = value
                atomic_json(record_path, record)
            inference = time.perf_counter()
            edited = self.backend.infer(effective, working_image, token, progress).convert("RGB")
            record["metrics"]["inference_seconds"] = time.perf_counter()-inference
            token.check()
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
        except CancelledError as error:
            record["status"], record["error"] = "cancelled", {"type": type(error).__name__, "message": str(error)}
            self.backend.unload()
        except Exception as error:
            record["status"], record["error"] = "failed", {"type": type(error).__name__, "message": str(error)}
            self.backend.unload()
        finally:
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
    if record.get("status") == "succeeded" and "result.png" not in artifacts:
        raise ValueError("성공 기록에 결과 이미지가 없습니다.")
    for name, expected in artifacts.items():
        path = confined(folder, name)
        if not path.is_file() or digest(path) != expected:
            raise ValueError(f"결과 파일 무결성 확인 실패: {name}")
    return {"id": record["id"], "status": record["status"], "verified_files": len(artifacts), "is_ai": record["backend"]["is_ai"]}
