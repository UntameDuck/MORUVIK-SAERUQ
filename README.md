# SAERUQ

MORUVIK에서 독립한 로컬 이미지 생성·편집 엔진. 현재는 **v0.2 로컬 작업 실행 엔진**이다. 독립적으로 사전학습한 모델을 의미하지 않는다.

## 프로젝트 분리

```text
E:/Project_Github/
  MORUVIK/   # 앱 기획 및 향후 사용자 인터페이스
  SAERUQ/    # 이 저장소: Python 엔진·CLI·평가·GPU 실행 도구
```

SAERUQ는 MORUVIK 파일이나 UI 프레임워크에 의존하지 않는다. 향후 MORUVIK이 Python API 또는 별도 서비스 계층을 통해 엔진을 사용한다.

원격 저장소: [MORUVIK-SAERUQ](https://github.com/UntameDuck/MORUVIK-SAERUQ). 앱 저장소는 [MORUVIK-APP](https://github.com/UntameDuck/MORUVIK-APP)이다.

## 현재 가능한 것

- GPU 없이 실행되는 **명시적인 mock backend**: 결정적 샘플 생성·이미지 변형. AI 출력 아님.
- 이미지 입력, grayscale 마스크, 원본 보존 합성.
- 경로 A `full`: 전체 이미지 편집 결과를 선택 영역에 합성.
- 경로 B `crop`: 주변 문맥을 포함한 영역 편집 결과를 원래 좌표에 복원해 합성.
- 실행별 원본·마스크·결과 PNG, 프롬프트·시드·환경·시간·SHA-256 기록.
- 직렬 엔진 실행, 협력적 취소, 실패 기록, backend 언로드.
- SQLite 작업 대기열: 등록·조회·취소·재시도, 입력 사본 보존, 단일 실행기와 프로세스 종료 후 상태 복구.
- 로딩·추론 단계별 시간과 GPU allocator의 메모리 지표 기록, 정리 실패 시 원래 오류 보존.
- 50개 자체 합성 평가 작업 준비, 실행 및 무결성 검사. 사진 품질은 추가 입력으로 평가해야 한다.
- 로컬 FLUX.2 Klein 4B용 **GPU 미검증** 어댑터: BF16, 4-step, CPU offload 선택, 로컬 LoRA.
- 정확한 모델 commit SHA로 다운로드하는 준비 명령과 오프라인 로딩·무결성 확인.

정밀 인페인팅, 양자화, 자동 객체 선택, 자체 학습 가중치, 앱 UI, HTTP 서비스는 현재 구현 범위에 포함되지 않는다. 생성·편집 품질과 실제 GPU 속도는 Vast.ai에서 확인해야 한다. 취소는 계산 경계에서 적용되며 로딩·단일 GPU 연산을 즉시 중단하지 못한다.

## 현재 PC에서 실행

Python 3.11~3.14가 필요하며 현재 PC에서는 별도 프로젝트 전용 venv에 Python 3.12.15로 검증한다. PyTorch나 모델 가중치는 CPU 개발 설치에 포함하지 않는다.

이미 준비된 환경에서 PowerShell:

```powershell
cd E:\Project_Github\SAERUQ
.\.venv\Scripts\python.exe -m saeruq doctor
.\.venv\Scripts\python.exe -m saeruq fixtures
.\.venv\Scripts\python.exe -m saeruq generate --backend mock --prompt "ceramic vase" --seed 42
.\.venv\Scripts\python.exe -m saeruq edit --backend mock --prompt "blue vase" --source datasets/fixtures/source.png --mask datasets/fixtures/mask.png --edit-mode full
.\.venv\Scripts\python.exe -m saeruq edit --backend mock --prompt "blue vase" --source datasets/fixtures/source.png --mask datasets/fixtures/mask.png --edit-mode crop
.\.venv\Scripts\python.exe -m saeruq evaluate --backend mock --manifest datasets/fixtures/evaluation.json --report runs/mock-evaluation.json
.\.venv\Scripts\python.exe -m pytest -q
```

새 환경은 `scripts/bootstrap-local.ps1 -PythonPath <Python 실행 파일>`로 준비한다. 검증 묶음은 `scripts/check-local.ps1`을 실행한다. 보고서 경로가 이미 존재하면 evaluate는 덮어쓰지 않으므로 새 경로를 지정한다. `Ctrl+C`는 실행 중 작업을 취소한다.

마스크는 원본과 같은 해상도의 grayscale 이미지다. 흰색=편집, 검정=보존, 회색=혼합. 투명도는 마스크로 해석하지 않는다. EXIF 방향은 적용해 읽으며, 이 버전은 RGB 8비트 편집을 대상으로 한다. 원본 파일은 변경하지 않는다.

## 작업 대기열

```powershell
$job = .\.venv\Scripts\python.exe -m saeruq enqueue --backend mock --prompt "ceramic vase" --width 768 --height 768 | ConvertFrom-Json
.\.venv\Scripts\python.exe -m saeruq jobs
.\.venv\Scripts\python.exe -m saeruq worker --backend mock
.\.venv\Scripts\python.exe -m saeruq job $job.id
```

worker는 기본적으로 같은 backend의 대기 작업을 순서대로 처리하고 종료한다. `--watch`는 새 작업을 기다리며, `Ctrl+C`로 현재 작업 취소 후 종료한다. `cancel <ID>`와 `retry <ID>`로 상태를 관리한다. 재시도는 새로운 작업 ID로 원래 이력을 보존한다.

대기열은 같은 PC의 로컬 파일시스템에서 사용한다. 같은 queue 경로에는 실행기 하나만 허용하며, 서로 다른 queue나 직접 generate 호출 간 GPU 동시 사용까지 제한하지는 않는다. GPU당 실행기 하나를 운영한다. 자세한 정책과 Python API는 [작업 실행 안내](docs/JOBS.md)를 따른다.

## Vast.ai에서 실제 모델 실행

[Vast.ai 실행 안내](docs/VAST_AI.md)를 따른다. GPU 대여·결제·모델 다운로드는 이번 로컬 준비 과정에서 실행하지 않았다.

온라인에서 의존성과 모델을 준비하고, 추론에서는 로컬 모델만 사용한다. 기본 가중치와 LoRA의 다운로드·설치는 제품의 오프라인 실행 단계와 구분한다. 초기 GPU 의존성 버전은 배포 후보이며 실제 GPU 호환성을 검증한 뒤 환경 lock을 확정한다.

## Python API

```python
from pathlib import Path
from saeruq.backends.mock import MockBackend
from saeruq.contracts import Request
from saeruq.runtime import Engine

engine = Engine(MockBackend(), Path("runs"))
try:
    record = engine.run(Request(prompt="ceramic vase", seed=42))
    print(record["status"], record["directory"])
finally:
    engine.close()
```

실패·취소는 실행 기록의 status로 반환한다. 입력 계약 위반은 ValueError로 거부한다. 실제 어댑터는 `FluxBackend(model_path, offload=False, lora=None)`를 사용한다. 한 Engine 인스턴스의 작업은 직렬 실행된다. JobStore와 worker를 사용하면 같은 대기열을 복수 프로세스가 동시에 소비하지 못하도록 보호한다.

## 실행 결과 구조

```text
runs/<UTC 시간>-<고유 ID>/
  run.json      # 상태·요청·환경·backend·geometry·metrics·해시
  source.png    # 편집 입력 RGB 사본 (편집 시)
  mask.png      # 선택 마스크 사본 (부분 편집 시)
  raw.png       # 합성 전 backend 출력
  result.png    # 최종 결과 (성공 시)
```

`saeruq verify-run <실행 폴더>`로 기록된 파일 해시를 검사한다. SHA-256은 파일 손상 검사용이며 디지털 서명은 아니다. 직접 generate/edit를 강제 종료하면 running 기록이 남을 수 있다. 작업 대기열을 사용하면 다음 worker 시작 시 완료 결과를 검사해 복구하거나 interrupted로 기록한다. 중단 작업은 자동 재실행하지 않는다. 원본 요청의 로컬 경로가 기록에 포함되므로 외부 공유 전 확인한다.

## 문서

- [구조·개발 순서](docs/ARCHITECTURE.md)
- [Vast.ai 실행 안내](docs/VAST_AI.md)
- [로컬 검증 결과](docs/LOCAL_VALIDATION.md)
- [작업 대기열·복구](docs/JOBS.md)

코드의 공개 라이선스는 아직 결정하지 않았다. 기반 모델·외부 패키지의 라이선스는 각 제공자의 조건을 따른다. 모델·데이터·비밀키는 Git에 올리지 않는다.
