"""저장된 학습·평가 패키지를 원본 청취 사건에서 독립적으로 대조한다."""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from scipy import sparse

import preprocess_events as inputs
from audit_raw_data import WHITESPACE, one, sha256
from review_temporal_split import EVENT_KEY, TRACK_KEY, build_reference

BATCH_ROWS = 65_536
PERIODS = ("validation", "test")
INT = pa.int32()
ID_PAIR = [("user_idx", INT), ("item_idx", INT)]
SCHEMAS = {
    "users": ([("user_idx", INT), ("user_id", pa.int64())], ["user_idx"]),
    "items": ([("item_idx", INT), ("artist_name", pa.string()), ("recording_name", pa.string()),
               ("train_listener_count", INT), ("artist_key", pa.string()),
               ("recording_key", pa.string())], ["item_idx"]),
    "train_interactions": (ID_PAIR + [("listen_count", pa.int64())], ["user_idx", "item_idx"]),
    **{f"{period}_targets": (ID_PAIR, ["user_idx", "item_idx"]) for period in PERIODS},
}


def build_expected_train(con, parquet: Path) -> tuple[list[dict], dict, dict]:
    """정제 사건에서 기간별 집계와 정렬된 공통 ID의 기대값을 산출한다."""
    build_reference(con, parquet)
    con.execute("""CREATE VIEW expected_events AS SELECT e.*, p.period FROM review_events e
        JOIN review_periods p ON e.listened_at >= p.start_utc AND e.listened_at < p.end_utc""")
    partitions = []
    for period, start, end in con.execute("SELECT * FROM review_periods ORDER BY start_utc").fetchall():
        stats = one(con, f"""SELECT count(*) AS events, count(DISTINCT user_id) AS users,
            count(DISTINCT ({TRACK_KEY})) AS items FROM expected_events WHERE period = ?""", [period])
        partitions.append({"split": period, "start_inclusive_utc": str(start),
                           "end_exclusive_utc": str(end), **stats})
    exclusions = dict(con.execute("""SELECT category, count(*) FROM review_rows
        WHERE category <> 'valid' GROUP BY category""").fetchall())
    exclusions = {key: exclusions.get(key, 0) for key in
                  ("missing_required", "outside_window", "empty_name")}
    exclusions["duplicate_excess"] = con.execute(
        "SELECT coalesce(sum(valid_rows - 1), 0) FROM review_events").fetchone()[0]
    con.execute("""CREATE TABLE expected_counts AS SELECT user_id, artist_key, recording_key,
        count(*) AS listen_count FROM expected_events WHERE period = 'train' GROUP BY ALL""")
    con.execute("""CREATE TABLE expected_users AS SELECT
        (row_number() OVER (ORDER BY user_id) - 1)::INTEGER AS user_idx, user_id
        FROM (SELECT DISTINCT user_id FROM expected_counts)""")
    # 사건마다 원문 쌍의 최솟값을 고른 뒤 정제된 train의 원문 쌍 빈도를 센다.
    con.execute(f"""CREATE TABLE expected_items AS WITH originals AS (
        SELECT {EVENT_KEY}, min((encode(artist_name), encode(recording_name))) AS original
        FROM review_rows WHERE category = 'valid'
        AND listened_at >= (SELECT start_utc FROM review_periods WHERE period = 'train')
        AND listened_at < (SELECT end_utc FROM review_periods WHERE period = 'train')
        GROUP BY {EVENT_KEY}
    ), variants AS (
        SELECT {TRACK_KEY}, original, count(*) AS frequency FROM originals GROUP BY ALL
    ), display AS (
        SELECT * FROM variants QUALIFY row_number() OVER (
            PARTITION BY {TRACK_KEY} ORDER BY frequency DESC, original) = 1
    ), popularity AS (
        SELECT {TRACK_KEY}, count(DISTINCT user_id)::INTEGER AS train_listener_count
        FROM expected_events WHERE period = 'train' GROUP BY {TRACK_KEY}
    )
    SELECT (row_number() OVER (ORDER BY encode(artist_key), encode(recording_key)) - 1)::INTEGER
        AS item_idx, decode(original[1]) AS artist_name, decode(original[2]) AS recording_name,
        train_listener_count, artist_key, recording_key
    FROM popularity JOIN display USING ({TRACK_KEY})""")
    con.execute("DROP TABLE review_rows")
    con.execute(f"""CREATE TABLE expected_train_interactions AS SELECT user_idx, item_idx, listen_count
        FROM expected_counts JOIN expected_users USING (user_id)
        JOIN expected_items USING ({TRACK_KEY})""")
    train = one(con, """SELECT (SELECT count(*) FROM expected_users) AS users,
        (SELECT count(*) FROM expected_items) AS items, count(*) AS user_item_pairs,
        coalesce(sum(listen_count), 0) AS events FROM expected_train_interactions""")
    return partitions, exclusions, train


