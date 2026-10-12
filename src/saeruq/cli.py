import argparse
import json
import signal
import sys
from importlib.util import find_spec
from pathlib import Path

from saeruq.backends.mock import MockBackend
from saeruq.contracts import CancellationToken, Request
from saeruq.evaluation import evaluate
from saeruq.fixtures import make_fixtures
from saeruq.models import MODEL_ID, prepare_model, verify_model
from saeruq.runtime import Engine, environment, verify_run


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="saeruq", description="SAERUQ 독립 이미지 엔진")
    commands = p.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor", help="환경 확인 (기본: CUDA 미초기화)")
    doctor.add_argument("--gpu", action="store_true")
    fixtures = commands.add_parser("fixtures", help="자체 합성 평가 이미지 및 50개 작업 준비")
    fixtures.add_argument("--output", type=Path, default=Path("datasets/fixtures"))
    info = commands.add_parser("model-info", help="온라인: 현재 모델 commit SHA 조회")
    prepare = commands.add_parser("prepare-model", help="온라인: 지정 revision 다운로드 및 해시 작성")
    prepare.add_argument("--revision", required=True)
    prepare.add_argument("--output", type=Path, default=Path("models/flux-klein-4b"))
    verify = commands.add_parser("verify-model")
    verify.add_argument("path", type=Path)
    run_verify = commands.add_parser("verify-run")
    run_verify.add_argument("path", type=Path)
    for name in ("generate", "edit", "evaluate"):
        command = commands.add_parser(name)
        command.add_argument("--backend", choices=["mock", "flux"], required=True, help="반드시 명시: mock 출력은 AI가 아님")
        command.add_argument("--model-path", type=Path)
        command.add_argument("--cpu-offload", action="store_true")
        command.add_argument("--lora", type=Path)
        command.add_argument("--runs", type=Path, default=Path("runs"))
        if name == "evaluate":
            command.add_argument("--manifest", type=Path, required=True)
            command.add_argument("--report", type=Path, required=True)
        else:
            command.add_argument("--prompt", required=True)
            command.add_argument("--seed", type=int, default=0)
            command.add_argument("--width", type=int, default=768)
            command.add_argument("--height", type=int, default=768)
        if name == "edit":
            command.add_argument("--source", type=Path, required=True)
            command.add_argument("--mask", type=Path)
            command.add_argument("--edit-mode", choices=["full", "crop"], default="full")
            command.add_argument("--context", type=int, default=64)
    return p


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args()
    engine = None
    token = CancellationToken()
    previous_signal = None
    try:
        if args.command == "doctor":
            result = environment()
            result["torch_installed"] = find_spec("torch") is not None
            result["gpu"] = {"checked": False}
            if args.gpu:
                import torch
                result["gpu"] = {"checked": True, "cuda_available": torch.cuda.is_available(), "devices": [{"name": torch.cuda.get_device_name(i), "vram_bytes": torch.cuda.get_device_properties(i).total_memory} for i in range(torch.cuda.device_count())]}
        elif args.command == "fixtures":
            result = {"manifest": str(make_fixtures(args.output).resolve()), "task_count": 50, "is_ai": False}
        elif args.command == "model-info":
            from huggingface_hub import HfApi
            model = HfApi().model_info(MODEL_ID)
            result = {"model_id": MODEL_ID, "revision": model.sha}
        elif args.command == "prepare-model":
            manifest = prepare_model(args.output, args.revision)
            result = {"model_id": manifest["model_id"], "revision": manifest["revision"], "files": len(manifest["files"]), "directory": str(args.output.resolve())}
        elif args.command == "verify-model":
            manifest = verify_model(args.path)
            result = {"model_id": manifest["model_id"], "revision": manifest["revision"], "verified_files": len(manifest["files"])}
        elif args.command == "verify-run":
            result = verify_run(args.path)
        else:
            if args.backend == "mock":
                if args.model_path or args.lora or args.cpu_offload:
                    raise ValueError("mock backend에는 모델·LoRA·오프로딩 옵션을 사용할 수 없습니다.")
                backend = MockBackend()
            else:
                if not args.model_path:
                    raise ValueError("flux backend에는 --model-path가 필요합니다.")
                from saeruq.backends.flux import FluxBackend
                backend = FluxBackend(args.model_path, args.cpu_offload, args.lora)
            engine = Engine(backend, args.runs)
            previous_signal = signal.signal(signal.SIGINT, lambda *_: token.cancel())
            if args.command == "evaluate":
                result = evaluate(engine, args.manifest, args.report, token)
                print(json.dumps({"report": str(args.report.resolve()), "is_ai": result["backend"]["is_ai"], "succeeded": result["succeeded"], "failed": result["failed"]}, ensure_ascii=False))
                return 130 if result["cancelled"] else (0 if result["failed"] == 0 else 1)
            result = engine.run(Request(args.prompt, args.seed, args.width, args.height,
                                        getattr(args, "source", None), getattr(args, "mask", None),
                                        getattr(args, "edit_mode", "full"), getattr(args, "context", 64)), token)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return {"succeeded": 0, "failed": 1, "cancelled": 130}[result["status"]]
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, ImportError, KeyError, RuntimeError) as error:
        print(json.dumps({"error": type(error).__name__, "message": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    finally:
        if previous_signal is not None:
            signal.signal(signal.SIGINT, previous_signal)
        if engine:
            engine.close()


if __name__ == "__main__":
    raise SystemExit(main())
