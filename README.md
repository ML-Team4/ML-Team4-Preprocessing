# ML Team4 공통 데이터 전처리

인기 편향 완화를 통한 개인화 음악 추천 프로젝트의 공통 데이터 저장소다. ListenBrainz 8주 원본에서 `listenbrainz-8week-v1`을 생성하고 저장된 학습·평가 파일 전체를 원본과 독립 대조했다. [전체 전처리 보고서](reports/preprocessing-report.md)

## 출처와 이용 조건

데이터 제공: MetaBrainz Foundation / ListenBrainz. [공식 데이터 안내](https://listenbrainz.org/data/), [덤프 문서](https://listenbrainz.readthedocs.io/en/latest/users/listenbrainz-dumps.html).

전달된 source_manifest.json에는 덤프 2723, 스냅샷 UTC 2026-10-15 00:00:02.588811 및 다음 [원본 TAR 경로](https://data.metabrainz.org/pub/musicbrainz/listenbrainz/fullexport/listenbrainz-dump-2723-20261015-000002-full/listenbrainz-spark-dump-2723-20261015-000002-full.tar)가 기록되어 있다. 이 링크는 출처 기록이며 다운로드 명령이 아니다. 현재 사용 가능한지는 재검증하지 않았다.

동봉 COPYING에서 CC0 1.0 Universal 문구를 직접 확인했다. [CC0 안내](https://creativecommons.org/publicdomain/zero/1.0/). 데이터 공유 시 COPYING·source_manifest·전달 파일의 SHA256을 함께 보관하고 제공 기관을 표기한다. 청취 기록의 공개 조건이 음원 파일 권한을 의미하지는 않는다. 이 설명은 데이터의 조건이며 저장소 코드의 라이선스를 별도로 정한 것은 아니다.

거대한 공식 TAR를 다시 내려받지 않는다. 표본 추출 방법·공식 TAR 규모는 기존 자료의 설명이며, 이번 점검은 로컬 전달 파일만 검증했다. 원래 표본은 학습 이력과 고정 해시 순서로 선정된 5,000명이고 미래 활동으로 다시 고르지 않는다. 공개 보고서에서는 원본 user_id 대신 기존 표시 ID를 사용한다.

## 설치와 경로 설정

Python 3.11 이상을 사용한다. macOS/Linux와 Colab에서 실행할 수 있으며 Windows에서는 WSL을 사용한다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp config/paths.example.toml config/paths.local.toml
```

위 명령은 저장소 루트에서 실행한다. `config/paths.local.toml`의 raw_dir을 이미 확보한 verified_dataset 폴더로 수정한다. 개인 경로는 이 로컬 파일에만 쓴다. 모든 상대 경로는 **설정 파일이 있는 폴더 기준**이며 report_dir/cache_dir에도 같은 규칙이 적용된다. raw_dir 아래에 보고서·캐시를 두면 실행을 거부한다.

필수 입력은 아래 여섯 파일이다. 원본 폴더의 재추출 코드는 실행할 필요가 없다.

```text
verified_dataset/
  listens_8weeks_5000users.parquet
  selected_users.csv
  README.txt
  source_manifest.json
  verified_8week_summary.json
  COPYING
```

## 점검 실행

설치와 로컬 경로 설정을 마친 뒤 가상환경을 활성화한 상태로 저장소 루트에서 실행한다.

```bash
# 로컬 raw-data-audit.json 재생성 (raw-data-audit.md도 갱신)
python scripts/audit_raw_data.py --config config/paths.local.toml
# 로컬 model-readiness-diagnostics.json 재생성
python scripts/check_model_readiness.py --config config/paths.local.toml
python -m unittest discover -s tests -v
```

과거 실행 결과·해시 기록을 보존하며 재실행하려면 기존 보고서를 별도 보관하거나 로컬 설정의 `report_dir`을 다른 로컬 폴더로 지정한다. 후자의 경우 같은 명령으로 해당 폴더에 새 결과를 쓰며, 기존 `reports/` 기록은 유지된다.
sample_size는 진단 사례 개수이며 평가 후보 표본 추출과 관계없다. 점검은 약 152만 학습 항목 전체를 유지한다.

## 직접 확인한 주요 결과

| 구분 | 이번 실행 |
| --- | --- |
| 원본 | 9,350,628행 / 5,000명 / 56일 |
| UTC 시각 | 2026-08-17 00:00:00 ~ 2026-10-11 23:59:59 |
| 결측·빈 이름·범위 밖 | 모두 0 |
| 원문 동일 사건 키의 초과 행 | 537,555 |
| 새 정규화 키의 초과 행 | 537,575 |
| 임시 진단의 사건 / 전체 항목 | 8,813,053 / 1,856,311 |
| 임시 진단의 학습 사건 / 항목 / user-item | 6,623,584 / 1,525,005 / 2,739,292 |
| 진단 검증 평가 사용자 / 정답 쌍 | 3,922 / 193,323 |
| 진단 테스트 평가 사용자 / 정답 쌍 | 3,852 / 201,203 |

위 집계는 최종 전처리 전달본 통계가 아니다. 과거 수치와의 차이를 공백 규칙 재현으로 확인했다. 세부 샘플과 제외 규모는 보고서에 있다. 사용자별 정상 반복을 보존하며 저인기·과도한 청취·모호한 비음악 콘텐츠를 근거 없이 제거하지 않는다.

## 입력 검증·정제·UTC 분할

[Issue #1](https://github.com/ML-Team4/ML-Team4-Preprocessing/issues/1)의 입력 검증·정제·분할은 [PR #2](https://github.com/ML-Team4/ML-Team4-Preprocessing/pull/2)에서 main에 반영했다. 원본 점검의 정규화 함수와 고정 시간 경계를 재사용하며 원본 6개 파일의 크기·SHA256, 정확한 Parquet 스키마와 선정 사용자 5,000명을 검증한다.

```bash
.venv/bin/python scripts/preprocess_events.py --config config/paths.local.toml --inputs-only
.venv/bin/python scripts/preprocess_events.py --config config/paths.local.toml --report .cache/preprocessing/cleaning-split-recheck.md
.venv/bin/python -m unittest discover -s tests -v
```

재실행 보고서는 새 파일명을 사용한다. 기존 보고서는 덮어쓰지 않는다. 전체 실행에서 원본 9,350,628행 = 중복 초과 537,575행 + train 6,623,584건 + validation 1,104,514건 + test 1,084,955건을 확인했다. 결측·기간 밖·빈 이름 제외는 각각 0건이다. 품질 진단은 train 기준의 별도 관계에서 집계하며 사건 삭제·계정 병합을 하지 않는다.

[실행 보고서](reports/cleaning-split-validation.md)에 입력 검증과 정제·분할 집계를 기록했다. 생성 코드가 이어받는 함수와 임시 관계는 [재사용 안내](docs/cleaning-split-guide.md)에 있다.

## 학습·평가 패키지 생성

설치 후 저장소 루트에서 아래 명령으로 합성 입력을 검증한다. 테스트가 임시 폴더에 원본과 패키지를 생성하고 검사가 끝나면 정리한다.

```bash
.venv/bin/python -m unittest discover -s tests -v
```

예제의 CSR은 `(5, 11)`에 비제로 15개, 청취 횟수 합계 18이다. validation은 평가 사용자 1명과 정답 1쌍, test는 2명과 3쌍이다. 테스트는 패키지의 README 예제도 실행해 크기·SHA256, CSR 한 행, 곡 인기도와 기간별 정답을 확인한다. 파일 스키마는 [데이터 계약](docs/data-contract.md), 실제 합성 실행 기록은 [PROGRESS.md](PROGRESS.md)에 있다.

실제 원본으로 실행할 때는 정제·분할에 사용한 로컬 설정과 아직 존재하지 않는 데이터 버전 이름을 사용한다.

```bash
.venv/bin/python scripts/build_dataset.py --config config/paths.local.toml \
  --output-root .cache/datasets --version listenbrainz-8week-v1
```

생성기는 디스크 기반 DuckDB에서 공통 정제·분할을 이어받고, Arrow 배치로 필수 컬럼을 가진 ZSTD Parquet을 쓴다. SciPy로 저장한 CSR을 다시 열어 Parquet의 전체 좌표·값과 대조한다. 임시 폴더에서 재로딩 검사를 통과한 뒤 같은 출력 위치에 버전 폴더를 확정한다. manifest에는 데이터 버전과 파일 정보, 정제·평가 집계를 기록한다. [DuckDB Arrow 내보내기](https://duckdb.org/docs/1.4/guides/python/export_arrow), [PyArrow ParquetWriter](https://arrow.apache.org/docs/20.0/python/generated/pyarrow.parquet.ParquetWriter.html), [SciPy 희소 NPZ](https://docs.scipy.org/doc/scipy-1.15.3/reference/generated/scipy.sparse.save_npz.html)

### 전체 원본 기준의 독립 검증

생성기의 재로딩 검사는 파일과 생성 관계를 대조한다. 아래 명령은 원본에서 별도로 만든 학습 좌표·횟수·인기도와 두 평가 기간의 사용자·정답을 저장된 파일 전체와 대조한다. 기존 패키지는 읽기 전용으로 열며 `"matches": true`, 모든 누락·초과 0, CSR 좌표·값 차이 0을 확인한다.

```bash
.venv/bin/python scripts/validate_dataset.py --config config/paths.local.toml \
  --package .cache/datasets/listenbrainz-8week-v1
```

동일 버전의 저장 위치가 이미 있으면 생성 명령이 실행을 거부한다. 새로 생성해 비교할 때는 새 출력 루트를 지정한다. 새로 생성한 파일의 논리적 내용과 집계를 대조한다. 원본이 없는 모델 담당자는 [패키지 README](docs/dataset-package-readme.md)의 예제를 받은 패키지 폴더에서 실행한다.

```bash
.venv/bin/python -c 'from scipy.sparse import load_npz; m = load_npz(".cache/datasets/listenbrainz-8week-v1/train_counts.npz"); print(m.shape, m.nnz)'
```

기대 출력은 `(5000, 1525005) 2739292`이다. 모델 인수인계에는 [공통 ID와 평가 정의](docs/data-preprocessing-handoff.md)를 사용한다.

## 문서 안내

설명 문서와 코드 주석은 한국어로 작성한다. 변수·함수·컬럼명과 기술 용어는 영어로 표기하며, 그래프 제목·축·범례도 영어로 작성한다. 반복해서 사용하는 기술 용어와 원문 보존 기준은 [AGENTS.md](AGENTS.md)에 기록했다.

- [원본 점검 결과 (Markdown)](reports/raw-data-audit.md)
- [전처리 타당성과 모델 적합성 추가 검토 (Markdown)](reports/model-readiness-review.md)
- [입력 검증·정제·분할 실행 보고서](reports/cleaning-split-validation.md)
- [정제 근거와 시간 분할 검증](reports/temporal-split-review.md)
- [전체 학습·평가 패키지 생성과 독립 검증](reports/preprocessing-report.md)
- [정제·분할 실행과 코드 재사용](docs/cleaning-split-guide.md)
- [공통 전처리 기준](docs/preprocessing-plan.md)
- [파일·ID·행렬·평가 데이터 규격](docs/data-contract.md)
- [팀 전달 안내](docs/data-preprocessing-handoff.md)
- [과제·설계 자료 대조](docs/source-review.md)
- [라이브러리·함수·매개변수와 출처](docs/audit-code-guide.md)
- [진행 상황과 다음 시작 지점](PROGRESS.md)

코드·문서와 Markdown 보고서를 Git에 저장한다. 원본·중간·전처리·모델 출력·캐시·개인 경로 설정·가상환경은 .gitignore로 제외한다. 진단 JSON은 `/reports/raw-data-audit.json`, `/reports/model-readiness-diagnostics.json` 두 경로만 정확히 제외하며 다른 JSON이나 향후 `dataset_manifest.json`을 확장자로 일괄 제외하지 않는다. 데이터 공유 공간과 최종 다운로드 경로는 아직 확정하지 않았다.

추가 적합성 검사도 동일한 고정 전처리 규칙을 사용하고 임시 테이블은 삭제한다.