def build_expected_evaluation(con, period: str, catalog_size: int) -> dict:
    """기간 고유 곡에 원래 train 카탈로그와 사용자별 이력을 적용한다."""
    pairs = f"expected_{period}_pairs"
    users = f"expected_{period}_eligibility"
    con.execute(f"""CREATE TABLE {pairs} AS WITH heard AS (
        SELECT user_id, {TRACK_KEY}, count(*) AS events
        FROM expected_events WHERE period = ? GROUP BY ALL
    )
    SELECT heard.*, u.user_idx, i.item_idx, CASE
        WHEN i.item_idx IS NULL THEN 'new_item'
        WHEN h.listen_count IS NOT NULL THEN 'train_seen'
        WHEN u.user_idx IS NULL THEN 'known_item_no_train_user'
        ELSE 'candidate' END AS kind
    FROM heard LEFT JOIN expected_items i USING ({TRACK_KEY})
    LEFT JOIN expected_counts h USING (user_id, {TRACK_KEY})
    LEFT JOIN expected_users u USING (user_id)""", [period])
    con.execute(f"""CREATE TABLE {users} AS WITH activity AS (
        SELECT user_id, count(*) FILTER (WHERE kind = 'candidate') AS target_count
        FROM {pairs} GROUP BY user_id
    ), history AS (
        SELECT user_id, count(*) AS heard_count FROM expected_counts GROUP BY user_id
    )
    SELECT u.user_idx, coalesce(a.target_count, 0) AS target_count, CASE
        WHEN h.heard_count IS NULL THEN 'no_train'
        WHEN a.target_count IS NULL THEN 'no_period_activity'
        WHEN a.target_count = 0 THEN 'no_target'
        WHEN ? - h.heard_count < 10 THEN 'fewer_than_10_candidates'
        ELSE 'eligible' END AS status
    FROM selected_cohort LEFT JOIN expected_users u USING (user_id)
    LEFT JOIN activity a USING (user_id) LEFT JOIN history h USING (user_id)""", [catalog_size])
    con.execute(f"""CREATE VIEW expected_{period}_users AS SELECT user_idx FROM {users}
        WHERE status = 'eligible'""")
    con.execute(f"""CREATE VIEW expected_{period}_targets AS SELECT user_idx, item_idx
        FROM {pairs} SEMI JOIN expected_{period}_users USING (user_idx) WHERE kind = 'candidate'""")
    columns = ("events", "unique_items", "user_item_pairs", "users")
    kinds = {kind: dict.fromkeys(columns, 0) for kind in
             ("new_item", "train_seen", "known_item_no_train_user", "candidate")}
    for kind, *values in con.execute(f"""SELECT kind, sum(events),
        count(DISTINCT ({TRACK_KEY})), count(*), count(DISTINCT user_id)
        FROM {pairs} GROUP BY kind""").fetchall():
        kinds[kind] = dict(zip(columns, values))
    statuses = dict(con.execute(f"SELECT status, count(*) FROM {users} GROUP BY status").fetchall())
    candidate = kinds["candidate"]
    targets, below_min = con.execute(f"""SELECT
        coalesce(sum(target_count) FILTER (WHERE status = 'eligible'), 0),
        coalesce(sum(target_count) FILTER (WHERE status = 'fewer_than_10_candidates'), 0)
        FROM {users}""").fetchone()
    return {
        "period_pairs_by_kind": kinds,
        "candidate_repeat_events_collapsed": candidate["events"] - candidate["user_item_pairs"],
        "candidate_pairs_excluded_for_fewer_than_10_candidates": below_min,
        "user_denominator_selected_cohort": sum(statuses.values()),
        "user_exclusions": {reason: statuses.get(reason, 0) for reason in
                            ("no_train", "no_period_activity", "no_target", "fewer_than_10_candidates")},
        "eligible_users": statuses.get("eligible", 0),
        "target_pairs": targets,
        "clean_events": sum(stats["events"] for stats in kinds.values()),
    }


