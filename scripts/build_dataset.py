"""공통 학습·평가 패키지 생성. 새 버전은 저장 파일을 검증한 뒤 확정한다."""
from __future__ import annotations

import argparse
import csv
import errno
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from itertools import zip_longest
from pathlib import Path

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import scipy
from scipy import sparse

import preprocess_events as common
from preprocess_events import base

REPO = Path(__file__).resolve().parents[1]
PACKAGE_README = REPO / "docs/dataset-package-readme.md"
BATCH_ROWS = 65_536
MIN_CANDIDATES = 10
PERIODS = ("validation", "test")
USER_EXCLUSIONS = ("no_train", "no_period_activity", "no_target", "fewer_than_10_candidates")
PAIR_KINDS = ("new_item", "train_seen", "known_item_no_train_user", "candidate")
ID_PAIR = (("user_idx", pa.int32()), ("item_idx", pa.int32()))
PARQUET_FILES = {
    "train_interactions": (ID_PAIR + (("listen_count", pa.int64()),), ("user_idx", "item_idx")),
    "users": ((("user_idx", pa.int32()), ("user_id", pa.int64())), ("user_idx",)),
    "items": ((("item_idx", pa.int32()), ("artist_name", pa.string()),
               ("recording_name", pa.string()), ("train_listener_count", pa.int32()),
               ("artist_key", pa.string()), ("recording_key", pa.string())), ("item_idx",)),
    **{f"{period}_targets": (ID_PAIR, ("user_idx", "item_idx")) for period in PERIODS},
}


def build_train_tables(con, train: dict) -> None:
    """정제된 train_pairs·train_display_names에서 ID와 학습 파일 관계를 만든다."""
    if max(train["users"], train["items"]) > np.iinfo(np.int32).max:
        raise ValueError("Train user or item count exceeds the int32 contract")
    con.execute("""CREATE TABLE users AS SELECT
        (row_number() OVER (ORDER BY user_id) - 1)::INTEGER AS user_idx, user_id
        FROM (SELECT DISTINCT user_id FROM train_pairs)""")
    con.execute("""CREATE TABLE items AS WITH popularity AS (
        SELECT artist_key, recording_key, count(*)::INTEGER AS train_listener_count
        FROM train_pairs GROUP BY ALL)
        SELECT (row_number() OVER (ORDER BY encode(artist_key), encode(recording_key)) - 1)::INTEGER
            AS item_idx, artist_name, recording_name, train_listener_count, artist_key, recording_key
        FROM popularity JOIN train_display_names USING (artist_key, recording_key)""")
    con.execute("""CREATE TABLE train_interactions AS SELECT u.user_idx, i.item_idx, p.listen_count
        FROM train_pairs p JOIN users u USING (user_id) JOIN items i USING (artist_key, recording_key)""")
    con.execute("""CREATE TABLE train_history_sizes AS
        SELECT user_idx, count(*) AS items_heard FROM train_interactions GROUP BY user_idx""")


