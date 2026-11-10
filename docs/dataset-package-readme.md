# ML Team4 공통 학습·평가 CSV 데이터

같은 데이터 버전의 CSV 7개를 함께 사용한다. 데이터 버전과 자료형·검증 기록은 dataset_manifest.json에 있다. 실제 데이터의 출처는 manifest의 source, 이용 조건은 COPYING을 확인한다. 합성 예제의 source.kind는 synthetic_example이다.

| 파일 | 컬럼과 용도 |
| --- | --- |
| train_interactions.csv | user_idx, item_idx, listen_count. 사용자·곡별 훈련 청취 횟수 |
| users.csv | user_idx, user_id. 공통 사용자 ID와 원본 ID 매핑 |
| items.csv | item_idx, artist_name, recording_name, train_listener_count, artist_key, recording_key. 곡 ID·곡명·가수명·고유 훈련 청취자 수·정규화 키 |
| validation_targets.csv, test_targets.csv | user_idx, item_idx. 각 평가 기간의 고유 정답 쌍 |
| validation_users.csv, test_users.csv | user_idx. 각 평가 기간의 대상 사용자 |

user_idx와 item_idx는 0부터 시작한다. 학습 기록 한 행은 고유 사용자·곡 쌍이며 listen_count에 실제 반복 청취 횟수를 합산했다. 후보는 학습 카탈로그에서 해당 사용자의 train 청취곡을 뺀 집합이다. test 정답에는 validation에서 들은 곡이 다시 포함될 수 있다. 모델 내부 ID를 바꿨다면 공통 ID로 복원해 결과를 공유한다.

CSV는 첫 행에 컬럼명을 갖는 UTF-8 BOM이다. 정수 ID와 횟수의 자료형을 지정하고, 아티스트명·곡명은 문자열로 읽는다. 쉼표·따옴표·개행을 CSV 인용 규칙으로 보존했다. 곡명에 개행이 있을 수 있어 CSV 파서로 행 수를 센다. pandas를 사용한다면 encoding="utf-8-sig", keep_default_na=False를 지정해 실제 이름인 NA나 NULL이 결측으로 바뀌는 것을 방지한다.

훈련은 2026-08-17 이상 09-28 미만, 검증은 09-28 이상 10-05 미만, 테스트는 10-05 이상 10-12 미만의 UTC 기록이다. 검증으로 모델 설정을 선택하고 테스트로 최종 평가한다. 두 평가 기간 모두 최초 train 카탈로그와 사용자별 train 이력을 기준으로 한다.

## 패키지 읽기

Python 3.11 이상에서 아래 예제에 필요한 pyarrow와 scipy를 설치한다. 전처리 코드를 재실행할 때의 의존성은 저장소 requirements.txt에 있다.

```bash
python -m pip install pyarrow scipy
```

패키지 폴더에서 다음 코드를 실행하면 파일 무결성을 확인하고 CSV로부터 희소 학습 행렬을 만들 수 있다. 곡명·가수명 확인과 평가 정답 로딩도 포함한다.

```python
import csv
import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.csv as arrow_csv
from scipy.sparse import csr_matrix

manifest = json.loads(Path("dataset_manifest.json").read_text(encoding="utf-8"))
for name, metadata in manifest["files"].items():
    path = Path(name)
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if path.stat().st_size != metadata["bytes"] or digest != metadata["sha256"]:
        raise ValueError(f"파일 무결성 검사가 실패했습니다: {name}")
print("파일 무결성 검사 통과")

train = arrow_csv.read_csv(
    "train_interactions.csv",
    convert_options=arrow_csv.ConvertOptions(column_types={
        "user_idx": pa.int32(), "item_idx": pa.int32(), "listen_count": pa.int64(),
    }),
)
shape = (manifest["mapping"]["users"], manifest["mapping"]["items"])
counts = csr_matrix((train["listen_count"].to_numpy(),
                     (train["user_idx"].to_numpy(), train["item_idx"].to_numpy())), shape=shape)
print(manifest["dataset_version"], counts.shape, counts.nnz)
if counts.shape[0]:
    history = counts.getrow(0)
    print("user_idx=0의 candidate 수", counts.shape[1] - history.nnz)
    first_item = int(history.indices[0])
    with open("items.csv", encoding="utf-8-sig", newline="") as stream:
        item = next(row for row in csv.DictReader(stream) if int(row["item_idx"]) == first_item)
    print(item["item_idx"], item["artist_name"], item["recording_name"])

for period in ("validation", "test"):
    with open(f"{period}_users.csv", encoding="utf-8-sig", newline="") as stream:
        first_user = next(csv.DictReader(stream), None)
    if first_user is not None:
        with open(f"{period}_targets.csv", encoding="utf-8-sig", newline="") as stream:
            target_count = sum(row["user_idx"] == first_user["user_idx"] for row in csv.DictReader(stream))
        print(period, "첫 평가 사용자의 target 수", target_count)
```

실제 8주 데이터의 학습 기록과 곡 목록은 Excel 한 시트의 행 한도를 넘는다. 전체 행을 분석하고 모델에 입력할 때 위 CSV 로딩 방법을 사용한다. 각 파일의 행 수와 검증 여부는 manifest에서 확인할 수 있다.
