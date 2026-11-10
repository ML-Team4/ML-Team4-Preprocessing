# 공통 학습·평가 데이터 인수인계

Item-Based CF와 Implicit MF는 `datasets/listenbrainz-preprocessing/`의 같은 CSV 7개를 사용한다. 저장소를 Code → Download ZIP으로 내려받고 저장소 루트에서 `unzip datasets/listenbrainz-preprocessing.zip -d datasets`로 풀면 시작할 수 있다. 모델 담당자는 원본 전처리를 다시 실행할 필요가 없다.

## 주별 원본과 모델 입력의 차이

`datasets/raw/listens_*.csv.gz` 8개는 UTC 월요일 00:00 이상~다음 주 월요일 00:00 미만의 청취 원본이다. 네 컬럼은 `user_id` int64, `listened_at` timestamp[us], `artist_name` string, `recording_name` string이며 9,350,628행·5,000명이다. 원본 중복·공백·대소문자·특수문자·마이크로초를 보존했다. 주별 파일 경계로 모델의 시간 분할을 다시 정하지 않는다.

전처리 패키지는 고정 정제 후 학습·평가에 필요한 CSV 7개다. 다음 UTC 반개구간을 적용하며 시작 시각은 포함하고 끝은 포함하지 않는다.

| 용도 | UTC 시작 포함 | UTC 종료 미포함 | 정제 사건 |
| --- | --- | --- | ---: |
| train | 2026-08-17 00:00:00 | 2026-09-28 00:00:00 | 6,623,584 |
| validation | 2026-09-28 00:00:00 | 2026-10-05 00:00:00 | 1,104,514 |
| test | 2026-10-05 00:00:00 | 2026-10-12 00:00:00 | 1,084,955 |

## CSV의 실제 헤더와 역할

| 파일 | 헤더 | 행 수와 역할 |
| --- | --- | --- |
| train_interactions.csv | user_idx, item_idx, listen_count | 2,739,292개 고유 사용자·곡 쌍과 정제된 train 청취 횟수 |
| users.csv | user_idx, user_id | 5,000명, 공통 사용자 인덱스와 원본 ID 매핑 |
| items.csv | item_idx, artist_name, recording_name, train_listener_count, artist_key, recording_key | 1,525,005곡, 공통 곡 인덱스·표시 이름·고유 train 청취자 수·정규화 키 |
| validation_users.csv | user_idx | 검증 평가 사용자 3,922명 |
| validation_targets.csv | user_idx, item_idx | 해당 검증 사용자의 고유 정답 193,323쌍 |
| test_users.csv | user_idx | 테스트 평가 사용자 3,852명 |
| test_targets.csv | user_idx, item_idx | 해당 테스트 사용자의 고유 정답 201,203쌍 |

CSV는 헤더가 있는 UTF-8 BOM이다. 이름에 쉼표·따옴표·개행이 있으므로 CSV 파서를 사용한다. 문자열의 `NA`, `NULL`을 결측으로 추론하지 않는다. pandas를 쓰면 `encoding="utf-8-sig", keep_default_na=False`를 지정한다. 실제 파일·컬럼별 자료형과 정렬은 [데이터 계약](data-contract.md)에 있다.

`user_idx`, `item_idx`는 0부터 시작하며 파일별 공통 인덱스다. 모델별로 다시 번호를 만들면 정답·추천 결과·곡명 매핑이 어긋난다. 내부 연산상 재배열이 필요하면 역매핑으로 공통 인덱스를 복원한다. `selected_users.csv`의 1부터 시작하는 `user_index`는 원본 점검용이며 모델 입력 인덱스와 다르다.

## 폴더 한 곳을 지정하는 실행 예제

