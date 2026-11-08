# 2-2 입력 검증·공통 정제·UTC 분할

[Issue #1](https://github.com/ML-Team4/ML-Team4-Preprocessing/issues/1)의 첫 구현 단계다. `scripts/preprocess_events.py`는 다음 학습 파일 생성 단계에서도 같은 정제 결과를 사용할 수 있도록 함수와 SQL 관계를 제공한다. 실제 전체 실행 집계는 [실행 보고서](../reports/cleaning-split-validation.md)에 있다. 최종 매핑·CSR·평가 파일·manifest·패키지는 아직 만들지 않았다.

## 실행 명령과 원본 보호

저장소 루트에서 기존 가상환경·의존성을 사용한다. 원본 위치는 Git에서 제외되는 `config/paths.local.toml`에만 둔다. 상대 경로는 설정 파일의 부모 기준이다. 원본 재추출·사용자 재표본 추출은 하지 않는다.

```bash
# 여섯 입력의 bytes·SHA256, 스키마, 선정 사용자 집합만 확인
.venv/bin/python scripts/preprocess_events.py --config config/paths.local.toml --inputs-only

# 전체 정제·분할·학습 기간 품질 집계. 재실행 보고서는 Git 제외 공간에 작성
.venv/bin/python scripts/preprocess_events.py --config config/paths.local.toml --report .cache/preprocessing/cleaning-split-recheck.md

.venv/bin/python -m unittest discover -s tests -v
```

보고서는 기존 경로에 덮어쓰지 않는다. 재실행 보고서가 이미 있으면 새로운 파일명을 지정한다. 최초 전체 실행은 `--report reports/cleaning-split-validation.md`로 기록했다.

원본 검증은 [고정 입력 기준](../config/raw-input-reference.toml)을 읽는다. 1단계 커밋 `c28cdd1`의 원본 점검 표에서 6개 파일의 이름·크기·SHA256을 옮겼다. 점검 보고서나 로컬 진단 JSON을 다시 생성해도 전처리 기준은 갱신되지 않는다. 원본 버전을 바꾸기로 결정한 경우에만 기준 파일을 함께 검토하고 수정한다.

6개 필수 파일의 크기와 SHA256이 1단계와 다르면 중단한다. Parquet은 `user_id BIGINT`, `listened_at TIMESTAMP`, `artist_name VARCHAR`, `recording_name VARCHAR`의 정확한 순서와 형을 요구한다. CSV의 헤더·5,000명·ID와 표시 번호의 일대일 대응·1~5,000 연속성·원본 사용자 집합을 검증한다. 기존 CSV의 학습 고유곡 수를 새 정제 결과에 강제하거나 최소 10곡 조건을 재적용하지 않는다.

원본은 읽기 전용이다. 실행 후에도 여섯 해시와 크기를 다시 확인한 뒤에만 보고서를 쓴다. DB와 spill은 설정의 `cache_dir` 아래 임시 폴더에 두며 정상·일반 예외 종료 시 정리한다. 기본 memory_limit은 3GB, threads는 2이고 spill 한도는 12GiB다. DB 파일 공간은 별도이며 memory_limit이 프로세스 RSS 전체의 상한은 아니다. 전체 사건·후보·사용자×곡·곡×곡 배열을 Python으로 가져오지 않는다.

## 재사용하는 함수와 관계

| 함수 | 입력과 결과 |
| --- | --- |
| `read_settings(config_path)` / `working_connection(settings)` | 로컬 경로·자원 설정, 디스크 기반 DuckDB 연결 수명 관리 |
| `read_reference()` / `verify_input_files(raw_dir, reference)` | 고정 TOML 기준으로 필수 6개 입력 검증. 이름·크기·해시만 반환 |
| `validate_input_tables(con, raw_dir)` | 정확한 스키마와 5,000명 사용자 집합 검증, `raw_input`·`selected_cohort` 등록 |
| `build_clean_events(con)` | 정규화·제외 사유·결정적 중복 처리·분할·학습 원시 횟수 집계, 공개 가능한 규모만 반환 |
| `quality_diagnostics(con)` | train 기준 별도 품질 관계와 플래그, 개인 식별자 없는 집계 반환 |
| `fingerprint_inputs(raw_dir)` | 다음 단계의 출력 생성 후에도 원본 불변성 재검증 |

다음 단계 생성 코드는 저장소 루트에서 아래처럼 같은 DB 연결을 사용한다. 보고서에 개인별 관계를 출력하지 않는다.

```python
from pathlib import Path
import sys
sys.path.insert(0, "scripts")
import preprocess_events as common

settings = common.read_settings(Path("config/paths.local.toml"))
before = common.verify_input_files(settings["raw_dir"], common.read_reference())
with common.working_connection(settings) as con:
    common.validate_input_tables(con, settings["raw_dir"])
    summary = common.build_clean_events(con)
    # 이 연결 안에서 아래 관계를 읽어 다음 단계 파일을 생성한다.
    # 기존 audit()를 다시 호출해 별도의 정제 규칙을 만들지 않는다.
assert common.fingerprint_inputs(settings["raw_dir"]) == before
```

| 내부 관계 | 열과 의미 |
| --- | --- |
| `classified_input` | 원본 4열, `artist_key`, `recording_key`, `exclusion_reason`; 제외 우선순위를 확인하는 뷰 |
| `clean_events` | `user_id`, `listened_at`, `artist_key`, `recording_key`, `artist_name`, `recording_name`, `split` |
| `train_events`, `validation_events`, `test_events` | 위 사건을 UTC 반개구간으로 나눈 뷰 |
| `train_pairs` | `user_id`, `artist_key`, `recording_key`, `listen_count BIGINT`; 학습 고유 사용자·곡별 원시 사건 수 |
| `train_display_names` | 두 정규화 키, 원문 이름 쌍, `representative_events`; 정제된 train 사건 빈도로 표시 이름을 선택하는 뷰 |

이 관계의 원본 ID와 원문은 로컬 임시 DB 안에서만 사용한다. 정렬·0기반 공통 ID·최종 스키마를 적용한 전달 파일은 다음 단계에서 만든다. DB 연결이 닫히면 임시 결과도 사라지므로 다음 단계의 파일 생성은 반드시 `with` 안에서 이어간다.

## 정제·대표 선택·분할

1. `audit_raw_data.install_normalizer()`를 그대로 재사용한다. NFC → 고정 공백 29종 연속열 축약 → 양끝 공백 제거 순서이며 버전 표기·대소문자·악센트를 보존한다.
2. 필수값 NULL → 전체 기간 밖 → 정규화 후 빈 이름 → 유효 사건의 중복 초과 순서로 한 번씩 센다. 중복 초과 행은 각 그룹의 원본 행 수에서 1을 뺀 값의 합이다. 원본 행 수를 네 제외 수와 정제 사건 수의 합과 대조한다.
3. 키는 `(user_id, listened_at, artist_key, recording_key)`다. 동일 키에서 `arg_min`으로 원문 아티스트·곡명 **쌍**을 선택하며 정렬 키도 `encode(artist_name)`, `encode(recording_name)`의 UTF-8 바이트 쌍이다. 다른 시각 반복과 같은 시각 다른 곡은 보존한다.
4. `train_display_names`는 사건별 대표 선택 후 train 원문 쌍 빈도가 가장 큰 쌍을 고른다. 동률에는 같은 바이트 정렬을 적용한다. 원본 중복 행을 표시 빈도로 세거나 두 이름을 독립 선택하지 않는다. 이 뷰는 다음 단계에서 조회할 때 계산된다.
5. 시간대 없는 원본 TIMESTAMP를 부속 자료대로 UTC로 해석하며 정밀도를 유지한다. train `[2026-08-17,2026-09-28)`, validation `[2026-09-28,2026-10-05)`, test `[2026-10-05,2026-10-12)`다. 세 분할 합계를 정제 사건 수와 대조하고 각 분할의 모든 시각이 해당 구간에 있는지 검사한다.

## 품질 플래그와 검사 범위

`quality_train_names`는 train 유효 원본의 이름 이상을 사건 단위로 모으며 대표 원문에서 탈락한 변형도 고려한다. U+FFFD, U+200B/BOM, 공백 외 제어문자를 별도 플래그로 남긴다. `quality_train_timestamps`는 사용자·시각별 곡 수, `quality_train_days`는 사용자·일별 사건 수를 센다. 10/100곡, 720/1,440건 구간은 1단계와 같은 기술통계 구간이다.

`quality_profile_pairs`는 train 고유곡을 공유한 사용자쌍의 공통 곡 수와 Jaccard를 구한다. 10곡 이상 공유한 검토 대상과 완전히 동일한 곡 집합을 가진 쌍을 유지한다. 10곡 미만의 동일 집합도 빠뜨리지 않는다. `quality_train_user_flags`는 이 신호를 사용자별로 모은 별도 뷰다. 원본 ID를 포함하는 이 관계들은 Git에 저장하지 않으며 공개 보고서는 집계만 사용한다. 플래그로 사건을 삭제하거나 사용자를 병합하지 않는다.

총 15개 테스트에는 독립 Python 기준과의 사건·제외 수·학습 횟수·표시 이름 대조가 포함된다. UTC 경계의 마이크로초·마지막 종료 제외·다중 제외 조건의 우선순위·정상 반복·동시각 다곡·두 필드 키 충돌·버전 구분·원문 쌍과 빈도 동률·원본 중복 빈도 오인·행 순서·미래 데이터 변경·짧은 이력·미래 전용 사용자를 검사한다. 입력 변조·스키마·사용자 집합 오류·예외 시 임시 DB 정리·기존 보고서 덮어쓰기 거부도 확인한다. PR 검토 후에는 변경된 입력의 해시로 보고서를 다시 써도 입력을 거부하고, 분할 행의 누락·미지정·잘못된 배정을 감지하는 회귀 검사를 추가했다. 최종 CSR·평가 파일 검증은 아직 수행하지 않았다.

주요 SQL 기능은 DuckDB 1.4 공식 문서의 [arg_min·bool_or](https://duckdb.org/docs/1.4/sql/functions/aggregates), [encode](https://duckdb.org/docs/1.4/sql/functions/text), [QUALIFY](https://duckdb.org/docs/1.4/sql/query_syntax/qualify)를 참고했다. 집계와 윈도 함수를 CTE로 나눠 DuckDB 1.4.4에서 검사했다. 새 외부 패키지나 외부 전처리 코드를 추가하지 않았다.
