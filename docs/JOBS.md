# SAERUQ v0.2 작업 실행

GUI와 분리된 엔진 작업 계층이다. 대기열은 로컬 SQLite DB이며 GPU 없이도 mock으로 전체 흐름을 시험할 수 있다.

## 상태

```text
queued → running → succeeded / failed / cancelled
   └────────────→ cancelled
running + 실행기 강제 종료 → 다음 시작 시 완료 기록 검증 또는 interrupted
failed / cancelled / interrupted → retry → 새로운 queued 작업
```

작업 등록은 DB에 기록하고 편집 입력을 대기열의 inputs 폴더에 복사한다. 실행 및 재시도 전에 사본 해시를 검사한다. 원본 입력을 이후 수정하거나 삭제해도 사본으로 실행한다. 사본이 변조되면 실행을 거부한다. 등록 도중 디스크 오류가 생기면 아직 DB에 등록되지 않은 입력 사본이 남을 수 있다.

## CLI

```powershell
cd E:\Project_Github\SAERUQ
$job = .\.venv\Scripts\python.exe -m saeruq enqueue --backend mock --prompt "파란 꽃병" --source datasets/fixtures/source.png --mask datasets/fixtures/mask.png --edit-mode crop | ConvertFrom-Json
.\.venv\Scripts\python.exe -m saeruq jobs --status queued
.\.venv\Scripts\python.exe -m saeruq worker --backend mock --max-jobs 1
.\.venv\Scripts\python.exe -m saeruq job $job.id
```

사용할 명령:

- `enqueue`: 생성 또는 편집 요청 등록. backend를 반드시 지정한다.
- `jobs`: 최신순 작업 목록. worker는 등록 순서로 처리한다.
- `job <ID>`: 작업 상태·결과·실행 단계·진행률.
- `cancel <ID>`: 대기 중이면 즉시 취소. 실행 중이면 요청만 기록하고 다음 계산 경계에서 중단.
- `retry <ID>`: 실패·취소·중단 작업을 새 ID로 등록. 성공 작업이나 실행 중 작업에는 사용할 수 없다.
- `worker`: 같은 backend의 대기 작업 처리. 기본은 대기열이 비면 종료하며, `--watch`로 계속 기다린다.

모든 작업 명령은 `--queue <경로>`를 공유해야 한다. worker의 `--runs`는 결과 저장 위치다. 기본 경로는 각각 runs/queue와 runs다. worker 종료 코드는 정상 0, 신규/복구 실패·복구 중단·정리 오류 1, 입력 오류 2, Ctrl+C 130이다. 사용자 요청으로 취소한 개별 작업만 있는 경우 worker 자체는 정상 종료할 수 있다.

## Python 호출

```python
from pathlib import Path
from saeruq.backends.mock import MockBackend
from saeruq.contracts import Request
from saeruq.jobs import JobStore, work
from saeruq.runtime import Engine

store = JobStore(Path("runs/queue"))
job = store.submit(Request(prompt="a vase", seed=42), backend="mock")
summary = work(store, Engine(MockBackend(), Path("runs")), "mock")
print(store.inspect(job["id"]), summary)
```

`work`는 종료 시 backend를 언로드한다. 여러 스레드·프로세스에서 등록·조회·취소할 수 있으며, 같은 대기열에는 OS 파일 잠금으로 실행기 하나만 허용한다. DB 연결은 호출마다 생성·정리한다.

## 중단 복구 정책

이전 실행기의 OS 잠금이 풀린 뒤 새 worker가 시작하면 running 작업을 확인한다.

1. run.json이 완료 상태이고 ID·파일 해시가 맞으면 해당 성공/실패/취소 기록을 복구한다.
2. 기록이 없거나 부분 기록이거나 해시가 다르면 interrupted로 표시한다.
3. GPU 작업은 자동으로 재실행하지 않는다. 사용자가 retry를 선택한다.
4. queued 작업은 계속 실행한다. recovery 요약에는 복구된 상태별 개수가 포함된다.

OS 잠금 파일은 삭제하지 않는다. 삭제하면 다른 잠금 파일로 동시에 실행할 위험이 있다. 잠금 해제는 실행기 종료로 이루어진다. 강제 종료의 검증은 현재 Windows에서 수행했으며 Linux flock 경로는 Vast.ai에서 추가 확인한다.

## 경계와 후속 과제

- 같은 queue의 소비만 보호한다. 다른 queue·직접 generate·다른 프로그램의 GPU 작업은 별도 운영 정책으로 제한한다.
- 네트워크 공유 폴더 및 다른 장비로 DB를 이동하는 동작은 지원하지 않는다. 경로를 포함하므로 Vast.ai에서 대기열을 새로 등록한다.
- worker의 모델·LoRA·offload 설정이 소비하는 작업에 적용된다. 작업마다 모델 설정을 다르게 라우팅하는 기능은 후속이다.
- 이미지·DB를 하나의 파일시스템 트랜잭션으로 묶지는 않는다. 결과 이미지는 개별 원자적 교체 후 해시와 완료 상태를 기록한다.
- 강제 종료 시 진행 중 GPU 연산을 재개하지 않으며, 중단 지점 이후부터 이어 계산하는 기능도 없다.
- HTTP API, UI, 우선순위·다중 GPU 스케줄링은 아직 없다.
