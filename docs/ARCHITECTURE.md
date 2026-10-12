# SAERUQ 엔진 구조

기준: 2026-10-12. MORUVIK UI 개발을 보류하고 엔진을 우선 독립시킨다.

```text
CLI 또는 Python 호출자
       ↓ Request / CancellationToken
Engine (단일 인스턴스 직렬 실행)
  ├─ 입력 검증·원본/마스크 사본
  ├─ full / crop 편집 geometry
  ├─ backend load → infer → mask composite
  └─ 상태·PNG·환경·성능·파일 해시 저장
       ↓ Backend 계약
  MockBackend   CPU 샘플, is_ai=false
  FluxBackend   로컬 Klein 4B, GPU 검증 대기
```

backend 계약은 describe/load/infer/metrics/unload다. capabilities에는 native_mask_edit=false를 명시한다. 원본 보존 합성은 엔진 계층이 처리한다. 이미지 편집 모델이 선택 영역 밖을 변경하더라도 최종 합성에서 마스크 값이 0인 픽셀은 원본을 사용한다. 경계 자연스러움·객체 위치·편집 정확성은 별도 품질 평가다.

crop 경로는 마스크 bounding box를 context만큼 확장하고 최대 요청 변 길이에 맞춰 16 배수로 크기를 조정한다. 생성 결과를 crop 원래 해상도로 되돌린 뒤 원본 좌표에 붙인다. 최소 해상도·반올림으로 비율이 약간 달라질 수 있다. geometry 기록에서 실제 inference_size와 crop_box를 확인한다.

실행 기록은 schema_version=1이며 상태는 running/succeeded/failed/cancelled다. run.json은 임시 파일 작성과 원자적 교체로 저장한다. 이미지 파일과 전체 실행 디렉터리의 트랜잭션 저장은 아니므로 강제 종료 후 부분 파일이 남을 수 있다. 성공 결과는 result.png 해시 기록으로 확인한다.

## 구현 및 후속 순서

1. 완료 대상: 독립 패키지, CPU mock, 입력·합성, 결과 기록, 평가, 로컬 검증.
2. 다음: Vast.ai BF16 생성·편집 실행, cold load·warm inference·VRAM 측정.
3. 다음: full/crop 편집 비교 및 허가된 사진·일러스트 평가 입력 확장.
4. 이후: 필요 과제의 Base 4B LoRA 학습, 로컬 추론용 모델에서 효과 비교.
5. 이후: 양자화·저메모리 프로필, 취소 지연·OOM 복구 정책, 보호 영역·정밀 편집 경로 개선.
6. 앱 단계: MORUVIK의 서비스/API·작업 대기열·캔버스·레이어 연결.

모델 로딩/학습 성공, Windows 오프라인 동작, 8/12/16GB 지원, 성능 수치는 아직 검증되지 않았다. 현재 PC에서 샘플이 성공해도 위 항목의 통과를 의미하지 않는다.
