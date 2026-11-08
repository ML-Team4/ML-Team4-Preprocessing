# ML Team4 공통 데이터 전처리

인기 편향 완화를 통한 개인화 음악 추천 프로젝트의 공통 데이터 저장소다. **현재는 1단계 원본 점검과 전처리 기준 정리만 완료했다.** 전체 전처리 파일 생성은 2단계다. Item-based·Implicit MF 구현, 보정 가중치, 성능 평가 코드와 UI는 이 저장소의 이번 작업 범위에 포함하지 않는다.

## 출처와 이용 조건

데이터 제공: MetaBrainz Foundation / ListenBrainz. [공식 데이터 안내](https://listenbrainz.org/data/), [덤프 문서](https://listenbrainz.readthedocs.io/en/latest/users/listenbrainz-dumps.html).

전달된 source_manifest.json에는 덤프 2723, 스냅샷 UTC 2026-10-15 00:00:02.588811 및 다음 [원본 TAR 경로](https://data.metabrainz.org/pub/musicbrainz/listenbrainz/fullexport/listenbrainz-dump-2723-20261015-000002-full/listenbrainz-spark-dump-2723-20261015-000002-full.tar)가 기록되어 있다. 이 링크는 출처 기록이며 다운로드 명령이 아니다. 현재 사용 가능한지는 재검증하지 않았다.

동봉 COPYING에서 CC0 1.0 Universal 문구를 직접 확인했다. [CC0 안내](https://creativecommons.org/publicdomain/zero/1.0/). 데이터 공유 시 COPYING·source_manifest·전달 파일의 SHA256을 함께 보관하고 제공 기관을 표기한다. 청취 기록의 공개 조건이 음원 파일 권한을 의미하지는 않는다. 이 설명은 데이터의 조건이며 저장소 코드의 라이선스를 별도로 정한 것은 아니다.

거대한 공식 TAR를 다시 내려받지 않는다. 표본 추출 방법·공식 TAR 규모는 기존 자료의 설명이며, 이번 점검은 로컬 전달 파일만 검증했다. 원래 표본은 학습 이력과 고정 해시 순서로 선정된 5,000명이고 미래 활동으로 다시 고르지 않는다. 공개 보고서에서는 원본 user_id 대신 기존 표시 ID를 사용한다.

## 설치와 경로 설정

Python 3.11 이상(macOS/Linux, Colab 포함)을 사용한다. 이번 실행은 Python 3.13.7 / DuckDB 1.4.4 / macOS arm64에서 확인했다. `resource` 모듈로 RSS를 측정하므로 Windows 직접 실행은 지원하지 않으며 WSL을 사용한다.

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

원본 Parquet SHA256:

```text
4beecfca926a472081b70bcc50c2a20193756a90f9e8dc84336076e86b72e3c3
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

두 점검 모두 원본을 읽기만 한다. 예시 설정의 `report_dir = "../reports"` 기준으로 `audit_raw_data.py`는 성공 시 `reports/raw-data-audit.json`과 `reports/raw-data-audit.md`를 갱신한다. `check_model_readiness.py`는 `reports/model-readiness-diagnostics.json`만 갱신하며 `reports/model-readiness-review.md`를 자동 생성하지 않는다. 실패하면 종료 오류를 확인하고 이전 성공 보고서를 새 실행 결과로 해석하지 않는다. 임시 정규화·중복·집계 테이블은 cache_dir 아래 임시 DuckDB에서만 만들고 정상 종료/일반 예외 시 삭제한다. 강제 종료로 남은 캐시도 Git 제외 대상이다. 최종 Parquet·CSR·평가 정답을 만들지 않는다.

두 JSON은 로컬 생성물로 Git에서 제외되므로 새 clone에는 없다. Git에서는 [원본 점검 보고서](reports/raw-data-audit.md)와 [모델 적합성 검토](reports/model-readiness-review.md)를 읽는다. JSON이 없어도 이 보고서들과 [전처리 계획](docs/preprocessing-plan.md)·[데이터 계약](docs/data-contract.md)으로 2단계 준비를 시작할 수 있다. 상세 기계 판독 통계가 필요할 때 원본 여섯 파일을 확보하고 위 명령으로 재생성한다.

과거 실행 결과·해시 기록을 보존하며 재실행하려면 기존 보고서를 별도 보관하거나 로컬 설정의 `report_dir`을 다른 로컬 폴더로 지정한다. 후자의 경우 같은 명령으로 해당 폴더에 새 결과를 쓰며, 기존 `reports/` 기록은 유지된다. 실행 시간·환경·코드 추적은 새 실행 기준으로 기록되므로 과거 실행 기록을 소급해 바꾸지 않는다.

memory_limit=3GB, threads=2, sample_size=5가 기본이다. 임시 디스크 여유도 필요하며 DuckDB spill 제한은 12GiB다(임시 DB 크기는 별도). 메모리 설정은 프로세스 전체의 엄격한 상한이 아니다. 대량 행을 Python 리스트나 pandas로 가져오지 않는다. sample_size는 진단 사례 개수이며 평가 후보 표본 추출과 관계없다. 점검은 약 152만 학습 항목 전체를 유지한다.

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

위 집계는 최종 전처리 전달본 통계가 아니다. 과거 수치와의 차이를 공백 규칙 재현으로 확인했다. 세부 샘플·제외 규모·해시·실행 시간과 RSS는 보고서에 있다. 사용자별 정상 반복을 보존하며 저인기·과도한 청취·모호한 비음악 콘텐츠를 근거 없이 제거하지 않는다.

## 문서 안내

- [원본 점검 결과 (Markdown)](reports/raw-data-audit.md)
- [전처리 타당성과 모델 적합성 추가 검토 (Markdown)](reports/model-readiness-review.md)
- [공통 전처리 기준](docs/preprocessing-plan.md)
- [파일·ID·행렬·평가 데이터 규격](docs/data-contract.md)
- [팀 전달 설명 초안](docs/data-preprocessing-handoff.md)
- [과제·설계 자료 대조](docs/source-review.md)
- [라이브러리·함수·매개변수와 출처](docs/audit-code-guide.md)
- [진행 상황과 다음 시작 지점](PROGRESS.md)

코드·문서와 Markdown 보고서를 Git에 저장한다. 원본·중간·전처리·모델 출력·캐시·개인 경로 설정·가상환경은 .gitignore로 제외한다. 진단 JSON은 `/reports/raw-data-audit.json`, `/reports/model-readiness-diagnostics.json` 두 경로만 정확히 제외하며 다른 JSON이나 향후 `dataset_manifest.json`을 확장자로 일괄 제외하지 않는다. 데이터 공유 공간과 최종 다운로드 경로는 아직 확정하지 않았다.

추가 적합성 검사도 동일한 고정 전처리 규칙을 사용하고 임시 테이블은 삭제한다. 모델 학습이나 추천 성능을 측정하지 않으며, 학습 연결 구조·정답의 신호 유무·지표의 수학적 상한·배열 메모리를 계산한다. 로컬 결과 JSON은 자동 생성되고 Git의 검토 Markdown은 그 결과와 공식 문헌을 해석한 문서다.
