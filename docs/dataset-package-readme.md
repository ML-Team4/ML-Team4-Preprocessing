# ML Team4 공통 학습·평가 데이터

`dataset_manifest.json`의 `dataset_version`과 별도로 받은 manifest SHA256을 확인한다.
같은 버전의 파일을 한 묶음으로 사용한다. 합성 예제는 manifest의
`source.kind`가 `synthetic_example`이며 학습·평가 절차 확인용이다.
실제 데이터의 제공 기관·덤프·스냅샷은 manifest의 `source`를, 이용 조건은
`COPYING`을 확인한다. `users.parquet`에는 원본 사용자 ID 매핑이 들어 있다.

| 파일 | 용도 |
| --- | --- |
| `train_counts.npz` | 사용자 행 × 곡 열의 희소 CSR, 값은 정제 후 원시 청취 횟수 |
| `train_interactions.parquet` | CSR과 같은 고유 사용자·곡 좌표와 청취 횟수 |
| `users.parquet`, `items.parquet` | 0 기반 공통 ID 매핑, 대표 원문 이름, 곡별 고유 학습 청취자 수 |
| `validation_targets.parquet`, `test_targets.parquet` | 각 기간 평가 사용자들의 고유 정답 쌍 |
| `validation_users.csv`, `test_users.csv` | 각 기간의 평가 사용자 ID |

두 기간의 후보는 학습 카탈로그에서 해당 사용자의 **train** 청취곡을 뺀 집합이다.
후보 개수는 CSR 열 수에서 해당 행의 비제로 수를 뺀 값이다. test 정답에는
validation에서도 들은 곡이 포함될 수 있다. 모델의 내부 ID를 재배열하면
공통 ID로 복원한 뒤 결과를 공유한다.

패키지 폴더에서 `shasum -a 256 dataset_manifest.json`으로 전달받은 해시를 대조한다.
Python 3.11 이상과 `numpy==2.2.6`, `scipy==1.15.3`, `pyarrow==20.0.0`을 사용해
아래 예제를 같은 폴더에서 실행한다.

```python
import csv
import hashlib
import json
from pathlib import Path

import pyarrow.parquet as pq
from scipy.sparse import load_npz

manifest = json.loads(Path("dataset_manifest.json").read_text(encoding="utf-8"))
for name, metadata in manifest["files"].items():
    path = Path(name)
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if path.stat().st_size != metadata["bytes"] or digest != metadata["sha256"]:
        raise ValueError(f"File size or SHA256 mismatch: {name}")

counts = load_npz("train_counts.npz")
print(manifest["dataset_version"], counts.shape, counts.nnz)
if counts.shape[0]:
    history = counts.getrow(0)
    print("user_idx=0 candidate count", counts.shape[1] - history.nnz)
    items = pq.read_table("items.parquet", filters=[("item_idx", "=", int(history.indices[0]))])
    print(items.select(["item_idx", "train_listener_count"]).to_pylist())

for period in ("validation", "test"):
    with open(f"{period}_users.csv", encoding="utf-8", newline="") as stream:
        first_user = next(csv.DictReader(stream), None)
    if first_user is not None:
        targets = pq.read_table(f"{period}_targets.parquet",
                                filters=[("user_idx", "=", int(first_user["user_idx"]))])
        print(period, targets.num_rows, "targets for the first evaluation user")
```
