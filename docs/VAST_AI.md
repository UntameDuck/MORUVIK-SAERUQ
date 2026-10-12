# Vast.ai 엔진 검증 절차

GPU 대여 플랫폼은 Vast.ai로 확정했다. 아래는 실행 준비서이며 아직 인스턴스를 대여하지 않았다.

## 첫 환경

- 단일 NVIDIA GPU, RTX 4090 24GB 우선 후보.
- RAM 64GB, 여유 디스크 150~200GB를 초기 계획으로 잡고 실제 파일 크기에 맞춰 조정.
- 첫 실험은 On-demand. 매물별 CUDA·드라이버·시스템·스토리지·전송 조건을 예약 전에 확인.
- 첫 실험 관리 상한 USD 20~30은 계획 제안이며 견적 또는 승인된 결제 금액이 아니다.
- 현재 엔진은 BF16 CUDA 경로만 제공한다. 양자화 지원·16GB 최소 사양을 확정하지 않는다.

## 코드 준비

엔진 저장소를 인스턴스로 clone한다. .venv, runs, models, 비밀키는 소스 전송에서 제외한다. 개발 중 실제 서비스 API를 외부에 노출할 필요는 없다.

```bash
git clone https://github.com/UntameDuck/MORUVIK-SAERUQ.git
cd MORUVIK-SAERUQ
bash scripts/bootstrap-vast.sh
source .venv/bin/activate
saeruq doctor --gpu
python -m pytest -q
saeruq fixtures
```

GPU extras는 2026-10-12 PyPI에서 조회한 버전 후보를 고정한 것이다. 설치 및 GPU 실행 성공 전 호환성 검증 완료로 취급하지 않는다. 성공 환경의 pip freeze와 doctor JSON을 실험 결과와 함께 보관한다.

## 모델 준비 (온라인)

```bash
saeruq model-info
# 위 명령의 revision 값을 다음 명령에 넣는다. main 대신 40자리 SHA 사용.
saeruq prepare-model --revision <40자리-commit-SHA> --output models/flux-klein-4b
saeruq verify-model models/flux-klein-4b
```

모델 전체에서 Diffusers 실행 구성만 다운로드하고 각 파일의 SHA-256을 saeruq-model.json에 기록한다. 가중치는 Git에 포함하지 않는다. 모델 접근 권한·인증이 필요한 경우 제공자의 절차를 따르며 토큰을 코드나 명령 기록에 넣지 않는다.

## 첫 생성·부분 편집

```bash
saeruq generate --backend flux --model-path models/flux-klein-4b --prompt "A ceramic vase on a wooden table" --seed 42 --width 1024 --height 1024
saeruq edit --backend flux --model-path models/flux-klein-4b --source datasets/fixtures/source.png --mask datasets/fixtures/mask.png --prompt "Change the orange vase to blue, preserve the background" --seed 42 --edit-mode full
saeruq edit --backend flux --model-path models/flux-klein-4b --source datasets/fixtures/source.png --mask datasets/fixtures/mask.png --prompt "Change the orange vase to blue, preserve the background" --seed 42 --edit-mode crop
saeruq evaluate --backend flux --model-path models/flux-klein-4b --manifest datasets/fixtures/evaluation.json --report runs/flux-evaluation.json
```

메모리 여유가 부족하면 --cpu-offload를 명시해 새 실행과 비교한다. 이 버전은 OOM 뒤 자동 해상도 변경·재시도를 하지 않는다. 실패 기록을 보존하고 backend를 언로드한다. 필요하면 해상도 또는 오프로딩 옵션을 변경해 새로운 작업으로 실행한다.

로드 시간에는 모델 파일 무결성 검사와 최초 로딩이 포함된다. 추론 VRAM 지표는 PyTorch allocator의 peak allocated/reserved이며 OS 전체 VRAM이나 시스템 RAM 측정값이 아니다. 별도 시스템 모니터링을 함께 기록한다. evaluate에서 모델은 같은 프로세스 안에 유지되어 첫 작업과 이후 warm 작업을 구분할 수 있다.

## 오프라인 확인

추론 어댑터는 HF_HUB_OFFLINE·TRANSFORMERS_OFFLINE과 local_files_only를 사용한다. 이것만으로 OS 전체의 외부 통신 차단을 증명하지 않는다. 모델·패키지를 준비한 다음 SSH 등 관리 연결을 유지할 수 있는 시험 방식에서 프로세스 외부 통신을 차단하고 재실행한다. 단순 환경 변수 적용 시험과 네트워크 차단 시험을 각각 기록한다.

이 Linux 결과는 Windows 오프라인 제품 검증과 별도다.

## LoRA는 다음 단계

먼저 기본 모델의 실패 유형을 확인한다. 이후 Base 4B와 허가된 데이터로 학습하고 회수한 .safetensors 파일을 `--lora <로컬 파일>`로 적용한다. 이번 단계에는 학습 실행기나 학습된 SAERUQ 가중치가 없다. 학습 도구는 첫 GPU 결과 이후 확정한다.

## 실험 종료

1. 결과 PNG, run.json, 평가 보고서, 환경 lock, LoRA를 회수한다.
2. 로컬에서 verify-run으로 파일 무결성을 확인한다.
3. GPU 시간·스토리지·전송료와 실패 비용을 기록한다.
4. 백업 확인 후 인스턴스를 정리하고 별도 볼륨 등 남은 과금 자원을 확인한다.

공식 근거: [Klein 모델 카드](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B), [Diffusers API](https://huggingface.co/docs/diffusers/api/pipelines/flux2), [모델 다운로드](https://huggingface.co/docs/huggingface_hub/guides/download), [BFL LoRA 가이드](https://huggingface.co/blog/black-forest-labs/flux-2-klein-lora), [Vast.ai 저장소 과금](https://docs.vast.ai/guides/instances/storage/types).