`python -m pip install pyarrow scipy` 후 아래에서 `DATA_DIR`만 지정하면 CSV 7개를 읽는다. 사용자 행 × 곡 열의 CSR을 만들며 행은 `user_idx`, 열은 `item_idx`, 값은 `listen_count`다. 관측되지 않은 좌표는 선호 여부를 모르는 쌍이다. 두 모델의 라이브러리가 다른 축 방향을 요구하면 내부에서 전치하되 공통 인덱스는 유지한다.

```python
from pathlib import Path
import numpy as np
import pyarrow as pa
import pyarrow.csv as pc
from scipy.sparse import csr_matrix

DATA_DIR = Path("datasets/listenbrainz-preprocessing")

def read(name, types):
    return pc.read_csv(DATA_DIR / name,
        parse_options=pc.ParseOptions(newlines_in_values=True),
        convert_options=pc.ConvertOptions(column_types=types,
            null_values=[], strings_can_be_null=False))

users = read("users.csv", {"user_idx": pa.int32(), "user_id": pa.int64()})
items = read("items.csv", {"item_idx": pa.int32(), "artist_name": pa.string(),
    "recording_name": pa.string(), "train_listener_count": pa.int32(),
    "artist_key": pa.string(), "recording_key": pa.string()})
train = read("train_interactions.csv", {"user_idx": pa.int32(),
    "item_idx": pa.int32(), "listen_count": pa.int64()})
counts = csr_matrix((train["listen_count"].to_numpy(),
    (train["user_idx"].to_numpy(), train["item_idx"].to_numpy())),
    shape=(users.num_rows, items.num_rows))
print("사용자 × 곡", counts.shape, "학습 쌍", counts.nnz,
      "청취 횟수 합", int(counts.sum()))

# 모델이 추천한 공통 item_idx를 곡명·아티스트명으로 조회한다.
recommended_item_idx = [0, 1, 2]  # 실행 예시 인덱스; 실제 모델의 추천 결과로 교체
lookup = {row["item_idx"]: row for row in
    items.select(["item_idx", "artist_name", "recording_name", "train_listener_count"])
         .take(pa.array(recommended_item_idx, type=pa.int32())).to_pylist()}
for item_idx in recommended_item_idx:
    print(item_idx, lookup[item_idx])

# 평가 사용자와 정답은 각 기간의 파일을 짝지어 사용한다.
evaluation = {}
for period in ("validation", "test"):
    eligible = read(f"{period}_users.csv", {"user_idx": pa.int32()})["user_idx"].to_numpy()
    targets = read(f"{period}_targets.csv", {"user_idx": pa.int32(), "item_idx": pa.int32()})
    target_u, target_i = (targets[k].to_numpy() for k in ("user_idx", "item_idx"))
    assert np.array_equal(np.unique(target_u), eligible)
    evaluation[period] = (eligible, targets)
    user_idx = int(eligible[0])
    truth = set(target_i[target_u == user_idx].tolist())
    train_seen = set(counts.getrow(user_idx).indices.tolist())
    assert truth.isdisjoint(train_seen)
    # 추천 점수 배열 scores에 scores[list(train_seen)] = -np.inf 적용 후 Top-10 선택
    print(period, "사용자", len(eligible), "정답 쌍", targets.num_rows,
          "첫 사용자 정답", len(truth), "후보", counts.shape[1] - len(train_seen))
```

곡명 조회 예시의 0·1·2는 조회 동작을 보여주는 값이며 사용자별 추천 결과가 아니다. 저장소의 `python scripts/use_dataset.py --data-dir datasets/listenbrainz-preprocessing`은 파일 무결성, 전체 인덱스·인기도·평가 정답 연결도 검사한다.

## 공통 기준과 모델 담당자의 선택 범위

두 모델이 공유하는 값은 CSV 7개, 사용자·곡 인덱스, 원래 train 카탈로그·학습 이력, UTC 분할, validation/test 사용자·정답과 고유 train 청취자 수다. `listen_count`는 정제 train의 원시 청취 횟수이며 log 변환된 값이나 confidence가 아니다. `items.csv`의 실제 컬럼 `train_listener_count`는 해당 곡을 들은 **고유 학습 사용자 수**로, 총 재생 수와 다르다.