def compare_sets(con, expected: str, actual: str, columns: list[str]) -> dict:
    """유일성이 검사된 관계를 양방향으로 대조하며 집계만 반환한다."""
    key = ", ".join(columns)
    return one(con, f"""SELECT
        (SELECT count(*) FROM {expected} ANTI JOIN {actual} USING ({key})) AS missing,
        (SELECT count(*) FROM {actual} ANTI JOIN {expected} USING ({key})) AS extra""")


def require_metadata(actual: dict, expected: dict, label: str) -> None:
    """계약 필드의 존재·자료형·값을 확인하고 허용된 추가 필드는 보존한다."""
    for key, value in expected.items():
        if key not in actual or type(actual[key]) is not type(value) or actual[key] != value:
            raise ValueError(f"{label}의 manifest 필드가 다릅니다: {key}")


def read_parquet(con, package: Path, table: str, entry: dict) -> dict:
    """Arrow로 저장 스키마·NULL·물리적 순서를 검사하고 전체 행을 대조한다."""
    path = package / f"{table}.parquet"
    fields, order = SCHEMAS[table]
    schema = pa.schema([pa.field(name, dtype, nullable=False) for name, dtype in fields])
    require_metadata(entry, {
        "columns": {field.name: str(field.type) for field in schema},
        "column_order": schema.names, "nullable": False,
        "sorted_by": order, "compression": "zstd",
    }, path.name)
    previous = -1
    with pq.ParquetFile(path) as parquet:
        require_metadata(entry, {"rows": parquet.metadata.num_rows}, path.name)
        if parquet.schema_arrow != schema or parquet.metadata.num_rows != entry["rows"]:
            raise ValueError(f"{path.name}의 열 순서·자료형·필수 필드 또는 행 수가 다릅니다")
        for group in range(parquet.num_row_groups):
            metadata = parquet.metadata.row_group(group)
            if any(metadata.column(i).compression != "ZSTD" for i in range(len(schema))):
                raise ValueError(f"{path.name}의 압축이 ZSTD가 아닙니다")
        for batch in parquet.iter_batches(batch_size=BATCH_ROWS):
            if any(col.null_count for col in batch.columns):
                raise ValueError(f"{path.name}에 NULL이 있습니다")
            indices = [batch[name].to_numpy().astype(np.int64) for name in order]
            if any(np.any(values < 0) for values in indices):
                raise ValueError(f"{path.name}에 음수 인덱스가 있습니다")
            # 비음수 int32 두 열의 순서쌍을 int64로 비교해 배치 경계도 검사한다.
            keys = indices[0] if len(indices) == 1 else (indices[0] << 32) + indices[1]
            if keys[0] <= previous or np.any(keys[1:] <= keys[:-1]):
                raise ValueError(f"{path.name}의 ID가 중복되거나 정렬되지 않았습니다")
            previous = keys[-1]
    actual = f"stored_{table}"
    con.read_parquet(str(path)).create_view(actual)
    return compare_sets(con, f"expected_{table}", actual, schema.names)


