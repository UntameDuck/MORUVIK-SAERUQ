import json
import re
from pathlib import Path

from saeruq.storage import atomic_json, confined, digest

MODEL_ID = "black-forest-labs/FLUX.2-klein-4B"
MARKER = "saeruq-model.json"


def prepare_model(destination: Path, revision: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("모델 revision은 정확한 40자리 commit SHA여야 합니다.")
    destination = destination.resolve()
    marker = destination / MARKER
    if marker.exists():
        previous = json.loads(marker.read_text(encoding="utf-8"))
        if previous.get("revision") != revision or previous.get("model_id") != MODEL_ID:
            raise ValueError("다른 revision의 모델이 있습니다. 새 디렉터리를 사용하세요.")
    from huggingface_hub import snapshot_download
    snapshot_download(repo_id=MODEL_ID, revision=revision, local_dir=destination,
                      allow_patterns=["model_index.json", "scheduler/*", "tokenizer/*", "text_encoder/*", "transformer/*", "vae/*", "README.md", "LICENSE*"])
    files = {p.relative_to(destination).as_posix(): digest(p) for p in sorted(destination.rglob("*")) if p.is_file() and ".cache" not in p.parts and p.name != MARKER}
    if "model_index.json" not in files or not any(n.endswith(".safetensors") for n in files):
        raise ValueError("모델 스냅샷이 불완전합니다.")
    manifest = {"schema_version": 1, "model_id": MODEL_ID, "revision": revision, "files": files}
    atomic_json(marker, manifest)
    return manifest


def verify_model(root: Path) -> dict:
    marker = root / MARKER
    if not marker.is_file():
        raise ValueError("saeruq-model.json이 없습니다. prepare-model로 모델을 준비하세요.")
    manifest = json.loads(marker.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or manifest.get("model_id") != MODEL_ID or not re.fullmatch(r"[0-9a-f]{40}", manifest.get("revision", "")):
        raise ValueError("지원되지 않는 모델 manifest입니다.")
    files = manifest.get("files", {})
    if not isinstance(files, dict) or "model_index.json" not in files or not any(n.endswith(".safetensors") for n in files):
        raise ValueError("불완전한 모델 manifest입니다.")
    for name, expected in files.items():
        path = confined(root, name)
        if not path.is_file() or digest(path) != expected:
            raise ValueError(f"모델 파일 무결성 확인 실패: {name}")
    return manifest