def build_evaluation(con, period: str, catalog_size: int, cohort_size: int, events: int) -> dict:
    """기간에 실제로 들은 곡만 분류한다. 후보의 전체 좌표 대신 개수만 계산한다."""
    con.execute(f"""CREATE TABLE {period}_classified AS WITH pairs AS (
        SELECT user_id, artist_key, recording_key, count(*) AS events
        FROM {period}_events GROUP BY ALL)
        SELECT p.*, u.user_idx, i.item_idx, CASE
            WHEN i.item_idx IS NULL THEN 'new_item'
            WHEN h.item_idx IS NOT NULL THEN 'train_seen'
            WHEN u.user_idx IS NULL THEN 'known_item_no_train_user'
            ELSE 'candidate' END AS kind
        FROM pairs p LEFT JOIN users u USING (user_id)
        LEFT JOIN items i USING (artist_key, recording_key)
        LEFT JOIN train_interactions h ON u.user_idx = h.user_idx AND i.item_idx = h.item_idx""")
    con.execute(f"""CREATE TABLE {period}_eligibility AS WITH activity AS (
        SELECT user_id, count(*) FILTER (WHERE kind = 'candidate') AS target_pairs
        FROM {period}_classified GROUP BY user_id)
        SELECT u.user_idx, coalesce(a.target_pairs, 0) AS target_pairs,
            CASE WHEN u.user_idx IS NULL THEN 'no_train'
                WHEN a.user_id IS NULL THEN 'no_period_activity'
                WHEN a.target_pairs = 0 THEN 'no_target'
                WHEN ? - h.items_heard < ? THEN 'fewer_than_10_candidates'
                ELSE 'eligible' END AS status
        FROM selected_cohort c LEFT JOIN users u USING (user_id)
        LEFT JOIN activity a ON c.user_id = a.user_id
        LEFT JOIN train_history_sizes h ON u.user_idx = h.user_idx""",
        [catalog_size, MIN_CANDIDATES])
    con.execute(f"""CREATE TABLE {period}_targets AS SELECT p.user_idx, p.item_idx
        FROM {period}_classified p JOIN {period}_eligibility e USING (user_idx)
        WHERE p.kind = 'candidate' AND e.status = 'eligible'""")

    stats = {kind: dict.fromkeys(("events", "unique_items", "user_item_pairs", "users"), 0)
             for kind in PAIR_KINDS}
    for row in base.rows(con, f"""SELECT kind, sum(events) AS events,
            count(DISTINCT (artist_key, recording_key)) AS unique_items,
            count(*) AS user_item_pairs, count(DISTINCT user_id) AS users
            FROM {period}_classified GROUP BY kind"""):
        stats[row.pop("kind")] = row
    users = dict.fromkeys((*USER_EXCLUSIONS, "eligible"), 0)
    users.update(con.execute(f"""SELECT status, count(*)
        FROM {period}_eligibility GROUP BY status""").fetchall())
    target_counts = dict(con.execute(f"""SELECT status, sum(target_pairs)
        FROM {period}_eligibility GROUP BY status""").fetchall())
    candidate = stats["candidate"]
    eligible_pairs = target_counts.get("eligible", 0)
    below_min_pairs = target_counts.get("fewer_than_10_candidates", 0)
    if (sum(s["events"] for s in stats.values()) != events or sum(users.values()) != cohort_size
            or eligible_pairs + below_min_pairs != candidate["user_item_pairs"]):
        raise RuntimeError(f"{period} event/pair/user accounting does not reconcile")
    con.execute(f"DROP TABLE {period}_classified")
    con.execute(f"DROP TABLE {period}_eligibility")
    return {
        "clean_events": events,
        "period_pairs_by_kind": stats,
        "candidate_repeat_events_collapsed": candidate["events"] - candidate["user_item_pairs"],
        "candidate_pairs_excluded_for_fewer_than_10_candidates": below_min_pairs,
        "user_denominator_selected_cohort": cohort_size,
        "user_exclusions": {reason: users[reason] for reason in USER_EXCLUSIONS},
        "eligible_users": users["eligible"],
        "target_pairs": eligible_pairs,
    }


def ordered_batches(con, table: str):
    """한 번에 최대 BATCH_ROWS행을 전달하는 정렬 쿼리와 필수 필드 스키마."""
    columns, order = PARQUET_FILES[table]
    schema = pa.schema([pa.field(name, dtype, nullable=False) for name, dtype in columns])
    projection = ", ".join(schema.names)
    result = con.execute(f"SELECT {projection} FROM {table} ORDER BY {', '.join(order)}")
    return schema, result.fetch_record_batch(BATCH_ROWS)


def write_parquet(con, staging: Path) -> None:
    for table in PARQUET_FILES:
        schema, reader = ordered_batches(con, table)
        with reader, pq.ParquetWriter(staging / f"{table}.parquet", schema, compression="zstd") as writer:
            for batch in reader:
                if any(column.null_count for column in batch.columns):
                    raise RuntimeError(f"Unexpected NULL in {table}")
                writer.write_batch(pa.RecordBatch.from_arrays(batch.columns, schema=schema))


def write_csr(staging: Path, train: dict) -> None:
    """정렬된 상호작용을 배치로 읽어 희소 열·값과 사용자별 행 포인터를 채운다."""
    shape = (train["users"], train["items"])
    nnz = train["user_item_pairs"]
    indices = np.empty(nnz, dtype=np.int32)
    data = np.empty(nnz, dtype=np.int64)
    indptr = np.zeros(shape[0] + 1, dtype=np.int64)
    offset = 0
    with pq.ParquetFile(staging / "train_interactions.parquet") as parquet:
        for batch in parquet.iter_batches(batch_size=BATCH_ROWS):
            users, items, counts = (column.to_numpy() for column in batch.columns)
            end = offset + batch.num_rows
            if end > nnz or np.any(users < 0) or np.any(users >= shape[0]):
                raise RuntimeError("Unexpected train pair count or user_idx")
            indices[offset:end] = items
            data[offset:end] = counts
            indptr[1:] += np.bincount(users, minlength=shape[0])
            offset = end
    if offset != nnz:
        raise RuntimeError("Train pair count differs from the cleaned train_pairs relation")
    np.cumsum(indptr, out=indptr)
    matrix = sparse.csr_matrix((data, indices, indptr), shape=shape)
    sparse.save_npz(staging / "train_counts.npz", matrix)


