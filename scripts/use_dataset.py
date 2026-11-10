"""공통 CSV 7개를 읽고 사용자·곡 매핑, 학습 행렬과 평가 정답을 확인한다."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.csv as arrow_csv
from scipy.sparse import csr_matrix

SCHEMAS = {
    "train_interactions": {"user_idx": pa.int32(), "item_idx": pa.int32(), "listen_count": pa.int64()},
    "users": {"user_idx": pa.int32(), "user_id": pa.int64()},
    "items": {"item_idx": pa.int32(), "artist_name": pa.string(), "recording_name": pa.string(),
              "train_listener_count": pa.int32(), "artist_key": pa.string(), "recording_key": pa.string()},
    **{f"{p}_users": {"user_idx": pa.int32()} for p in ("validation", "test")},
    **{f"{p}_targets": {"user_idx": pa.int32(), "item_idx": pa.int32()} for p in ("validation", "test")},
}


def load_dataset(data_dir: Path) -> dict:
    """입력 폴더 한 곳으로 파일 무결성·좌표·인기도·기간별 평가 연결을 검사한다."""
    manifest = json.loads((data_dir / "dataset_manifest.json").read_text(encoding="utf-8"))
    for name, entry in manifest["files"].items():
        path = data_dir / name
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if path.stat().st_size != entry["bytes"] or digest != entry["sha256"]:
            raise ValueError(f"파일 무결성 검사가 실패했습니다: {name}")
    tables = {}
    for name, types in SCHEMAS.items():
        table = arrow_csv.read_csv(data_dir / f"{name}.csv",
            parse_options=arrow_csv.ParseOptions(newlines_in_values=True),
            convert_options=arrow_csv.ConvertOptions(column_types=types,
                null_values=[], strings_can_be_null=False))
        if table.column_names != list(types) or any(c.null_count for c in table.columns):
            raise ValueError(f"CSV 헤더 또는 필수값이 다릅니다: {name}")
        if table.num_rows != manifest["files"][f"{name}.csv"]["rows"]:
            raise ValueError(f"CSV 행 수가 다릅니다: {name}")
        tables[name] = table
    users, items, train = (tables[name] for name in ("users", "items", "train_interactions"))
    shape = (users.num_rows, items.num_rows)
    for table, key, size in ((users, "user_idx", shape[0]), (items, "item_idx", shape[1])):
        if not np.array_equal(table[key].to_numpy(), np.arange(size)):
            raise ValueError("공통 인덱스의 순서·연속성이 다릅니다")
    u, i, n = (train[key].to_numpy() for key in ("user_idx", "item_idx", "listen_count"))
    if np.any(u < 0) or np.any(u >= shape[0]) or np.any(i < 0) or np.any(i >= shape[1]) or np.any(n <= 0):
        raise ValueError("학습 좌표 또는 청취 횟수의 범위가 다릅니다")
    counts = csr_matrix((n, (u, i)), shape=shape)
    if counts.nnz != train.num_rows or not np.array_equal(
            np.bincount(i, minlength=shape[1]), items["train_listener_count"].to_numpy()):
        raise ValueError("학습 좌표 중복 또는 고유 청취자 수가 다릅니다")
    evaluation = {}
    for period in ("validation", "test"):
        target = tables[f"{period}_targets"]
        target_u, target_i = (target[key].to_numpy() for key in ("user_idx", "item_idx"))
        eligible = tables[f"{period}_users"]["user_idx"].to_numpy()
        if (not np.array_equal(np.unique(target_u), eligible)
                or len(np.unique(eligible)) != len(eligible)
                or np.any(target_u < 0) or np.any(target_u >= shape[0])
                or np.any(target_i < 0) or np.any(target_i >= shape[1])
                or len(np.unique(target_u.astype(np.int64) * shape[1] + target_i)) != len(target_u)
                or np.any(np.asarray(counts[target_u, target_i]).ravel() != 0)):
            raise ValueError(f"{period}의 사용자·정답·학습 기청취곡 연결이 다릅니다")
        evaluation[period] = {"users": len(eligible), "target_pairs": target.num_rows}
    first = int(i[0]) if len(i) else None
    return {"dataset": manifest["dataset_version"], "csv_files_loaded": len(tables),
            "shape": list(shape), "nnz": counts.nnz, "listen_count_sum": int(n.sum()),
            "evaluation": evaluation,
            "example_item": items.slice(first, 1).to_pylist()[0] if first is not None else None}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("datasets/listenbrainz-preprocessing"))
    args = parser.parse_args()
    print(json.dumps(load_dataset(args.data_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
