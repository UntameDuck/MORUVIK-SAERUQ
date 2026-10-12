# 로컬 검증 결과

검증일: 2026-10-12. Windows 11, Python 3.12.15, Pillow 12.3.0, pytest 9.1.1.

## 통과한 항목

- pytest: **30 passed**. 한글 CLI, 입력 거부, 결정적 샘플 생성, full/crop 편집, 회색 마스크 혼합, 원본 보존, 취소와 다음 작업, 실패·언로드, 출력 크기 검증, 파일 변조·경로 이탈 감지, 모델 manifest 해시, 평가 저장을 확인했다.
- `scripts/check-local.ps1` 실행 성공: 환경 진단, 평가 입력 준비, 50개 mock 작업 완료.
- 평가 성공 50 / 실패 0. 모든 실행의 파일 해시를 확인했다.
- 부분 편집 15건에서 마스크 값이 0인 영역의 픽셀 변화 합계 **0**.
- CPU 개발 환경에 torch/diffusers/모델 가중치를 설치하지 않았다.
- Linux 준비 스크립트는 LF 줄바꿈을 확인했다. 실제 Linux/GPU 실행은 아직 하지 않았다.
- 두 프로젝트의 로컬 Git main 초기화 완료. 원격 연결·커밋·push는 아직 하지 않았다.
- .venv, runs, 모델 가중치가 Git ignore에 포함되는지 확인했다.

실행 보고서: `runs/local-check-3babbada6ece4ca7833cc2a95a5a59fd.json` (로컬 전용, Git 제외).
개발 환경의 설치 버전은 `requirements-cpu.lock.txt`에 기록했다. 새 CPU 환경을 같은 버전으로 준비하려면 lock 파일 설치 후 패키지를 `--no-deps`로 설치한다.

## 아직 검증하지 않은 항목

- Vast.ai 의존성 설치와 NVIDIA CUDA/BF16 실행.
- 실제 FLUX 이미지 생성·편집 및 LoRA 적용.
- 정밀 편집의 지시 충실도·경계 자연스러움.
- 실제 GPU 속도·VRAM, 시스템 RAM 사용량, OOM 복구.
- 운영체제의 외부 통신 차단 상태에서 추론.
- Windows NVIDIA에서 배포 재현과 오프라인 실행.

샘플 평가의 backend는 is_ai=false이며, 성공 수치를 실제 AI 품질·성능으로 해석하지 않는다. GPU 버전 후보는 PyPI 의존성 메타데이터에서 Hub 범위 등의 충돌을 조정했지만 실제 설치·실행 검증이 필요하다.