def verify_csr(staging: Path, train: dict) -> dict:
    """NPZ를 일반 소비자 API로 열어 좌표·값 전부를 저장된 Parquet과 대조한다."""
    path = staging / "train_counts.npz"
    matrix = sparse.load_npz(path)
    if not sparse.isspmatrix_csr(matrix) or matrix.shape != (train["users"], train["items"]):
        raise RuntimeError("Invalid CSR format or shape")
    nnz = train["user_item_pairs"]
    if (matrix.data.size != nnz or matrix.indices.size != nnz
            or matrix.indptr.size != matrix.shape[0] + 1 or matrix.indptr[-1] != nnz
            or matrix.data.dtype != np.int64 or np.any(matrix.data <= 0)):
        raise RuntimeError("Invalid CSR pointers, nnz or data dtype/counts")
    matrix.check_format(full_check=True)
    if not matrix.has_canonical_format or np.any(np.diff(matrix.indptr) <= 0):
        raise RuntimeError("CSR must have sorted unique items and a nonempty row per train user")
    total = int(matrix.data.sum())
    if total != train["events"]:
        raise RuntimeError("CSR count sum differs from the clean train event count")

    offset = 0
    with pq.ParquetFile(staging / "train_interactions.parquet") as parquet:
        for batch in parquet.iter_batches(batch_size=BATCH_ROWS):
            users, items, counts = (column.to_numpy() for column in batch.columns)
            end = offset + batch.num_rows
            row_ids = np.searchsorted(matrix.indptr, np.arange(offset, end), side="right") - 1
            if (not np.array_equal(users, row_ids) or not np.array_equal(items, matrix.indices[offset:end])
                    or not np.array_equal(counts, matrix.data[offset:end])):
                raise RuntimeError("Reloaded CSR coordinates/counts differ from train_interactions")
            offset = end
    if offset != nnz:
        raise RuntimeError("Reloaded CSR and train_interactions nnz differ")
    return {"format": "csr", "shape": list(matrix.shape), "nnz": nnz, "sum_counts": total,
            "data_dtype": str(matrix.data.dtype), "indices_dtype": str(matrix.indices.dtype),
            "indptr_dtype": str(matrix.indptr.dtype), "canonical": True,
            "matches_interactions_all_coordinates_and_values": True}


def target_users(con, period: str) -> list[int]:
    return [row[0] for row in con.execute(f"""SELECT DISTINCT user_idx
        FROM {period}_targets ORDER BY user_idx""").fetchall()]


def fingerprint(path: Path) -> dict:
    return {"bytes": path.stat().st_size, "sha256": base.sha256(path)}


def verify_package(con, staging: Path, train: dict, evaluation: dict, copying: dict) -> dict:
    """파일의 물리적 행 순서·스키마·내용을 생성 관계와 대조하고 파일 메타데이터를 반환한다."""
    expected_rows = {
        "users": train["users"], "items": train["items"],
        "train_interactions": train["user_item_pairs"],
        **{f"{p}_targets": evaluation[p]["target_pairs"] for p in PERIODS},
    }
    files = {}
    for table, (_, order) in PARQUET_FILES.items():
        path = staging / f"{table}.parquet"
        schema, reader = ordered_batches(con, table)
        with reader, pq.ParquetFile(path) as parquet:
            if parquet.schema_arrow != schema or parquet.metadata.num_rows != expected_rows[table]:
                raise RuntimeError(f"Reloaded schema/row count differs for {path.name}")
            # write_batch writes one row group. Match each reloaded group in physical order.
            for expected, group in zip_longest(reader, range(parquet.num_row_groups)):
                if expected is None or group is None:
                    raise RuntimeError(f"Reloaded row groups differ for {path.name}")
                actual = parquet.read_row_group(group)
                if not actual.equals(pa.Table.from_arrays(expected.columns, schema=schema)):
                    raise RuntimeError(f"Reloaded rows/order differ for {path.name}")
        files[path.name] = {**fingerprint(path), "rows": expected_rows[table],
                            "columns": {f.name: str(f.type) for f in schema},
                            "column_order": schema.names, "nullable": False,
                            "sorted_by": list(order), "compression": "zstd"}
    for period in PERIODS:
        path = staging / f"{period}_users.csv"
        ids = target_users(con, period)
        with path.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.reader(stream, strict=True))
        expected = [["user_idx"], *[[str(user_idx)] for user_idx in ids]]
        if rows != expected or len(ids) != evaluation[period]["eligible_users"]:
            raise RuntimeError(f"Reloaded CSV differs from distinct users in {period}_targets")
        files[path.name] = {**fingerprint(path), "rows": len(ids), "columns": {"user_idx": "decimal int32"},
                            "column_order": ["user_idx"], "encoding": "UTF-8", "header": True,
                            "sorted_by": ["user_idx"]}
    matrix = verify_csr(staging, train)
    files["train_counts.npz"] = {**fingerprint(staging / "train_counts.npz"), **matrix,
                                "axes": ["user_idx", "item_idx"]}
    files["README.md"] = {**fingerprint(staging / "README.md"), "format": "UTF-8 Markdown"}
    files["COPYING"] = fingerprint(staging / "COPYING")
    if files["COPYING"] != copying or files["README.md"]["sha256"] != base.sha256(PACKAGE_README):
        raise RuntimeError("Copied package documents differ from their inputs")
    return files


