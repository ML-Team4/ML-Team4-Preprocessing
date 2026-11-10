# ML Team4 공통 데이터 전처리

## 모델 담당자용 데이터 사용법

1. [GitHub 저장소](https://github.com/ML-Team4/ML-Team4-Preprocessing)의 **Code → Download ZIP**으로 내려받고 저장소 ZIP을 푼다.
2. 저장소 루트에서 동봉된 전처리 ZIP을 `datasets/`에 푼다.
3. Item-Based CF와 Implicit MF의 공통 입력 폴더를 `datasets/listenbrainz-preprocessing/`로 지정한다.

```bash
unzip datasets/listenbrainz-preprocessing.zip -d datasets
```

압축을 풀면 `datasets/listenbrainz-preprocessing/train_interactions.csv`가 바로 보여야 한다. 파일 탐색기로 풀 때도 `datasets/`를 대상 폴더로 선택한다. ZIP 안에 패키지 폴더가 포함되어 있다.

| 파일 | 용도 |
| --- | --- |
| train_interactions.csv | `user_idx`, `item_idx`, `listen_count`로 학습 행렬 생성 |
| users.csv, items.csv | 공통 사용자·곡 인덱스 사용, 곡명·아티스트명·학습 구간 고유 청취자 수 조회 |
| validation_users.csv, validation_targets.csv | 공통 검증 사용자와 정답으로 모델 설정 선택 |
| test_users.csv, test_targets.csv | 설정 확정 후 공통 테스트 사용자와 정답으로 최종 평가 |

두 모델 담당자는 압축을 푼 폴더에서 작업을 시작하며, 곡 인덱스와 시간 분할은 제공된 값을 그대로 사용한다. 청취 횟수의 `log1p` 변환 등 모델별 가중치는 각 담당자가 결정한다. 사용자 행 × 곡 열의 학습 행렬은 `(5000, 1525005)`, 고유 학습 좌표는 2,739,292개, 청취 횟수 합계는 6,623,584건이다.

[팀원용 상세 인수인계](docs/data-preprocessing-handoff.md)에서 CSV 헤더·행렬 구성·추천 곡명 조회·평가 정답 연결 예제를 확인한다. CSV를 읽어 보기만 할 때는 `pyarrow`, `scipy`가 필요하다. Python 3.11 이상으로 저장소에서 아래 검사를 실행하면 CSV 7개 로딩, 인덱스·고유 청취자 수·기간별 정답 연결과 실제 곡명을 확인한다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/use_dataset.py --data-dir datasets/listenbrainz-preprocessing
```

## 제공하는 데이터와 처리 결과

ListenBrainz 덤프 2723에서 확보한 8주·5,000명 청취 표본을 제공한다. 주별 원본 CSV는 **9,350,628행의 중복·대소문자·공백·특수문자·시각 정밀도를 보존**한다. 주별 파일은 전달 단위이며 모델 분할 단위가 아니다.

전처리는 NFC와 고정 공백 정리 후 같은 사용자·시각·곡 키의 중복 초과 537,575행을 제외한다. 결측·기간 밖·빈 이름 제외는 각 0행이다. 서로 다른 시각의 반복 청취, 저인기 곡과 고활동 사용자는 유지한다. train만으로 공통 인덱스·대표 곡명·고유 청취자 수를 만들고 같은 train을 두 평가 기간에 사용한다.

| 구간 | UTC 시작 포함 | UTC 종료 미포함 | 정제 사건 | 평가 사용자 / 정답 쌍 |
| --- | --- | --- | ---: | ---: |
| train | 2026-08-17 00:00:00 | 2026-09-28 00:00:00 | 6,623,584 | — |
| validation | 2026-09-28 00:00:00 | 2026-10-05 00:00:00 | 1,104,514 | 3,922 / 193,323 |
| test | 2026-10-05 00:00:00 | 2026-10-12 00:00:00 | 1,084,955 | 3,852 / 201,203 |

```text
datasets/
  raw/
    listens_2026-08-17.csv.gz ... listens_2026-10-05.csv.gz  # 연속 8주
    selected_users.csv
    README.txt                 # 제공받은 출처 설명 원문
    source_manifest.json       # 덤프·표본 추출 출처 원문
    verified_8week_summary.json # 변환 전 Parquet 검증 원문
    COPYING
    distribution_manifest.json # 주별 CSV 변환·중복 포함 대조 결과
  listenbrainz-preprocessing.zip
  listenbrainz-preprocessing/   # 사용자가 ZIP 해제 시 생성, Git 게시 제외
```

## 원본에서 전처리 재현

모델 담당자의 학습은 위 전처리 ZIP에서 시작한다. 전처리 규칙을 점검하거나 재현하려는 경우 저장소 루트에서 설치 후 아래를 실행한다. 기본 설정은 동봉된 `datasets/raw/`를 압축 상태로 직접 읽는다. 별도 원본 전달이나 전체 덤프 다운로드, 개인 경로 설정이 필요하지 않다.

```bash
# 원본 주별 CSV·출처 파일의 고정 무결성, 스키마와 선정 5,000명 확인
python scripts/preprocess_events.py --inputs-only
# 원본 진단 결과를 .cache/audit-reports/에 저장
python scripts/audit_raw_data.py
# 실제 정제·시간 분할·CSV 저장·재로딩 검사
python scripts/build_dataset.py --output-root .cache/reproduced
# 새 ZIP을 별도 출력 경로에 생성
python scripts/package_dataset.py
# 원본에서 별도로 계산한 기대값과 재생성 패키지의 전체 행 대조
python scripts/validate_dataset.py --package .cache/reproduced/listenbrainz-preprocessing
# 동봉 ZIP의 CSV 7개와 재생성 CSV의 바이트 단위 비교
python scripts/verify_distribution.py --reproduced .cache/reproduced/listenbrainz-preprocessing
python -m unittest discover -s tests -v
```

`.cache/reproduced/listenbrainz-preprocessing/`가 이미 있으면 생성기가 중단한다. 반복 실행은 `--output-root .cache/reproduced-another`처럼 새 출력 폴더를 지정하고 검증 명령에도 그 경로를 사용한다. 기존 배포본과 재생성 결과를 덮어쓰지 않는다. 정제·분할과 품질 플래그만 확인하려면 `python scripts/preprocess_events.py --report .cache/cleaning-check.md`, 독립 시간 분할은 `python scripts/review_temporal_split.py`로 검사한다.

설정 파일의 상대 경로는 설정 파일이 있는 폴더 기준이다. 기본값은 [paths.example.toml](config/paths.example.toml)이며 원본 CSV의 기준은 [raw-csv-reference.toml](config/raw-csv-reference.toml)이다. 기존 Parquet을 가진 사용자는 예제를 `config/paths.local.toml`로 복사하고 `raw_dir`만 기존 원본 폴더로 바꾼 뒤 `--config config/paths.local.toml`을 지정할 수 있다. 이 경우 [기존 Parquet 기준](config/raw-input-reference.toml)을 적용한다. 형식별로 같은 정제·분할 함수를 사용한다.

[배포 검증 보고서](reports/distribution-validation.md)에 원본과 주별 CSV의 중복 포함 전체 대조, 기존 CSV와의 비교, 다운로드본 검증 결과를 기록했다.

## 청취 분포와 평가 범위 분석

[EDA 보고서](reports/listening-eda.md)는 학습 인기도·사용자 이력·평가 제외 규모를 설명한다. 고유 train 청취자가 한 명인 곡은 전체 학습곡의 78.97%, 학습 청취 사건의 33.00%다. [집계표 11개](reports/eda/tables/)와 [그래프 2개](reports/eda/figures/), 내부 manifest·독립 검증 기록을 제공한다. 이 구간은 기술통계이며 모델의 최종 저인기 평가 기준은 별도 선택한다.

```bash
python scripts/analyze_listening.py --output-dir .cache/eda-recheck
python scripts/verify_listening_analysis.py --output-dir .cache/eda-recheck --reproduce
# 게시된 집계표·그림도 독립 재현 대조
python scripts/verify_listening_analysis.py --output-dir reports/eda --reproduce
```

EDA는 원본 재전처리 없이 공통 패키지와 저장소의 고정 기준을 사용한다. `--package`로 다른 위치를 지정할 수 있다. 예상 밖 산출물이 있으면 중단하고 기존 파일을 보존한다. 한국어 해석 보고서는 자동으로 덮어쓰지 않는다.

## 출처와 이용 조건

데이터 제공자는 MetaBrainz Foundation / ListenBrainz다. [공식 데이터 안내](https://listenbrainz.org/data/)와 [덤프 문서](https://listenbrainz.readthedocs.io/en/latest/users/listenbrainz-dumps.html)를 참고한다. 동봉 [source_manifest.json](datasets/raw/source_manifest.json)의 덤프 2723·스냅샷 `2026-10-15 00:00:02.588811 UTC`와 [README 원문](datasets/raw/README.txt)을 보존한다. 이 자료의 공식 TAR URL은 출처 기록이며 재현에 다운로드가 필요하지 않다. 전체 모집단 추출 과정은 동봉 출처의 설명과 로컬 표본의 검증 범위를 구분한다.

데이터 이용 조건은 [COPYING](datasets/raw/COPYING)의 **CC0 1.0 Universal**이다. 제공 기관과 출처 자료를 함께 보관한다. 청취 기록의 이용 조건은 음원 파일의 권한이나 저장소 코드의 별도 라이선스를 뜻하지 않는다. `verified_8week_summary.json`의 파일 크기·무결성 값은 변환 전 Parquet에 관한 기록이며, 주별 CSV는 별도 고정 기준과 `distribution_manifest.json`으로 검사한다.

## 처리 기준과 검증 문서

- [상세 인수인계와 실행 예제](docs/data-preprocessing-handoff.md), [데이터 계약](docs/data-contract.md)
- [고정 정제 기준](docs/preprocessing-rules.md), [정제·분할 코드 재사용](docs/cleaning-split-guide.md)
- [원본 점검](reports/raw-data-audit.md), [정제·분할 집계](reports/cleaning-split-validation.md), [시간 분할과 처리 근거](reports/temporal-split-review.md)
- [CSV 검증](reports/csv-dataset-validation.md), [배포 검증](reports/distribution-validation.md), [모델 적합성과 한계](reports/model-readiness-review.md)
- [함수·매개변수·출처](docs/audit-code-guide.md)

Git에는 원본 배포 대상과 전처리 ZIP, 코드·문서·EDA 산출물을 포함한다. 압축 해제 사본·기존 Parquet·캐시·개인 설정·모델 출력은 제외한다. 설명은 한국어, 식별자·기술 용어와 그래프 표기는 영어를 사용한다.