def read_evaluation_users(con, package: Path, period: str, entry: dict) -> dict:
    path = package / f"{period}_users.csv"
    require_metadata(entry, {
        "columns": {"user_idx": "decimal int32"}, "column_order": ["user_idx"],
        "encoding": "UTF-8", "header": True, "sorted_by": ["user_idx"],
    }, path.name)
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.reader(stream, strict=True)
        if next(reader, None) != ["user_idx"]:
            raise ValueError(f"{path.name}의 헤더가 다릅니다")
        ids = []
        previous = -1
        for row in reader:
            if len(row) != 1 or not row[0].isascii() or not row[0].isdecimal():
                raise ValueError(f"{path.name}에 잘못된 10진수 ID가 있습니다")
            index = int(row[0])
            if not previous < index <= np.iinfo(np.int32).max or row[0] != str(index):
                raise ValueError(f"{path.name}의 ID 표기·범위·정렬 또는 유일성이 계약과 다릅니다")
            ids.append(index)
            previous = index
    require_metadata(entry, {"rows": len(ids)}, path.name)
    table = f"stored_{period}_users"
    con.register(table, pa.table({"user_idx": pa.array(ids, type=INT)}))
    targets = f"stored_{period}_target_users"
    con.execute(f"CREATE VIEW {targets} AS SELECT DISTINCT user_idx FROM stored_{period}_targets")
    return {
        "expected_users": compare_sets(con, f"expected_{period}_users", table, ["user_idx"]),
        "stored_target_users": compare_sets(con, targets, table, ["user_idx"]),
    }


def read_csr(con, path: Path, train: dict, entry: dict) -> dict:
    shape = (train["users"], train["items"])
    nnz = train["user_item_pairs"]
    require_metadata(entry, {
        "format": "csr", "axes": ["user_idx", "item_idx"], "shape": list(shape), "nnz": nnz,
        "sum_counts": train["events"], "canonical": True,
        "matches_interactions_all_coordinates_and_values": True,
    }, path.name)
    # SciPy는 indptr[-1] 뒤의 원소를 잘라낼 수 있어 저장 배열을 먼저 검사한다.
    with np.load(path, allow_pickle=False) as stored:
        stored_shape = stored["shape"]
        if (stored["format"].shape != () or stored["format"].item() != b"csr"
                or stored_shape.shape != (2,) or stored_shape.dtype.kind not in ("i", "u")
                or tuple(stored_shape) != shape):
            raise ValueError("저장된 CSR의 format 또는 shape이 계약과 다릅니다")
        sizes = {"data": nnz, "indices": nnz, "indptr": shape[0] + 1}
        for name, size in sizes.items():
            values = stored[name]
            allowed = ("int64",) if name == "data" else ("int32", "int64")
            if values.ndim != 1 or values.size != size:
                raise ValueError(f"저장된 CSR의 {name} 배열 차원 또는 길이가 다릅니다")
            if str(values.dtype) not in allowed or str(values.dtype) != entry[f"{name}_dtype"]:
                raise ValueError(f"CSR의 {name} 자료형이 계약 또는 manifest와 다릅니다")
    matrix = sparse.load_npz(path)
    if (not sparse.isspmatrix_csr(matrix) or matrix.shape != shape or matrix.nnz != nnz
            or matrix.indptr.size != shape[0] + 1 or matrix.indptr[0] != 0
            or matrix.indptr[-1] != nnz or matrix.indices.size != nnz or matrix.data.size != nnz
            or np.any(matrix.indptr[1:] <= matrix.indptr[:-1])):
        raise ValueError("CSR의 format·shape·nnz 또는 행 포인터가 계약과 다릅니다")
    matrix.check_format(full_check=True)
    if np.any(matrix.data <= 0) or not matrix.has_canonical_format:
        raise ValueError("CSR에 0 이하의 값, 정렬되지 않은 열 또는 중복 열이 있습니다")
    total = int(matrix.data.sum())
    if (list(shape) != entry["shape"] or nnz != entry["nnz"]
            or total != entry["sum_counts"] or total != train["events"]):
        raise ValueError("CSR의 규모 또는 청취 횟수 합계가 다릅니다")
    offset = 0
    mismatches = 0
    with con.execute("""SELECT * FROM expected_train_interactions
            ORDER BY user_idx, item_idx""").fetch_record_batch(BATCH_ROWS) as reader:
        for batch in reader:
            end = offset + batch.num_rows
            user, item, count = (col.to_numpy() for col in batch.columns)
            row = np.searchsorted(matrix.indptr, np.arange(offset, end), side="right") - 1
            mismatches += int(np.count_nonzero((row != user)
                           | (matrix.indices[offset:end] != item) | (matrix.data[offset:end] != count)))
            offset = end
    return {"shape": list(shape), "nnz": nnz, "sum_counts": total,
            "canonical": True, "mismatching_ordered_entries": mismatches}


