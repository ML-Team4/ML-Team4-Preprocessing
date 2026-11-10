# 공통 데이터 전달 규격

CSV 전달 계약은 2.0.0이며 현재 데이터 버전은 `listenbrainz-preprocessing`다. 학습 사용자 5,000명, 곡 1,525,005개, 고유 학습 쌍 2,739,292개다. 모든 모델 담당자는 같은 데이터 버전과 공통 ID를 사용한다. [CSV 생성과 검증 결과](../reports/csv-dataset-validation.md)

## ID와 곡 식별

곡은 고정 정규화한 아티스트·곡명 **두 문자열의 튜플**로 식별한다. 정확한 고유 음원의 식별자는 아니며 서로 다른 음원이 같은 이름을 가질 수 있다. 단순 연결 문자열을 join key로 쓰지 않는다.

user_idx는 정제된 train에 이력이 있는 원본 user_id를 숫자 오름차순으로 정렬해 0부터 부여한다. item_idx는 train에서 관측한 (artist_key, recording_key)를 UTF-8 바이트 사전순으로 정렬해 0부터 부여한다. 로케일·대소문자 무시 정렬을 사용하지 않는다. 두 ID는 각각 끊김 없는 int32다. 개수가 int32 범위를 넘으면 중단하고 규격 버전을 올린다. 입력 순서·병렬 실행 순서에 의존하지 않는다.

기존 selected_users.csv의 user_index는 1부터 시작하는 점검 표시 ID이며 새 user_idx로 재사용하지 않는다. future-only 사용자나 신규 곡에 학습 ID를 새로 부여하지 않는다. 모델이 내부적으로 ID를 재배열하면 역매핑을 보존하고 공유 출력에서 반드시 공통 user_idx/item_idx로 복원한다. 다른 데이터 버전의 ID는 호환된다고 가정하지 않는다.

## 파일별 스키마

표형 데이터는 CSV 7개로 전달한다. 첫 행에 컬럼명을 포함하며 인코딩은 UTF-8 BOM이다. CSV를 읽을 때 아래 자료형을 지정하고 문자열의 기본 결측값 추론을 끈다. 쉼표·따옴표·개행은 CSV 인용 규칙으로 보존한다. 정렬은 읽기 순서의 계약이며 소비자가 필터링하거나 병합하면 다시 정렬해야 한다.

| 파일 | 컬럼 및 자료형 | 행 단위와 정렬 |
| --- | --- | --- |
| train_interactions.csv | user_idx int32, item_idx int32, listen_count int64 | 고유 user-item 쌍, user_idx → item_idx 오름차순 |
| users.csv | user_idx int32, user_id int64 | user_idx 오름차순, 양쪽 모두 고유 |
| items.csv | item_idx int32, artist_name string, recording_name string, train_listener_count int32, artist_key string, recording_key string | item_idx 오름차순, 정규화 키 튜플 고유 |
| validation_targets.csv | user_idx int32, item_idx int32 | 검증 평가 사용자만의 중복 없는 정답 쌍, user_idx → item_idx |
| test_targets.csv | user_idx int32, item_idx int32 | 테스트 평가 사용자만의 중복 없는 정답 쌍, user_idx → item_idx |
| validation_users.csv | user_idx, 10진 정수 | 헤더 포함 UTF-8 BOM, user_idx 오름차순, 중복 없음 |
| test_users.csv | user_idx, 10진 정수 | 헤더 포함 UTF-8 BOM, user_idx 오름차순, 중복 없음 |
| dataset_manifest.json | UTF-8 JSON object | 버전·생성 기준·집계·파일 해시·재현 명령 |

items의 artist_key/recording_key는 ID용 정규화 필드이다. artist_name/recording_name은 train에서 선택한 대표 원문 이름 쌍이다. 대표 선택은 [전처리 기준](preprocessing-rules.md)의 사건별 원문 선택 → 빈도 내림차순 → UTF-8 바이트 동률 정렬을 따른다. 표시 이름에서 ID를 역추정하지 않는다. 추가 원문 표기 파일이나 제외 사용자 기록을 만들면 데이터 공간에 두고 manifest에 스키마를 추가한다.

원문 표시 이름에는 개행·제어문자·마크업 기호가 있을 수 있다. 소비 코드는 출력 형식에 맞게 이스케이프하고, 화면 표시상의 정리를 ID 키 변경으로 연결하지 않는다. 이름 이상 플래그나 동시각 다곡 플래그를 추가하더라도 공통 학습·평가 항목을 임의로 삭제하지 않는다.

listen_count는 중복 처리 후 **train에 남은 서로 다른 청취 시각 사건 수**다. 로그값, binary preference, confidence, IPS, 역인기도 가중치를 저장하지 않는다. 모델 담당자가 이 횟수와 train_listener_count로 필요한 변환을 계산한다. train_listener_count는 해당 item_idx의 고유 학습 사용자 수이며 총 재생 수와 다르다. 모든 항목에 1 ≤ train_listener_count ≤ U가 성립한다.

## CSV에서 학습 행렬 구성