def build_package(config: Path, output_root: Path, version: str, *,
                  reference_path: Path = common.REFERENCE, expected_users: int = 5000,
                  example_script: Path | None = None, example_variant: str | None = None) -> Path:
    """원본 검증부터 최종 경로 확정까지 실행한다. 대체 입력 기준은 합성 테스트에서 사용한다."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", version):
        raise ValueError("Version must be a single safe directory name (1–80 characters)")
    output_root = output_root.resolve()
    settings = common.read_settings(config)
    raw = settings["raw_dir"]
    if output_root == raw or raw in output_root.parents:
        raise ValueError("Output must not be inside raw_dir")
    if output_root.is_relative_to(REPO):
        relative = output_root.relative_to(REPO).as_posix() + "/"
        ignored = subprocess.run(["git", "check-ignore", "--no-index", "-q", relative], cwd=REPO)
        if ignored.returncode != 0:
            raise ValueError("Output inside the repository must be Git-ignored")
    final = output_root / version
    if os.path.lexists(final):
        raise FileExistsError(f"Dataset version already exists: {version}")
    output_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{version}-", dir=output_root) as folder:
        staging = Path(folder)
        build_staged_package(staging, config, settings, version, reference_path, expected_users,
                             example_script, example_variant)
        if os.path.lexists(final):
            raise FileExistsError(f"Dataset version already exists: {version}")
        # 각 실행은 고유한 임시 폴더를 쓴다. POSIX rename은 다른 실행이 먼저
        # 확정한 비어 있지 않은 패키지 디렉터리를 교체할 수 없다.
        try:
            staging.rename(final)
        except OSError as error:
            if error.errno in (errno.EEXIST, errno.ENOTEMPTY):
                raise FileExistsError(f"Dataset version already exists: {version}") from error
            raise
    return final


def build_staged_package(staging: Path, config: Path, settings: dict, version: str,
                         reference_path: Path, expected_users: int, example_script: Path | None,
                         example_variant: str | None) -> None:
    raw = settings["raw_dir"]
    trace = base.git_trace(REPO)
    code_paths = [Path(__file__), Path(common.__file__), Path(base.__file__), PACKAGE_README,
                  REPO / "requirements.txt"]
    if example_script is not None:
        code_paths.append(example_script)
    source_paths = {str(p.resolve().relative_to(REPO)): p for p in code_paths}
    source_paths.update({"input_reference": reference_path, "local_config": config})
    source_hashes = {key: base.sha256(path) for key, path in source_paths.items()}
    before = common.verify_input_files(raw, common.read_reference(reference_path))
    source = json.loads((raw / "source_manifest.json").read_text(encoding="utf-8"))
    with common.working_connection(settings) as con:
        inputs = common.validate_input_tables(con, raw, expected_users=expected_users)
        cleaning = common.build_clean_events(con)
        train = cleaning["train"]
        build_train_tables(con, train)
        split_sizes = {p["split"]: p["events"] for p in cleaning["partitions"]}
        evaluation = {p: build_evaluation(con, p, train["items"], expected_users, split_sizes[p])
                      for p in PERIODS}
        write_parquet(con, staging)
        for period in PERIODS:
            with (staging / f"{period}_users.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream, lineterminator="\n")
                writer.writerow(["user_idx"])
                writer.writerows((user_idx,) for user_idx in target_users(con, period))
        write_csr(staging, train)
        shutil.copyfile(PACKAGE_README, staging / "README.md")
        shutil.copyfile(raw / "COPYING", staging / "COPYING")
        copying = next({k: entry[k] for k in ("bytes", "sha256")}
                       for entry in before if entry["file"] == "COPYING")
        files = verify_package(con, staging, train, evaluation, copying)
        engine = base.one(con, "PRAGMA version")

    if common.fingerprint_inputs(raw) != before:
        raise RuntimeError("Input changed during build")
    if {key: base.sha256(path) for key, path in source_paths.items()} != source_hashes:
        raise RuntimeError("Code or settings changed during build")
    synthetic = example_script is not None
    command = ["python", "scripts/build_dataset.py", "--config", "<config>"]
    if synthetic:
        command = ["python", example_script.relative_to(REPO).as_posix(), "--variant", example_variant]
    command += ["--output-root", "<output-root>", "--version", version]
    manifest = {
        "contract_version": "1.0.0", "dataset_version": version, "rules_version": "1.0.0",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": {"kind": "synthetic_example" if synthetic else "listenbrainz",
                   "provider": None if synthetic else "MetaBrainz Foundation / ListenBrainz",
                   "url": source["source_url"], "snapshot_utc": source["source_snapshot_utc"],
                   "license": "CC0-1.0", "license_evidence": "COPYING"},
        "input_files": before, "input_validation": inputs,
        "processing": {
            "normalization": "DuckDB nfc_normalize, fixed whitespace runs to ASCII space, trim",
            "whitespace_codepoints": [f"U+{c:04X}" for c in base.WHITESPACE],
            "deduplication_key": ["user_id", "listened_at", "artist_key", "recording_key"],
            "exclusion_priority": list(common.EXCLUSION_REASONS),
            "event_original_names": "First original name pair by UTF-8 bytes per deduplication key",
            "display_names": "Most frequent original name pair in clean train, UTF-8 byte order for ties",
            "train_fixed_for_both_periods": True, "cleaning": cleaning,
        },
        "mapping": {"start": 0, "dtype": "int32", "users": train["users"], "items": train["items"],
                    "user_id_order": "numeric ascending", "item_key_order": "tuple of UTF-8 bytes ascending",
                    "users_sha256": files["users.parquet"]["sha256"],
                    "items_sha256": files["items.parquet"]["sha256"]},
        "evaluation": {
            "candidate_rule": "Entire train catalog minus this user's original train items",
            "target_rule": "Distinct period items intersected with candidates",
            "min_candidates": MIN_CANDIDATES, "min_targets": 1,
            "user_exclusion_priority": list(USER_EXCLUSIONS),
            "pair_accounting": ("Pair categories are disjoint. Unique items and users can overlap across "
                                "categories and must not be summed."),
            "periods": evaluation,
        },
        "files": files,
        "validation": {
            "status": "passed",
            "scope": "Package generation and reload integrity",
            "checks": ["input bytes/SHA256 and cohort", "exclusive cleaning and split accounting",
                       "evaluation event/pair/cohort accounting", "Parquet schema and ordered rows vs build tables",
                       "CSV users vs distinct target users", "CSR format, shape, nnz, canonical order and sum",
                       "all CSR coordinates and counts vs saved interactions", "copied documents",
                       "input, code and config SHA256 unchanged"],
        },
        "execution": {
            "branch": trace["branch"], "head_at_execution": trace["head"],
            "dirty_at_execution": None if trace["status"] is None else bool(trace["status"]),
            "source_and_config_sha256": source_hashes, "reproduction_argv": command,
            "python": platform.python_version(), "os": platform.platform(),
            "duckdb": duckdb.__version__, "duckdb_engine": engine,
            "nfc_library": {"utf8proc": "2.9.0", "unicode": "15.1.0"} if duckdb.__version__ == "1.4.4" else None,
            "numpy": np.__version__, "scipy": scipy.__version__, "pyarrow": pa.__version__,
            "memory_limit": settings["memory_limit"], "threads": settings["threads"],
            "arrow_batch_rows": BATCH_ROWS,
        },
    }
    manifest_path = staging / "dataset_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if json.loads(manifest_path.read_text(encoding="utf-8")) != manifest:
        raise RuntimeError("Reloaded manifest differs from build metadata")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="원본과 임시 저장 위치를 지정한 로컬 설정")
    parser.add_argument("--output-root", required=True, type=Path, help="버전별 폴더를 저장할 Git 제외 경로")
    parser.add_argument("--version", required=True, help="아직 존재하지 않는 데이터 버전")
    args = parser.parse_args()
    final = build_package(args.config, args.output_root, args.version)
    print(f"{final}\ndataset_manifest.json SHA256 {base.sha256(final / 'dataset_manifest.json')}")


if __name__ == "__main__":
    main()
