# 점검 코드와 외부 기능 설명

## 작성과 참고 출처

점검 스크립트와 작은 회귀 검사는 이번 작업을 위해 작성했다. 인터넷의 완성 전처리 코드를 복사하지 않았다. 기존 로컬 `analyze_window_audit.py`의 정규화 SQL 표현을 과거 통계 비교에 한해 재현했다. 원본 폴더의 `verify_dataset.py`는 기존 점검 범위 참고로 읽었으며 실행·복사하지 않았다. 두 파일은 기존 작업물이며 이번 코드의 입력 데이터나 의존 파일이 아니다. 정확한 파일 해시는 [자료 대조 기록](source-review.md)에 남겼다.

| 기능과 공식 출처 | 쓰는 이유 및 주요 매개변수 |
| --- | --- |
| [DuckDB Python API](https://duckdb.org/docs/stable/clients/python/overview) | 설치 의존성은 duckdb==1.4.4 하나. connect(database)는 cache 임시 파일 DB를 열고 close()로 닫음. execute(sql, parameters)는 경로·설정값을 값 매개변수로 전달. fetchall은 작은 집계·제한된 표본에만 사용 |
| [Parquet 읽기](https://duckdb.org/docs/stable/data/parquet/overview) | read_parquet(path).create_view('raw')는 원본을 읽는 SQL 뷰를 만들며 원본을 덮어쓰지 않음. DESCRIBE로 실제 형 확인 |
| [CSV 읽기](https://duckdb.org/docs/stable/data/csv/overview) | read_csv(path, header=true, columns=...)로 user_id/train_unique_tracks/user_index를 모두 BIGINT로 명시. 추론에 의한 식별자 손실 방지 |
| [문자열 함수](https://duckdb.org/docs/stable/sql/functions/text) | nfc_normalize는 정규 NFC, regexp_replace(s,pattern,' ','g')는 모든 매치(g)를 공백 하나로, trim은 양끝 ASCII 공백 제거. lower는 진단 사례 검색에만 사용하고 ID에 미사용 |
| [정규식](https://duckdb.org/docs/stable/sql/functions/regular_expressions) | RE2 문법의 명시적 코드포인트 문자 클래스 사용. 과거 '\s'와 유니코드 공백 처리가 다름. regexp_matches는 비음악 의심 신호 검색일 뿐 분류기가 아님 |
| [설정](https://duckdb.org/docs/stable/configuration/overview) | memory_limit='3GB', threads=2, preserve_insertion_order=false, TimeZone='UTC'. temp_directory는 임시 spill 폴더, max_temp_directory_size='12GiB'는 spill 한도. DB 파일은 별도 디스크를 사용 |
| SQL DISTINCT/GROUP BY/COUNT | 튜플 그대로 고유 키 집계. count(*)-고유 사건 수가 중복 키의 초과 행 수. count(DISTINCT user_id)가 청취자 수 |
| SQL FILTER/CASE | 기간·결측·빈 이름별 조건부 통계. 시작 >=, 종료 <의 반개구간을 사용 |
| SQL SEMI JOIN/ANTI JOIN/EXISTS | 학습 카탈로그 포함 여부와 사용자별 기청취 제외를 검사. 거대한 전체 후보를 전개하지 않음 |
| SQL quantile_cont(value,q) | 활동 일별·사용자별 건수의 연속 보간 분위수. q=0.5 중앙값, 0.95/0.99 진단용 분포. 삭제 임계값으로 사용하지 않음 |
| SQL row_number()/md5() | 기존 CSV 안에서 md5(str(user_id)+':42') 순서와 1기반 표시 ID가 일치하는지 확인. 원래 후보 전체에서의 표본 선정 적합성을 재검증한 것은 아님 |
| [Python argparse](https://docs.python.org/3/library/argparse.html) | --config 필수 TOML 경로. 코드를 수정하지 않고 원본 위치 지정 |
| [tomllib](https://docs.python.org/3/library/tomllib.html) / pathlib | 설정 읽기와 경로 검증. 상대 경로 기준은 설정 파일의 부모. sample_size 1~20, threads 1~4만 허용 |
| [hashlib](https://docs.python.org/3/library/hashlib.html) | file_digest(file,'sha256')는 파일 스트림 해시. 입력 무결성과 실행 전후 불변성·실행 코드 추적 |
| tempfile.TemporaryDirectory | 캐시 아래 임시 DB와 spill을 만들고 종료 시 삭제. 최종 전처리 파일을 보존하지 않음 |
| json / datetime / time / platform | 집계 JSON 및 안전한 Markdown 셀 문자열, UTC 생성 시각, 실행 초·환경 기록 |
| resource.getrusage(RUSAGE_SELF) | 현재 프로세스 최대 RSS. macOS는 bytes, Linux는 KiB여서 JSON에 bytes로 통일. 노트북 전체 메모리가 아님 |
| subprocess.run | Git 브랜치·HEAD·변경 상태 읽기만 실행. 첫 커밋 없는 HEAD는 null. 쉘을 거치지 않는 인수 배열 사용 |
| [unittest](https://docs.python.org/3/library/unittest.html) / unicodedata | 공백·NFC 결과를 독립 Python 기준과 대조. 작은 Parquet fixture로 중복·시간 경계·정답 집합·키 충돌을 검사 |

## 코드 내부 함수

`sha256(path)`는 읽기 전용 해시, `sql_literal(value)`는 SQL 문자열 리터럴의 작은따옴표만 이스케이프한다. 식별자에는 사용하지 않는다. `rows(con, sql, params)`와 `one(...)`은 작은 SQL 결과를 딕셔너리로 바꾼다. `install_normalizer(con)`는 29개 공백 코드포인트를 고정한 norm_name 매크로를 등록한다.

`audit(con, raw_dir, sample_size, log)`는 스키마·전체 통계·임시 정규화·과거 규칙 대조·기간별 제외 집계와 제한 표본을 반환한다. sample_size는 각 범주의 최대 사례 수다. 개인 절대 경로나 원본 user_id를 반환하지 않는다. 데이터에 대한 대규모 fetchall은 없으며 집계는 DuckDB에서 수행한다.

`render_report(result)`는 JSON을 사람이 읽을 표로 바꾼다. 문자열은 JSON 이스케이프하고 HTML/파이프 문자를 보호하여 곡명 속 개행·마크업을 지시나 표 구조로 해석하지 않게 한다. `git_trace(repo)`는 HEAD·branch·dirty 상태와 실행 스크립트 해시를 기록한다. `main()`은 경로·설정·입력 해시를 확인하고 성공한 결과만 두 보고서에 쓴다.

## 확인한 회귀 검사

정규화는 결합형 문자, 한글 자모, 모든 공백 코드포인트, 대소문자·버전 표기·U+200B 보존을 확인했다. 작은 사건 데이터에서는 원문/정규화 중복의 차이, 다른 시각 반복 유지, 구분자를 가진 두 튜플의 비충돌, 반개구간 경계, 학습 기청취·신규 곡 제외, 반복 정답 집합화와 입력 해시 보존을 확인했다. 테스트는 최종 패키지 파일의 무결성 검사를 대신하지 않는다.

## 추가 모델 적합성 진단

`scripts/check_model_readiness.py`는 기존 점검 함수를 재사용하며 추가 의존성이 없다. `--config` 형식과 경로 규칙은 동일하다. `diagnose(con, baseline, sample_size)`는 학습 지지도·사용자 집중도·곡 벡터의 구분 가능성·시간 이상 신호·평가 정답 난도·배열 메모리를 집계한다. 반환값에는 원본 user_id나 개인 경로가 없다.

임시 `di/dp/du`는 진단용 곡/청취쌍/사용자 테이블이다. 진단용 번호는 최종 공통 ID가 아니며 파일로 전달하지 않는다. `build_overlap(con)`는 학습 항목을 공유하는 사용자쌍과 공통 항목 수를 SQL self join으로 계산한다. 사용자쌍은 최대 5,000² 규모이며 152만² 항목 행렬을 계산하지 않는다.

`components(con)`는 항목마다 최소 사용자와 연결하는 별 모양 간선을 배치 10,000개씩 읽어 union-find로 연결요소를 계산한다. 전체 간선 목록을 Python 메모리에 올리지 않는다. 같은 연결요소라는 조건과 실제 사용자 이력 곡–정답 곡의 양의 코사인 조건을 구분한다.

`materialize_supported_targets(con, target_table, output_table)`는 정답 곡의 학습 청취자 중 대상 사용자와 학습 항목을 공유한 사람이 있는지 확인한다. positive log-count/binary 벡터의 Top-K 절삭 전 양의 유사도 존재 조건이다. 높은 추천 순위나 측정 Recall을 의미하지 않는다. `supported_targets(...)`는 같은 조건을 작은 fixture에서 확인하는 테스트용 함수다. 테이블 이름은 허용된 코드 상수만 사용한다.

`ideal_recall10_ceiling`은 사용자마다 `min(10, 정답수)/정답수`를 평균한 수학적 상한이다. `uniform_random_expected_recall10`은 각 사용자의 후보에서 10곡을 균등 추출할 때의 `10/후보수` 평균이다. 추천을 실제 생성하거나 모델 성능을 측정하지 않는다. 메모리 추정은 배열 원소 수×자료형 바이트 수이며 프레임워크·임시 작업공간·학습 시간은 포함하지 않는다.

동시각 10곡/100곡, 하루 720건/1,440건 구간은 분포 설명용이다. 정제 임계값으로 선택하거나 자동 적용하지 않는다. 동일 곡 집합의 사용자도 공유 재생목록·동기화 계정 등 여러 원인이 가능하므로 관측만으로 삭제하지 않는다. 시간·프로필 표본은 범주당 sample_size개로 제한한다.

추가 테스트는 사용자들이 같은 연결요소에 있어도 특정 정답과 직접적인 양의 Item-based 유사도 경로가 없을 수 있다는 반례와 고립 사용자를 검사한다. 분석의 수식·모델 API 근거는 추가 검토 보고서의 공식 원문 링크에 정리했다. 인터넷 모델 코드를 복사하거나 모델 라이브러리를 설치하지 않았다.

## 공통 정제·분할 구현

`scripts/preprocess_events.py`는 기존 `install_normalizer`, 시간 경계, 읽기 전용 SHA256 함수를 재사용한다. `arg_min`과 UTF-8 바이트 쌍으로 사건 대표 원문을 함께 선택하며, 정제된 train 원문 쌍 빈도와 바이트 동률 순서로 표시 이름 뷰를 제공한다. 입력 검사·SQL 관계·자원 설정·품질 플래그와 공식 함수 출처는 [정제·분할 재사용 안내](cleaning-split-guide.md)에 정리했다. 추가 패키지 설치나 모델 학습은 없다.