각 담당자는 binary preference, `log1p(listen_count)`, confidence와 인기도 보정 방식, sparse matrix의 내부 방향·자료형, hyperparameter와 학습 알고리즘을 결정한다. 고유 청취자 수는 모델별 보정 계산과 공통 ARP 계산에 사용한다. 원시 입력·공통 인덱스·평가 범위를 임의로 다시 만들거나 저인기 곡을 삭제하지 않는다.

## 검증·테스트 평가

각 기간의 `*_users.csv` 전체를 분모로 쓰고 같은 기간의 `*_targets.csv`에서 `user_idx`로 정답을 연결한다. 두 기간의 사용자 교집합만 고르지 않는다. 후보는 **전체 train 카탈로그 − 해당 사용자의 원래 train 청취곡**이다. test에서도 validation 청취곡을 추가로 제외하지 않는다. validation과 test 정답에 같은 곡이 등장할 수 있다.

평가 포함 조건은 train 이력 있음·해당 기간 정답 1개 이상·후보 10개 이상이다. 5,000명 중 기간 활동 없음은 validation 795명·test 886명, 후보에 속한 정답 없음은 283명·262명이며 학습 이력 없음과 후보 10개 미만은 각 0명이다. 후보를 인기 상위 곡이나 임의 표본으로 축소하지 않는다.

추천은 사용자별 서로 다른 후보 10곡이다. `Recall@10`은 사용자별 정답 교집합 수/정답 수를 구한 뒤 사용자 평균, `ARP`는 추천 10곡의 `train_listener_count` 평균을 구한 뒤 사용자 평균이다. validation으로 설정을 고정하고 최초 train을 유지해 test에서 최종 평가한다. 계산식은 [공통 지표 정의](data-contract.md#지표의-공통-정의)를 참고한다.

## 정제 근거와 한계

- NFC와 고정 공백 정리 후 아티스트·제목의 두 문자열 튜플로 곡을 식별한다. 같은 유효 train에서 곡 키 166개가 줄었고 대소문자·구두점·버전 표기는 보존한다. 이름만으로 같은 이름의 다른 음원을 구별할 수는 없다.
- 같은 사용자·시각·정규화 곡 키의 초과 537,575행만 제외한다. 다른 시각의 반복과 동시각의 다른 곡은 보존한다. 필수값 누락·기간 밖·빈 이름 제외는 각 0행이다.
- 사건의 대표 원문 쌍은 UTF-8 바이트 순으로 선택한다. 표시 이름은 정제된 train에서 원문 쌍 빈도가 가장 큰 쌍이며 동률도 같은 순서로 정한다.
- 저인기 곡·고활동 사용자·모호한 비음악 신호를 자동 제거하지 않는다. 고유 청취자 한 명인 곡 1,204,318개는 전체 학습곡의 78.97%다. 서로 다른 사용자 근거가 부족한 곡의 동률·구별 한계는 모델에서 확인한다.

[고정 처리 기준](preprocessing-rules.md) · [시간 분할 검증](../reports/temporal-split-review.md) · [EDA 결과](../reports/listening-eda.md) · [모델 적합성 관측](../reports/model-readiness-review.md)

원본 점검·전처리를 재현하는 별도 경로는 [README의 원본에서 전처리 재현](../README.md#원본에서-전처리-재현)에 있다. 원본 `source_manifest.json`·`verified_8week_summary.json`은 변환 전 Parquet 기록, `distribution_manifest.json`과 `config/raw-csv-reference.toml`은 주별 CSV 배포 검증을 담당한다. 데이터 출처는 MetaBrainz Foundation / ListenBrainz, 이용 조건은 동봉 `COPYING`의 CC0 1.0 Universal이다.
