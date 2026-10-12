# 로컬 검증 결과

검증일: 2026-10-12. Windows 11, Python 3.12.15, Pillow 12.3.0, pytest 9.1.1.

## v0.1 초기 검증

- pytest: **30 passed**. 한글 CLI, 입력 거부, 결정적 샘플 생성, full/crop 편집, 회색 마스크 혼합, 원본 보존, 취소와 다음 작업, 실패·언로드, 출력 크기 검증, 파일 변조·경로 이탈 감지, 모델 manifest 해시, 평가 저장을 확인했다.
- `scripts/check-local.ps1` 실행 성공: 환경 진단, 평가 입력 준비, 50개 mock 작업 완료.
- 평가 성공 50 / 실패 0. 모든 실행의 파일 해시를 확인했다.
- 부분 편집 15건에서 마스크 값이 0인 영역의 픽셀 변화 합계 **0**.
- CPU 개발 환경에 torch/diffusers/모델 가중치를 설치하지 않았다.
- Linux 준비 스크립트는 LF 줄바꿈을 확인했다. 실제 Linux/GPU 실행은 아직 하지 않았다.
- 이 초기 검증 시점에는 로컬 Git main만 초기화했다. 이후 MORUVIK-APP과 MORUVIK-SAERUQ 원격 연결 및 첫 push를 완료했다.
- .venv, runs, 모델 가중치가 Git ignore에 포함되는지 확인했다.

실행 보고서: `runs/local-check-3babbada6ece4ca7833cc2a95a5a59fd.json` (로컬 전용, Git 제외).
개발 환경의 설치 버전은 `requirements-cpu.lock.txt`에 기록했다. 새 CPU 환경을 같은 버전으로 준비하려면 lock 파일 설치 후 패키지를 `--no-deps`로 설치한다.

## v0.2 작업 엔진 검증

동일 PC의 Python 3.12.15 환경에서 v0.2.0 패키지를 설치하고 검증했다.

- 전체 pytest: **59 passed in 34.33s**.
- 대기열을 새 연결과 별도 CLI 프로세스에서 열어 등록·조회·취소·재시도했다.
- 실제 테스트 자식 프로세스를 종료한 뒤 OS 잠금 해제와 interrupted 기록을 확인했다. 다음 대기 작업은 정상 처리됐다.
- 결과 기록까지 완료하고 DB 반영 전에 종료한 경우, 파일 ID·해시 검증 후 성공 기록을 복구했다. 같은 추론을 재실행하지 않았다.
- 변조한 입력 사본은 모델 로딩 전에 거부하고, 변조한 완료 결과는 성공으로 복구하지 않았다.
- 실행 중 취소, 이후 등록된 작업을 받는 watch 모드, 실행기 종료 요청을 확인했다.
- 메모리 오류를 흉내 낸 실패 뒤 다음 작업이 실행됐다. 정리 오류가 원래 추론 오류를 덮어쓰지 않았다.
- 실제 GPU 없이 FLUX 어댑터의 호출 계약을 doubles로 검증했다: local_files_only, safetensors, BF16, 4-step, 참조 이미지 전달, CPU offload, 로컬 LoRA, 단계 취소, 부분 로딩 실패 뒤 재로딩.
- 별도 CLI smoke: 생성·편집·취소 후 재시도 3개 작업을 두 worker 프로세스에 나눠 처리했다. 성공 3 / 실패 0, 편집 마스크 밖 변화 0, 결과 해시 확인 성공.

Smoke 보고서: `runs/v02-smoke-2971a7bde67d4c19a2f1957bcc86f8ce/smoke-report.json` (Git 제외).

이 결과는 로컬 작업 엔진과 어댑터 호출 조건을 검증한다. 실제 FLUX 가중치 로딩·GPU 추론·LoRA 품질·실제 GPU OOM 대응을 검증한 결과가 아니다.

## 아직 검증하지 않은 항목

- Vast.ai 의존성 설치와 NVIDIA CUDA/BF16 실행.
- 실제 FLUX 이미지 생성·편집 및 LoRA 적용.
- 정밀 편집의 지시 충실도·경계 자연스러움.
- 실제 GPU 속도·VRAM, 시스템 RAM 사용량, OOM 복구.
- 운영체제의 외부 통신 차단 상태에서 추론.
- Windows NVIDIA에서 배포 재현과 오프라인 실행.

샘플 평가의 backend는 is_ai=false이며, 성공 수치를 실제 AI 품질·성능으로 해석하지 않는다. GPU 버전 후보는 PyPI 의존성 메타데이터에서 Hub 범위 등의 충돌을 조정했지만 실제 설치·실행 검증이 필요하다.