def package_fingerprints(package: Path, manifest: dict) -> dict:
    names = {f"{name}.parquet" for name in SCHEMAS} | {
        "train_counts.npz", "validation_users.csv", "test_users.csv", "README.md", "COPYING"}
    if set(manifest["files"]) != names or {path.name for path in package.iterdir()} != (
            names | {"dataset_manifest.json"}):
        raise ValueError("패키지 또는 manifest의 파일 목록이 계약과 다릅니다")
    fingerprints = {name: {"bytes": (package / name).stat().st_size,
                           "sha256": sha256(package / name)} for name in sorted(names)}
    for name, actual in fingerprints.items():
        if actual != {key: manifest["files"][name][key] for key in ("bytes", "sha256")}:
            raise ValueError(f"{name}의 크기 또는 SHA256이 manifest와 다릅니다")
    return fingerprints


def verify_manifest_metadata(manifest: dict, raw: Path, reference_path: Path) -> None:
    """출처는 동봉 기록과 대조하고 실행 코드 지문의 필수 항목과 형식을 검사한다."""
    source = json.loads((raw / "source_manifest.json").read_text(encoding="utf-8"))
    kind = manifest["source"]["kind"]
    if kind not in ("listenbrainz", "synthetic_example"):
        raise ValueError("manifest의 데이터 출처 종류가 다릅니다")
    if reference_path.resolve() == inputs.REFERENCE.resolve() and kind != "listenbrainz":
        raise ValueError("고정 원본의 출처는 ListenBrainz여야 합니다")
    require_metadata(manifest["source"], {
        "url": source["source_url"], "snapshot_utc": source["source_snapshot_utc"],
        "provider": "MetaBrainz Foundation / ListenBrainz" if kind == "listenbrainz" else None,
        "license": "CC0-1.0", "license_evidence": "COPYING",
    }, "source")
    require_metadata(manifest["files"]["README.md"], {"format": "UTF-8 Markdown"}, "README.md")
    require_metadata(manifest["validation"], {"status": "passed"}, "validation")
    hashes = manifest["execution"]["source_and_config_sha256"]
    required = {"scripts/build_dataset.py", "scripts/preprocess_events.py", "scripts/audit_raw_data.py",
                "docs/dataset-package-readme.md", "requirements.txt", "input_reference", "local_config"}
    if (not isinstance(hashes, dict) or not required.issubset(hashes)
            or any(not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
                   for value in hashes.values())):
        raise ValueError("실행 코드·설정 SHA256의 필수 항목 또는 형식이 다릅니다")
    # 다른 환경에서의 재검증도 허용한다. 과거 실행 코드와 설정은 기록된 지문으로 추적한다.
    require_metadata(hashes, {
        "input_reference": sha256(reference_path),
        "docs/dataset-package-readme.md": manifest["files"]["README.md"]["sha256"],
    }, "source_and_config_sha256")