모델 담당자는 train_interactions.csv의 user_idx·item_idx·listen_count를 사용해 shape=(U,I)의 SciPy CSR을 메모리에 구성할 수 있다. U와 I는 users.csv와 items.csv의 행 수다. [팀원용 로딩 예제](data-preprocessing-handoff.md#폴더-한-곳을-지정하는-실행-예제)에 실행 가능한 코드가 있다.

고유 사용자·곡 좌표가 한 번씩 저장되고 횟수가 양수여야 한다. 학습 CSV 행 수는 고유 학습 쌍 수, listen_count 합계는 정제된 train 사건 수와 같다. CSV 전체 좌표·횟수·정렬을 생성 관계 및 원본에서 독립적으로 산출한 기대값에 대조한다. 모델이 만든 CSR에서도 nnz와 횟수 합계를 대조할 수 있다.

## 시간 구간과 평가 정의

UTC `[2026-08-17,2026-09-28)` train, `[2026-09-28,2026-10-05)` validation, `[2026-10-05,2026-10-12)` test다. 학습을 고정하며 검증 후 train+validation 재학습은 하지 않는다.

I를 학습 전체 카탈로그, H_u를 사용자 u의 학습 청취곡 집합으로 둔다. 후보는 `C_u = I \ H_u`다. 정답은 해당 기간의 고유 청취곡과 C_u의 교집합이다. 다른 시각의 반복도 정답에서는 하나의 곡으로 센다. test의 후보에서 validation 청취곡을 추가로 빼지 않는다. validation과 test에 같은 정답 곡이 등장하는 것도 허용한다.

학습 이력이 있고 정답이 ≥1개이며 |C_u|≥10인 사용자를 그 기간의 평가 사용자로 삼는다. validation/test 집합을 각각 정하고 교집합을 강제하지 않는다. 같은 기간 안의 네 실험 조건은 평가 사용자·후보·정답·학습 인기도가 모두 같아야 한다. 전체 카탈로그에서 학습 이력만 제외하며 임의 후보 제한·표본 추출은 허용하지 않는다. 사용자별 전체 후보를 CSV로 전개하지 않는다.

평가 제외 보고에는 다음을 구분한다.

- 학습에 없는 신규 곡: 사건 수, 고유 항목 수, user-item 쌍 수, 영향을 받은 사용자 수.
- 사용자 학습 기청취곡: 사건 수·항목 수·쌍 수·사용자 수. 신규 곡과 서로 다른 범주다.
- 학습 이력 없음, 기간 내 활동 없음, 후보에 해당하는 정답 없음, 후보 10개 미만: 각 사용자 수와 분모를 기록한다. 조건별 집계는 중복 여부를 명시하고 최종 제외 사유 집계에는 우선순위를 둔다.
- 최종 기간별 사용자 수와 정답 쌍 수. 정답 파일의 고유 user_idx 집합과 users.csv가 정확히 같아야 한다.

추천 목록은 사용자별 서로 다른 후보 곡 정확히 10개여야 한다. 알 수 없는 ID, 학습 기청취곡, 중복 곡, 목록 부족은 출력 계약 위반으로 기록한다. 점수 동률·이웃 부족 처리의 구체적인 모델 규칙은 팀 확인 항목이며 후보 자체를 줄이는 근거가 되지 않는다.

## 지표의 공통 정의

E는 해당 기간의 평가 사용자, T_u는 정답 집합, L_u는 추천 순서가 있는 10개 곡이다. 아래 정의는 모델별 평가에서 공통으로 사용한다.

`Recall@10 = (1/|E|) Σ_u |set(L_u) ∩ T_u| / |T_u|`

`ARP = (1/|E|) Σ_u [(1/10) Σ_(i in L_u) train_listener_count(i)]`

사용자별 비율/평균을 먼저 계산하는 macro 평균이다. 전체 정답 쌍 수로 한 번에 나누는 Recall과 구분한다. ARP는 로그 인기도나 총 청취 횟수가 아니다. 평가 사용자가 0명이면 정의되지 않음을 보고하고 0점으로 대체하지 않는다.

## manifest 필수 내용

| 그룹 | 필수 기록 |
| --- | --- |
| 식별 | contract_version, dataset_version, created_at_utc, 처리 규칙 버전 |
| 입력 | 원본 이름·바이트·SHA256, 부속 파일 해시, 출처 URL·덤프 스냅샷·CC0 근거 |
| 처리 | 정규화 코드포인트, 중복 키와 대표 이름 규칙, 제외 사유·처리 전후 통계 |
| 분할 | UTC의 start_inclusive/end_exclusive, train 고정·재학습 없음 |
| 매핑 | ID 시작 0·정렬·자료형, U/I, 사용자 및 곡 매핑 파일 SHA256 |
| 파일 | 상대 파일명, bytes, SHA256, rows, 컬럼·자료형·정렬, CSV 형식·인코딩·헤더 여부 |
| 평가 | 후보·정답 정의, 기간별 사용자·정답 수, 제외 규모·분모·중복 여부 |
| 재현 | 생성 코드·의존성 명세·고정 입력 기준의 SHA256, 재현 명령 |

manifest는 자기 자신의 SHA256을 내부에 넣지 않는다. 데이터 버전 묶음 전체를 검증할 때 manifest의 SHA256은 내부 검증 기록에 보관한다. 문서와 완료 설명에는 검사 범위와 결과를 요약한다. 공개 manifest는 공통 재현에 필요한 정보만 기록한다. 실행 장비·브랜치·자원 설정과 개인 설정의 해시는 공개 기록에서 제외한다. 개인 절대 경로·토큰·사용자 원본 ID 목록을 manifest나 Git 문서에 넣지 않는다. 파일 내용이나 ID 규칙이 바뀌면 데이터 버전을 올리고 모든 파일을 같은 버전으로 함께 전달한다.

`validation.status`는 생성기의 저장·재로딩 검사 상태다. 원본 기준 독립 검증과 전달 가능 여부는 해당 데이터 버전과 함께 [CSV 생성·검증 보고서](../reports/csv-dataset-validation.md)에 기록한다.
