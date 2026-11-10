# 공통 데이터 작업 진행 상황

## CSV 전달 데이터셋

학습·곡·사용자·평가 데이터 출력을 UTF-8 BOM CSV 7개로 통일했다. listenbrainz-8week-v2는 저장소 옆 공유 데이터셋 폴더에 있다. 모든 CSV의 원본 기준 누락·초과는 0건이며, v1과도 모든 값·순서가 일치했다. 관련 회귀 테스트 26개와 실제 패키지 로딩 예제를 통과했다. [변경 이유·검증 결과·직접 확인](reports/csv-dataset-validation.md)

작업 브랜치는 work/csv-datasets이고 [Issue #11](https://github.com/ML-Team4/ML-Team4-Preprocessing/issues/11)에 연결된다. 학습·평가에는 CSV 전달본을 사용한다. 다음 작업은 모델 담당자용 문서 보완이다.

2026-11-10 (Asia/Seoul) 갱신. [Issue #9](https://github.com/ML-Team4/ML-Team4-Preprocessing/issues/9)의 전체 학습·평가 패키지는 생성과 독립 검증을 마쳤다.

## 이전 전달 형식의 원본 대조 기록

- 정제 근거와 시간 분할 검증 [PR #8](https://github.com/ML-Team4/ML-Team4-Preprocessing/pull/8)의 main 머지 `0e4701ce7565d283f22ab80c637d20bcaf0c6504`를 확인했다. `git fetch origin` 후 같은 커밋에서 로컬 `work/dataset-validation`을 시작했다.
- `listenbrainz-8week-v1`을 생성하고 독립 검증을 완료했다. 패키지는 `.cache/datasets/listenbrainz-8week-v1/`에 있으며 팀에 전달할 수 있다. [전체 전처리 결과](reports/preprocessing-report.md)
- 원본 9,350,628행에서 중복 초과 537,575행을 제외하고 8,813,053사건을 보존했다. 결측·기간 밖·빈 이름 제외는 각 0행이다. train 6,623,584건, validation 1,104,514건, test 1,084,955건은 시간 분할 보고서와 각 차이 0건이다. 저인기·동시각 다곡·고활동 신호는 보존했다. 이름 튜플과 기존 표본의 범위가 해석을 제한한다. [정제 영향과 분모](reports/temporal-split-review.md#정제-규칙별-판단)
- 원본으로 재계산한 학습 사용자 5,000명, 곡 1,525,005개, 고유 쌍 2,739,292개의 ID·표시명·인기도·횟수가 저장 파일과 일치했다. CSR의 shape은 `(5000, 1525005)`이고 비제로 2,739,292개의 값과 좌표가 모두 일치했다. validation 3,922명·193,323쌍, test 3,852명·201,203쌍의 CSV·Parquet 누락과 초과는 각각 0이다.
- 원본 시간 분할 재검사, 회귀 검사 25개, 의존성 검사, 패키지 README 예제를 통과했다.
- NPZ에 저장된 여분 배열 원소와 manifest 메타데이터 오류를 탐지하도록 독립 검사를 보강했다. 오류 주입 48건을 모두 거부했고 정상 경계 사례 8건과 int64 CSR 인덱스는 통과했다. Python 정규화로 원본 전체를 별도 재집계한 학습·평가 파일 대조도 차이 0이다.
- 저장소 루트에서 `.venv/bin/python scripts/build_dataset.py --config config/paths.local.toml --output-root .cache/datasets --version listenbrainz-8week-v1`로 생성했다. 같은 위치에는 기존 v1을 보존한다. `.venv/bin/python scripts/validate_dataset.py --config config/paths.local.toml --package .cache/datasets/listenbrainz-8week-v1`로 다시 검사할 수 있다. 재검증 명령과 로딩 예제는 README에 있다. [독립 검증 실행](README.md#전체-원본-기준의-독립-검증)
- 모델 담당자는 데이터 버전과 패키지 README의 로딩 예제를 확인한 뒤 같은 버전으로 시작한다. 데이터 탐색은 `items.parquet`의 학습 청취자 수, `train_counts.npz`의 고유 좌표·횟수, 기간별 정답과 평가 제외 집계를 기준으로 시작할 수 있다. 팀 공유 위치, 실제 모델 학습·추천 결과는 별도 확인 항목이다. [팀 전달 안내](docs/data-preprocessing-handoff.md) · [데이터 탐색 Issue #4](https://github.com/ML-Team4/ML-Team4-Preprocessing/issues/4)

## 원본 점검 완료 기록

- 빈 저장소와 원격 브랜치 부재를 확인했다. 첫 커밋과 기존 미커밋 작업은 없었고 적용되는 AGENTS.md도 없었다. 현재 초기 상태에서 work/data-audit 브랜치를 만들었다.
- 필수 참고 자료 3개와 원본 폴더의 필수 6개 파일 및 기존 점검·추출 코드를 읽었다. 문서의 팀 확인 필요 사항을 확정 사실로 취급하지 않았다.
- Python 가상환경·고정 의존성·경로 예시와 재실행 가능한 원본 점검 코드를 작성하고 전체 전달본에서 실행했다.
- 원본 SHA256, 9,350,628행, 5,000명, 56일, 컬럼/형/UTC 범위, 결측·빈 이름·중복 후보·이름/반복/과도한 활동/비음악 의심 표본을 확인했다.
- 입력 여섯 파일의 실행 전후 해시가 같고 원본을 수정하지 않았음을 확인했다. 전체 공식 덤프는 재다운로드하지 않았다.
- 과거 공백 규칙을 같은 원본에서 재현해 기존 수치와 새 진단값의 차이를 설명했다. 기존 CSV 순위는 유지했다.
- 처리 기준·데이터 계약·팀 전달 초안·코드 기능 설명·자료 대조 문서를 작성했다. 두 개의 의미 있는 소규모 회귀 검사가 통과했다.
- 첫 커밋 준비를 위해 두 진단 JSON은 로컬에 보존하고 해당 두 경로만 Git에서 제외했다. Git에서 읽는 Markdown 보고서와 로컬 재생성 명령으로 문서 안내를 정리했다. 기존 실행 결과·해시 기록은 유지했다.

## 이번에 고정한 데이터 기준

NFC 및 고정된 유니코드 공백 29종 정리, 두 이름 튜플 키, 동일 사용자·시각·정규화 곡의 초과 행만 제거한다. 다른 시각 반복·저인기 항목·모호한 비음악/고청취 신호는 보존한다. 5,000명을 재표본 추출하지 않고 최소 10곡 조건을 재적용하지 않는다. 학습 이력이 0이 된 사용자만 제외 사유를 남기고 보충하지 않는 예외를 명시했다.

train/validation/test는 요청된 UTC 42/7/7일 경계로 고정한다. 학습 카탈로그·ID·대표 이름·청취 횟수·인기도는 train에서만 만든다. 검증 후 train을 확장하지 않는다. 평가 후보·정답·기간별 사용자 정의는 data-contract에 기록했다. 로그·confidence·보정 가중치는 모델 담당자가 계산한다.

## 주요 직접 측정값

| 항목 | 값 |
| --- | --- |
| 원본 / 요청 SHA256 일치 | 9,350,628행 / 일치 |
| 정규화 키 중복 초과 행 | 537,575 |
| 진단 사건 / 전체 항목 | 8,813,053 / 1,856,311 |
| 진단 학습 사건 / 항목 / user-item | 6,623,584 / 1,525,005 / 2,739,292 |
| 진단 단일 청취자 학습 항목 | 1,204,318 (약 79%) |
| 과거 규칙 대비 사건 / 전체 항목 차이 | -4 / -40 |
| 새 규칙과 CSV 학습 고유곡 수 불일치 | 8명 (과거 규칙으로는 0명) |
| 진단 검증 사용자 / 정답 쌍 | 3,922 / 193,323 |
| 진단 테스트 사용자 / 정답 쌍 | 3,852 / 201,203 |

위 표는 당시 임시 진단 집계다. [원본 점검 보고서](reports/raw-data-audit.md)

## 원본 점검 당시 남은 항목

당시 전체 원본의 입력 검증·정제·분할 집계와 합성 입력의 저장·재로딩까지 확인했다. 전체 원본 패키지와 독립 검증 실측은 문서 상단에 이어서 기록했다.

모델 담당자는 전체 학습 카탈로그의 희소 입력 처리, 보정 구성·가중 손실 지원, hyperparameter, 동률·이웃 부족, PDA/IPS/SNIPS 문헌을 확인해야 한다. item_idx 오름차순 동률 처리는 권장안이고 팀 합의로 확정되지 않았다. 이 항목 때문에 공통 데이터 생성 준비를 중단할 필요는 없다.

## 11월 8일 추가 전처리·모델 적합성 검사

`scripts/check_model_readiness.py`를 작성해 전체 원본에서 실행하고 `reports/model-readiness-diagnostics.json`과 `reports/model-readiness-review.md`를 만들었다. JSON은 로컬 생성물로 보관하고 Git에서는 [추가 검토 보고서](reports/model-readiness-review.md)를 읽는다. JSON 재생성 명령은 [README의 점검 실행](README.md#점검-실행)에 있다. 검토 Markdown은 자동 생성되지 않는다. 기본 코드의 규칙을 재사용하며 원래 점검 보고서를 덮어쓰지 않았다. 모델 학습·추천·실제 성능 평가는 수행하지 않았다.

- 사용자 4,959명이 큰 공통 청취 connected component에 속하고 39명은 고립됐다. 학습 고유곡 중앙값은 320개다.
- 테스트 정답 쌍의 약 94.01%에 Top-K 절삭 전 양의 Item-based 유사도 경로가 있다. 사용자별 비율 평균은 약 89.23%이며 실제 Recall이 아니다.
- 단독 청취 곡끼리 cosine similarity=1 동률이 가능하다. 동일 소유 사용자·횟수 패턴이 반복되는 묶음에 1,194,725곡이 속하며 MF도 곡 구별 근거가 부족할 수 있다.
- 동일 사용자·시각 572곡 사례를 확인했다. 학습 동시각 10곡 이상 사건은 2,146건으로 약 0.03%이며 자동 삭제하지 않았다.
- 학습 곡 집합이 같은 두 사용자는 403곡을 공유하고 곡·시각까지 같은 사건이 1,270건이었다. 동기화/유사 계정 의심 사례로 기록하고 자동 병합·삭제하지 않았다.
- 학습 청취 상위 1% 사용자가 사건의 약 19.81%를 차지한다. 원시 횟수 보존과 모델 측 변환·confidence 검증이 필요하다.
- Recall@10 수학적 최대 평균은 검증 약 0.4785, 테스트 약 0.4643이다. 정답 수에 따른 지표 상한이며 모델 결과가 아니다.
- float32 dense 사용자×곡은 약 30.50GB, 곡×곡은 약 9.30TB다. 희소 입력·블록 점수·계산 중 Top-K가 필요하다. 실제 모델 시간/메모리는 미검증이다.

원본 여섯 파일은 전후 해시가 같았다. 원래 표본·평가 정의·처리 규칙은 유지하고 추적용 진단 플래그를 전처리 계획에 추가했다. 기본 회귀 검사 2개에 직접 유사도 경로와 connected component의 차이를 확인하는 검사 1개를 추가해 총 3개가 통과했다. 강한 필터링이나 실제 계정 병합은 별도 근거와 중요한 결정 확인 없이 적용하지 않는다.

## 11월 8일 · 입력 검증·정제·시간 분할

- 기존 [Issue #1 · 공통 전처리와 시간 분할 구현](https://github.com/ML-Team4/ML-Team4-Preprocessing/issues/1)을 재사용했다. 적용되는 AGENTS.md는 없었고 작업 트리는 깨끗했다. origin의 fetch/push 주소가 지정 저장소와 같았다.
- `git fetch origin` 후 최신 `origin/main`에 원본 점검 커밋 `c28cdd191e5b928e4345ff09635e6d3c37e35a80`이 포함됨을 확인했다. 그 main에서 `work/preprocessing`을 만들고 `git push -u origin work/preprocessing`으로 먼저 게시했다. 원격 브랜치 해시를 확인한 뒤 파일을 수정했다. `work/data-audit`는 유지했다.
- 원본 6개 입력의 bytes·SHA256은 Git의 원본 점검 보고서와 모두 같았다. Parquet의 네 컬럼과 정확한 형, CSV 헤더·5,000명·원본 사용자 집합·표시 번호의 일대일 대응을 검증했다. 원본과 두 기존 진단 JSON 및 원본 점검 Markdown 보고서를 덮어쓰지 않았다.
- `scripts/preprocess_events.py`가 고정 정규화 함수를 재사용해 배타적 제외 사유, 사건별 대표 원문 쌍, UTC 분할, 학습 원시 횟수와 train 표시 이름 뷰를 제공한다. 학습·평가 파일 생성기는 이 함수와 같은 DB 연결을 사용한다. 사용자를 재선정하거나 최소 10곡·저인기 필터를 적용하지 않는다.

| 전체 원본 실행 항목 | 실제 값 |
| --- | --- |
| 원본 행 | 9,350,628 |
| 필수값 누락 / 기간 밖 / 빈 이름 제외 | 0 / 0 / 0 |
| 중복 초과 제외 | 537,575 |
| 보존 사건 합계 | 8,813,053 |
| train / validation / test 사건 | 6,623,584 / 1,104,514 / 1,084,955 |
| 학습 사용자 / 곡 / user-item | 5,000 / 1,525,005 / 2,739,292 |
| 원본 = 제외 + 세 분할 보존 집계 | 일치, 원본 점검 참고값과 차이 0 |
| 학습 이력 없음 | 0명 |

train 품질 진단은 별도 관계에서 집계했다. 이름 이상은 455사건·112명이며, U+FFFD·U+200B/BOM·공백 외 제어문자 신호의 각 사건 수는 18·221·216건이다. 이 값은 전체 기간의 원본 행 수가 아니라 train의 유효 중복 키 사건 기준이다. 동시각 10곡 이상은 94개 시각·42명·2,146사건이며 최대 572곡이다. 하루 1,440건 초과는 6명·39활동일이고 동일 학습 곡 집합은 1쌍이다. 신호로 삭제한 사건·병합한 계정은 각각 0이다. 공개 보고서에는 개인 ID나 개인별 기록을 넣지 않았다.

전체 입력의 정제·분할 집계와 원본 보존을 확인했다. [실행 보고서](reports/cleaning-split-validation.md)

기존 3개와 신규 10개, 총 **13개 테스트 통과**. 독립 Python 기준과 UTC 경계·마이크로초·마지막 시각 제외·제외 우선순위·중복과 정상 반복·동시각 다곡·두 필드 키·원문 쌍/빈도 동률·행 순서 변경·미래 데이터 불변성을 대조했다. 짧은 학습 이력·미래 전용 사용자 보존, train 품질 플래그의 비삭제, 파일 변조·스키마·사용자 집합 오류, 예외 정리와 기존 보고서 보존도 확인했다.

재개 시 저장소 루트에서 사용할 명령:

```bash
.venv/bin/python scripts/preprocess_events.py --config config/paths.local.toml --inputs-only
.venv/bin/python scripts/preprocess_events.py --config config/paths.local.toml --report .cache/preprocessing/cleaning-split-recheck.md
.venv/bin/python -m unittest discover -s tests -v
```

재실행 보고서가 이미 있으면 새 파일명을 쓴다. 정제·분할 구현 커밋에는 코드·테스트·문서·공개 집계 보고서가 포함됐다. 당시 실행은 원본 검증과 정제·분할 집계를 대상으로 했다.

## 11월 8일 · PR #2 검토 후 검증 보강

- 원본 점검 커밋 `c28cdd1`에서 원본 6개 파일의 크기·SHA256을 [고정 입력 기준](config/raw-input-reference.toml)으로 옮겼다. 전처리 CLI는 이 파일을 읽으므로 원본 점검 보고서를 다시 생성해도 승인 기준은 바뀌지 않는다.
- 그룹별 원본 행 수로 중복 초과를 직접 집계한다. 정제 사건 수와 세 분할의 합계, 각 분할의 UTC 구간을 별도로 검사한다. 변경된 입력을 반영한 점검 보고서, 분할 행 누락·미지정·오배정에 대한 회귀 테스트를 추가했다.
- 전체 테스트 15개가 통과했다. 실제 원본에서 다시 실행한 입력 해시·스키마·사용자 집계, 제외·분할·학습 집계, 품질 진단이 최초 [정제·분할 실행 보고서](reports/cleaning-split-validation.md)와 일치했다.

## 11월 8일 · 학습·평가 패키지 생성과 합성 검증

- [Issue #1](https://github.com/ML-Team4/ML-Team4-Preprocessing/issues/1)은 열린 상태이고 작업 체크리스트 네 항목은 모두 미체크였다. [PR #2](https://github.com/ML-Team4/ML-Team4-Preprocessing/pull/2)는 22:58:54 KST에 main으로 머지됐다. 이전 검증 결과는 위 PR #2 보강 기록과 [정제·분할 실행 보고서](reports/cleaning-split-validation.md)에 있다.
- 적용되는 AGENTS.md는 없었다. origin의 fetch·push 주소가 지정 GitHub 저장소와 일치했고 시작 작업 트리는 깨끗했다. `git fetch origin` 뒤 `origin/main`의 머지 커밋 `aa3935af1e4003f0b0abbc111bd7fba1d5e94884`에서 로컬 `work/dataset-build`를 만들었다. 같은 이름의 원격 브랜치와 PR은 없었다.
- `--inputs-only`로 실제 원본 여섯 파일의 크기·SHA256, 정확한 4열 스키마와 5,000명 사용자 집합을 다시 확인했다. 실제 원본 9,350,628행의 메타데이터가 기존 기록과 일치했다.
- [생성 코드](scripts/build_dataset.py)는 정제 모듈의 디스크 기반 DuckDB 연결과 `train_pairs`·`train_display_names`·기간별 사건을 사용한다. 학습 ID와 인기도를 train에서 만들고, 평가 기간의 관측 고유곡만 검사해 후보 수와 정답을 계산한다. 사용자 제외 집계는 선정 표본을 분모로 학습 이력 없음 → 기간 활동 없음 → 정답 없음 → 후보 10개 미만 순서다. 후보 반복은 후보 교집합에서 사건 수와 고유 쌍 수의 차이로 기록한다. 신규곡과 기청취곡의 사용자 수·고유 곡 수는 범주를 넘어서 중복될 수 있다.
- 새 버전은 출력 위치 아래 별도 임시 폴더에서 검증한다. PyArrow로 스키마·저장 순서·행 내용을 생성 관계와 대조하고 CSV를 정답의 고유 사용자와 맞춘다. SciPy로 CSR을 다시 읽어 구조와 합계, Parquet의 전체 좌표와 값을 검사한다. manifest와 복사 문서를 검사하고, 원본 6개 및 소스·설정의 해시가 유지된 경우에 버전 폴더를 확정한다. 같은 버전으로 재실행하면 기존 폴더를 보존한다.

| 합성 예제 항목 | 실제 값 |
| --- | --- |
| 선정 사용자 / 원본 행 | 6명 / 34행 |
| 제외 | 기간 밖 1행, 중복 초과 1행, 필수값 누락·빈 이름 각 0행 |
| 정제 사건 / train / validation / test | 32 / 18 / 9 / 5 |
| 학습 사용자 / 곡 / 고유 쌍 | 5 / 11 / 15 |
| CSR | shape `(5, 11)`, nnz 15, 합계 18, data int64, indices·indptr int32 |
| validation / test | 사용자 1명·정답 1쌍 / 사용자 2명·정답 3쌍 |
| validation 제외 / test 제외 | 우선순위대로 `1 / 2 / 1 / 1`명 / `1 / 2 / 0 / 1`명 |
| validation 신규곡 / 기청취곡 | 3사건·1곡·2쌍·2명 / 2사건·2곡·2쌍·2명 |

합성 입력에서 패키지 11파일의 저장과 재로딩을 확인했다. 생성 당시 HEAD는 위 머지 커밋이고 생성 코드는 미커밋 상태였다. 검증을 마친 합성 패키지는 정리했다.


저장소 루트에서 재현할 수 있는 명령은 아래와 같다. 테스트가 임시 폴더에 입력과 패키지를 생성하고 실행 후 정리한다.

```bash
.venv/bin/python scripts/preprocess_events.py --config config/paths.local.toml --inputs-only
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m pip check
git diff --check
```

기존 15개와 신규 4개로 **19개 통과**했다. 초기 실행에서 DuckDB 1.4.4의 Arrow API
이름과 합성 기대값·macOS 임시 경로 비교를 수정했다. 최종 검사에서 학습 Parquet 3개와 CSR
구조·값은 행 순서 변경 및 미래 데이터 변경에도 동일했다. 행 순서 변경은 평가 파일도 유지했다.
validation 정답 `(0, 1)`은 test에도 남았다. 후보 9개 사용자와 정답 0개 사용자, 신규곡,
기청취곡, 미래 전용 사용자는 각 규칙대로 제외됐다. 빈 학습과 헤더만 있는 CSV도 다시 읽었다.
합계를 유지한 CSR 값 변조는 좌표별 검사에서 거부됐으며, 실패 시 임시 파일이 정리됐다.
같은 버전으로 실행 명령을 재호출했을 때 종료 코드 1로 거부됐고 기존 11개 파일의 SHA256이
모두 유지됐다. 생성된 패키지 README 예제를 해당 폴더에서 별도로 실행해 10개 파일의
해시, CSR 한 행, 곡 인기도와 기간별 정답을 읽었다. 당시 실제 원본 패키지는 생성 전이었다.

## 11월 9일 · 합성 예제 재생성

기능과 검증 범위가 드러나도록 문서와 실행 설명을 정리했다. 당시 재생성한 예제의 데이터·문서 10파일의 해시와 정제·평가 집계는 앞서 생성한 예제와 모두 일치했다.
전체 테스트 19개, 실제 원본 입력 검증, 새 패키지의 README 로딩 예제를 다시 확인했다.

## 11월 9일 · 패키지 게시 전 검증

- `origin/main`의 README 간소화 커밋 `dd2a6790a88c91c3c25d9e571577520baf36bead`까지 `work/dataset-build`를 fast-forward하고, 원격의 축약과 로컬 패키지 사용법을 함께 반영했다. Issue #1은 열려 있고 원본 검증·정제·시간 분할 항목이 체크돼 있었다.
- 패키지 생성 프로세스를 강제로 종료하면 잠금 폴더 때문에 같은 버전 재시도가 실패했다. 실행별 임시 폴더에 파일을 쓰고 검증 후 최종 경로로 원자적으로 이동하도록 수정했다. 강제 종료 후 재시도와 동일 버전 동시 확정을 검사하는 회귀 테스트를 추가했다. SIGKILL 후 숨김 임시 폴더가 남을 수 있으며, 재시도는 새 임시 폴더에서 성공했다.
- 작업 환경과 새 가상환경을 사용한 별도 로컬 복제본에서 각각 **21개 테스트 통과**. 복제본에는 게시 후보 20개 파일을 적용했고, 개인 설정 없이 고정 의존성 설치·`pip check`·합성 예제 생성·동봉 README 실행을 확인했다.
- 실제 입력 여섯 파일은 실행 전후 크기·SHA256이 기준과 일치했다. 9,350,628행, 선정 사용자 5,000명, Parquet과 선정 CSV의 사용자 집합 차이는 양방향 0명이었다. 전체 원본에서 패키지를 만드는 검증은 별도 작업이다.
- 별도 합성 입력 3,752행에서 Python 기준으로 계산한 학습 매핑·대표 이름·청취 횟수·고유 청취자 수와 저장값이 일치했다. validation 28명·307쌍, test 29명·321쌍이며 각 기간 정답 누락·초과는 모두 0쌍이었다.
- 합성 패키지의 개별 산출물 10개가 앞선 합성 예제와 바이트 단위로 일치했다. 동일 버전 재실행을 거부한 뒤에도 기존 파일 11개의 해시가 유지됐다.

저장소 루트에서 `.venv/bin/python -m unittest discover -s tests -v`로 회귀 검사를 다시 실행할 수 있다.

## 11월 9일 · 패키지 생성기 PR 게시

검토와 게시 승인을 받은 20개 파일을 `5ca64a59996f2f758315f8deb59a93d0416622a3`으로 커밋하고 `git push -u origin work/dataset-build`로 게시했다. [PR #3 · 공통 학습·평가 파일과 검증 패키지 생성](https://github.com/ML-Team4/ML-Team4-Preprocessing/pull/3)의 base는 `main`, head는 `work/dataset-build`이며 본문에 `Refs #1`, 구현 범위, 검사 결과와 재현 명령을 기록했다.

## 패키지 생성기 병합 후 검증 준비 기록

1. 기존 구현과 검증 기록은 [Issue #1](https://github.com/ML-Team4/ML-Team4-Preprocessing/issues/1)과 [PR #3](https://github.com/ML-Team4/ML-Team4-Preprocessing/pull/3)에서 확인한다. PR #3은 main에 머지됐고 Issue #1은 닫혔다. 후속 작업은 해당 작업의 새 Issue에서 추적한다.
2. 패키지 생성기와 시간 분할 검증의 병합 이후 전체 원본 검증을 실행했다. 처리 방법과 검증 결과는 [전체 전처리 보고서](reports/preprocessing-report.md)에 있다.

## 11월 9일 · 문서와 코드 설명의 언어 표기 통일

[Issue #5 · 문서와 코드 설명의 언어·기술 용어 표기 통일](https://github.com/ML-Team4/ML-Team4-Preprocessing/issues/5)을 등록했다. 작업 트리는 깨끗했고 origin 주소를 확인한 뒤 `git fetch origin`을 실행했다. 최신 `origin/main`인 `fe84fa02d76d48b1b1473a4ef754bcfb6217457f`에서 로컬 `docs/language-consistency` 브랜치를 만들었다.

- AGENTS.md와 README에 한국어 설명, 영어 식별자·기술 용어, 영어 그래프 제목·축·범례 기준을 기록했다. `sparsity`, `matrix density`, `sparse matrix`, `dense matrix`, `hyperparameter`, `cosine similarity`, `connected component`를 공통 표기로 정했다.
- 코드 주석·설정 안내·실행 메시지·오류 설명과 manifest의 설명 문장을 한국어로 정리했다. 한국어 오류 메시지와 README 출력 설명을 확인하도록 기존 테스트의 기대 문구도 맞췄다.
- Python 파일 8개의 AST를 대조해 식별자·계산·분기·SQL·dict key가 origin/main과 일치함을 확인했다. 보고서의 통계와 기존 실행 시점의 hash 기록을 보존했다.
- 기존 테스트 21개와 `pip check`가 통과했다. 실제 원본 6개 파일의 bytes·SHA256, 9,350,628행의 schema와 선정 사용자 5,000명도 다시 확인했다. 초기 검사에서 이전 영문 오류를 기대하던 테스트 2곳을 찾아 한국어 문구로 수정했다.
- 수정한 패키지 README와 manifest 설명을 합성 입력으로 검증했다. 생성 당시 HEAD는 위 main 커밋이며 실제 생성 코드와 설정 hash를 확인했다.
- 새 패키지의 데이터 8파일과 COPYING이 앞선 보관본과 byte 단위로 일치했고, 기존 보관본 11파일의 hash도 모두 유지됐다. CSR은 shape `(5, 11)`, nnz 15, 횟수 합계 18이다. validation은 사용자 1명·target 1쌍, test는 사용자 2명·target 3쌍이다. 새 README 예제에서 파일 hash 대조와 데이터 로딩이 성공했다.

저장소 루트에서 아래 명령으로 재현한다. 임시 원본과 패키지는 테스트가 종료되면 정리된다.

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m pip check
```

EDA 보고서와 그래프는 아직 없으므로 해당 산출물의 언어 검사는 EDA 구현 시 진행한다. 전체 원본 패키지 검증은 별도로 진행했다.

## 11월 9일 · 언어 표기 통일 PR 게시

게시 승인 후 테스트 21개, `pip check`, AST·문서 내부 링크·언어 표기 검사를 다시 통과했다. 임시 경로에 재생성한 합성 패키지의 데이터·README hash가 보관본과 일치했고 한국어 로딩 예제도 성공했다.

승인된 18개 파일을 `646edee29e8c413e5dc7c300d88cb8890c40c900`으로 커밋하고 `git push -u origin docs/language-consistency`로 게시했다. [PR #6 · 문서·코드 설명과 기술 용어 표기 통일](https://github.com/ML-Team4/ML-Team4-Preprocessing/pull/6)의 base는 `main`, head는 `docs/language-consistency`이며 `Closes #5`로 Issue를 연결했다. Issue #5의 진행 기록에도 PR 링크와 검증 결과를 남겼다. main 머지는 사용자 담당이다.

검증용으로 보관했던 합성 패키지 4개·44파일은 이후 정리했다. README의 재현 안내도 임시 폴더를 자동 정리하는 기존 테스트 명령으로 맞췄다.

## 원본 점검 첫 커밋 준비 당시 Git 기록

원본 점검 준비 당시 브랜치는 work/data-audit였다. 첫 커밋 대상은 점검 코드·문서·Markdown 집계 보고서와 환경 예시였으며 데이터·가상환경·캐시·개인 설정과 두 로컬 진단 JSON은 Git 제외 대상으로 검사했다. JSON 제외 규칙은 저장소 루트의 두 보고서 경로에만 적용한다. 파일 정리 시점에는 스테이징·커밋·푸시·브랜치 생성을 하지 않았고, 이후 사용자 승인으로 위 원본 점검 첫 커밋과 main 게시를 완료했다. 정제·분할 구현은 work/preprocessing에서 진행해 PR #2로 머지했다.

제안 커밋 메시지: `chore: audit raw ListenBrainz data and define preprocessing contract`