def validate_dataset(config: Path, package: Path, *, manifest_sha256: str | None = None,
                     reference_path: Path = inputs.REFERENCE, expected_users: int = 5000) -> dict:
    package = package.resolve()
    settings = inputs.read_settings(config)
    raw = settings["raw_dir"]
    original = inputs.verify_input_files(raw, inputs.read_reference(reference_path))
    manifest_path = package / "dataset_manifest.json"
    manifest_digest = sha256(manifest_path)
    if manifest_sha256 is not None and manifest_sha256 != manifest_digest:
        raise ValueError("manifest의 SHA256이 전달받은 값과 다릅니다")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest["dataset_version"] != package.name or manifest["input_files"] != original
            or (manifest["contract_version"], manifest["rules_version"]) != ("1.0.0", "1.0.0")):
        raise ValueError("manifest의 데이터 버전·입력·계약·규칙이 다릅니다")
    files = manifest["files"]
    before = package_fingerprints(package, manifest)
    verify_manifest_metadata(manifest, raw, reference_path)
    copying = next(entry for entry in original if entry["file"] == "COPYING")
    if before["COPYING"] != {key: copying[key] for key in ("bytes", "sha256")}:
        raise ValueError("패키지 COPYING이 원본과 다릅니다")
    (package / "README.md").read_text(encoding="utf-8")
    parquet = raw / "listens_8weeks_5000users.parquet"
    if pq.read_schema(parquet).field("listened_at").type != pa.timestamp("us"):
        raise ValueError("원본 시각의 정밀도 또는 시간대 표기가 고정 스키마와 다릅니다")
    with inputs.working_connection(settings) as con:
        input_validation = inputs.validate_input_tables(con, raw, expected_users)
        partitions, exclusions, train = build_expected_train(con, parquet)
        require_metadata(manifest["mapping"], {
            "start": 0, "dtype": "int32", "users": train["users"], "items": train["items"],
            "user_id_order": "숫자 오름차순", "item_key_order": "UTF-8 byte tuple 오름차순",
            "users_sha256": files["users.parquet"]["sha256"],
            "items_sha256": files["items.parquet"]["sha256"],
        }, "mapping")
        require_metadata(manifest["evaluation"], {
            "min_candidates": 10, "min_targets": 1,
            "user_exclusion_priority": ["no_train", "no_period_activity", "no_target",
                                       "fewer_than_10_candidates"],
        }, "evaluation")
        differences = {name: read_parquet(con, package, name, files[f"{name}.parquet"])
                       for name in ("users", "items", "train_interactions")}
        csr = read_csr(con, package / "train_counts.npz", train, files["train_counts.npz"])
        evaluation = {}
        for period in PERIODS:
            evaluation[period] = build_expected_evaluation(con, period, train["items"])
            targets = f"{period}_targets"
            differences[targets] = read_parquet(con, package, targets, files[f"{targets}.parquet"])
            differences[f"{period}_users"] = read_evaluation_users(
                con, package, period, files[f"{period}_users.csv"])

    clean_events = sum(p["events"] for p in partitions)
    raw_rows = input_validation["raw_rows"]
    expected_cleaning = {"raw_rows": raw_rows, "exclusions": exclusions, "clean_events": clean_events,
                         "partitions": partitions, "train": train,
                         "cohort_users_without_train": expected_users - train["users"]}
    processing = manifest["processing"]
    cleaning = processing["cleaning"]
    checks = {
        "inputs_unchanged": original == inputs.fingerprint_inputs(raw),
        "package_unchanged": before == package_fingerprints(package, manifest)
        and sha256(manifest_path) == manifest_digest,
        "event_accounting": raw_rows == clean_events + sum(exclusions.values()),
        "manifest_cleaning_matches": all(cleaning[key] == value for key, value in expected_cleaning.items()),
        "manifest_accounting_verified": cleaning["accounting_matches"] is True,
        "manifest_rules_match": processing["whitespace_codepoints"] == [f"U+{c:04X}" for c in WHITESPACE]
        and processing["deduplication_key"] == EVENT_KEY.split(", ")
        and processing["exclusion_priority"] == list(exclusions)
        and processing["train_fixed_for_both_periods"] is True
        and manifest["evaluation"]["min_candidates"] == 10
        and manifest["evaluation"]["min_targets"] == 1,
        "manifest_input_validation_matches": manifest["input_validation"] == input_validation,
        "manifest_evaluation_matches": manifest["evaluation"]["periods"] == evaluation,
        "parquet_matches": all(not any(differences[name].values()) for name in SCHEMAS),
        "csv_matches": all(not any(counts.values()) for period in PERIODS
                           for counts in differences[f"{period}_users"].values()),
        "csr_matches": csr["mismatching_ordered_entries"] == 0,
    }
    return {"matches": all(checks.values()), "dataset_version": manifest["dataset_version"],
            "manifest_sha256": manifest_digest, "checks": checks, "input_validation": input_validation,
            "cleaning": expected_cleaning, "evaluation": evaluation, "set_differences": differences,
            "csr": csr}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="원본과 임시 저장 위치를 지정한 로컬 설정")
    parser.add_argument("--package", type=Path, required=True, help="검증할 버전별 패키지 폴더")
    parser.add_argument("--manifest-sha256", help="별도로 받은 manifest의 SHA256")
    args = parser.parse_args()
    result = validate_dataset(args.config, args.package, manifest_sha256=args.manifest_sha256)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["matches"]:
        raise SystemExit("원본 기준과 저장된 패키지가 일치하지 않습니다")


if __name__ == "__main__":
    main()
