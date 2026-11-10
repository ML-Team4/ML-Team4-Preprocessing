"""합성 원본부터 패키지 소비까지 검사하고 로컬 검토용 예제를 재현한다."""
import argparse
import csv
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.csv as arrow_csv
import pyarrow.parquet as pq
from scipy import sparse

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import build_dataset as build
import validate_dataset as independent

ARTIST = "Café"
TRAIN = "2026-08-17 00:00:00"
VALIDATION = "2026-09-28 00:00:00"
TEST = "2026-10-05 00:00:00"
VARIANTS = ("original", "shuffled", "future")
KEYS = [(ARTIST, f"t{i:02d}") for i in range(9)] + [(ARTIST, "t08 (Live)"), ("Élan", "t08")]


def fixture_rows(variant):
    # 11곡. 10번은 1곡, 20번은 2곡을 학습해 각각 후보 10곡, 9곡이다.
    rows = [
        (10, TRAIN, " Cafe\u0301", "t00"),
        (10, TRAIN, ARTIST, " t00"),  # 같은 사건에서 원문 이름 쌍을 함께 선택
        (10, "2026-09-27 23:59:59.999999", ARTIST, " t00"),  # 대표 원문 동률
        (20, TRAIN, "Café ", "t01"),
        (20, TRAIN, ARTIST, "t02"),
        (30, TRAIN, ARTIST, "t03"),
        (40, TRAIN, ARTIST, "t04"),
        *((60, TRAIN, artist, title) for artist, title in KEYS[1:]),
        (60, "2026-08-18", "Café ", "t01"),
        (60, "2026-08-19", "Café ", "t01"),  # t01의 다빈도 원문, 인기도는 여전히 2
    ]
    original = [
        (10, VALIDATION, ARTIST, "t01"),
        (10, "2026-10-04 23:59:59.999999", ARTIST, "t01"),  # 기간 내 반복
        (10, VALIDATION, ARTIST, "t00"),  # 기청취곡
        (10, VALIDATION, ARTIST, "new"),
        (10, "2026-09-29", ARTIST, "new"),
        (20, VALIDATION, ARTIST, "t00"),  # 후보 9개, 정답이 있어도 제외
        (30, VALIDATION, ARTIST, "t03"),  # 기청취곡만 있음
        (30, VALIDATION, ARTIST, "new"),
        (50, VALIDATION, ARTIST, "t00"),  # train 이력이 없는 사용자
        (10, TEST, ARTIST, "t01"),  # validation 정답의 재등장
        (10, "2026-10-11 23:59:59.999999", ARTIST, "t02"),
        (20, TEST, ARTIST, "t00"),
        (40, TEST, ARTIST, "t03"),  # test에만 포함되는 사용자
        (50, TEST, ARTIST, "new"),
        (60, "2026-10-12 00:00:00", ARTIST, "t00"),  # 종료 미포함
    ]
    if variant == "future":
        rows += [(10, VALIDATION, "Café ", "t03"), (50, TEST, ARTIST, "t01")]
        rows += [(10, f"2026-10-0{i}", ARTIST, "t00") for i in range(1, 5)]
    else:
        rows += original
    if variant == "shuffled":
        random.Random(42).shuffle(rows)
    return rows


@contextmanager
def example_input(rows):
    """합성 원본과 입력 기준을 사용하는 동안 임시 폴더를 보존한다."""
    cohort = sorted({user_id for user_id, *_ in rows})
    with tempfile.TemporaryDirectory(prefix="dataset-input-") as folder:
        fixture = Path(folder)
        raw = fixture / "raw"
        raw.mkdir()
        with duckdb.connect() as con:
            con.execute("""CREATE TABLE input(user_id BIGINT, listened_at TIMESTAMP,
                artist_name VARCHAR, recording_name VARCHAR)""")
            con.executemany("INSERT INTO input VALUES (?, ?, ?, ?)", rows)
            path = build.base.sql_literal(str(raw / build.base.REQUIRED_FILES[0]))
            con.execute(f"COPY input TO {path} (FORMAT PARQUET)")
        with (raw / "selected_users.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(("user_id", "train_unique_tracks", "user_index"))
            writer.writerows((user_id, 0, idx) for idx, user_id in enumerate(cohort, 1))
        (raw / "README.txt").write_text("Synthetic package regression example.\n", encoding="utf-8")
        (raw / "source_manifest.json").write_text(
            '{"source_url": null, "source_snapshot_utc": null}\n', encoding="utf-8")
        (raw / "verified_8week_summary.json").write_text("{}\n", encoding="utf-8")
        (raw / "COPYING").write_text("Synthetic example: CC0 1.0 Universal\n", encoding="utf-8")
        reference = fixture / "reference.toml"
        reference.write_text("\n".join(
            f'[files."{r["file"]}"]\nbytes = {r["bytes"]}\nsha256 = "{r["sha256"]}"\n'
            for r in build.common.fingerprint_inputs(raw)), encoding="utf-8")
        config = fixture / "config.toml"
        config.write_text('raw_dir = "raw"\ncache_dir = "cache"\n', encoding="utf-8")
        yield {"config": config, "reference_path": reference, "expected_users": len(cohort)}


def run_example(output_root, version, variant="original", *, rows=None):
    """합성 원본으로 실제 패키지 생성 경로를 호출한다."""
    with example_input(fixture_rows(variant) if rows is None else rows) as source:
        return build.build_package(output_root=output_root, version=version, **source,
                                   example_script=Path(__file__).resolve(), example_variant=variant)


def read_table(path):
    fields, _ = independent.SCHEMAS[path.stem]
    return arrow_csv.read_csv(
        path, parse_options=arrow_csv.ParseOptions(newlines_in_values=True),
        convert_options=arrow_csv.ConvertOptions(column_types=dict(fields),
                                                null_values=[], strings_can_be_null=False))


def matrix_from_csv(package):
    manifest = json.loads((package / "dataset_manifest.json").read_text())
    data = read_table(package / "train_interactions.csv")
    return sparse.csr_matrix((data["listen_count"].to_numpy(),
        (data["user_idx"].to_numpy(), data["item_idx"].to_numpy())),
        shape=(manifest["mapping"]["users"], manifest["mapping"]["items"]))


def pairs(path):
    return [(r["user_idx"], r["item_idx"]) for r in read_table(path).to_pylist()]


def convert_to_legacy_package(package):
    """과거 계약 패키지의 재검증 지원을 검사할 입력을 만든다."""
    manifest_path = package / "dataset_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    matrix = matrix_from_csv(package)
    for name, (fields, _) in independent.SCHEMAS.items():
        path = package / f"{name}.parquet"
        table = read_table(package / f"{name}.csv")
        schema = pa.schema([pa.field(key, dtype, nullable=False) for key, dtype in fields])
        pq.write_table(table.cast(schema), path, compression="zstd")
        entry = manifest["files"].pop(f"{name}.csv")
        for key in ("format", "encoding", "header"):
            entry.pop(key)
        entry.update(build.fingerprint(path), compression="zstd")
        manifest["files"][path.name] = entry
        (package / f"{name}.csv").unlink()
    for period in build.PERIODS:
        path = package / f"{period}_users.csv"
        text = path.read_text(encoding="utf-8-sig")
        path.write_text(text, encoding="utf-8")
        manifest["files"][path.name].update(build.fingerprint(path), encoding="UTF-8")
    path = package / "train_counts.npz"
    sparse.save_npz(path, matrix)
    manifest["files"][path.name] = {**build.fingerprint(path), "format": "csr",
        "axes": ["user_idx", "item_idx"], "shape": list(matrix.shape), "nnz": matrix.nnz,
        "sum_counts": int(matrix.sum()), "canonical": True,
        "matches_interactions_all_coordinates_and_values": True,
        **{f"{key}_dtype": str(getattr(matrix, key).dtype) for key in ("data", "indices", "indptr")}}
    manifest["contract_version"] = "1.0.0"
    for name in ("users", "items"):
        manifest["mapping"][f"{name}_sha256"] = manifest["files"][f"{name}.parquet"]["sha256"]
    manifest_path.write_text(json.dumps(manifest))


class DatasetBuildTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="dataset-test-")
        self.addCleanup(directory.cleanup)
        self.output = Path(directory.name).resolve()
        # CSV 배치 경계와 동일 사용자 행의 배치 경계 통과를 확인한다.
        batches = patch.object(build, "BATCH_ROWS", 4)
        batches.start()
        self.addCleanup(batches.stop)

    def test_example_roundtrip_and_exclusion_accounting(self):
        package = run_example(self.output, "baseline")
        manifest = json.loads((package / "dataset_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["source"]["kind"], "synthetic_example")
        self.assertEqual(set(p.name for p in package.iterdir()), {*manifest["files"], "dataset_manifest.json"})
        for name, entry in manifest["files"].items():
            self.assertEqual(build.fingerprint(package / name),
                             {key: entry[key] for key in ("bytes", "sha256")})
        cleaning = manifest["processing"]["cleaning"]
        self.assertEqual(cleaning["raw_rows"], 34)
        self.assertEqual(cleaning["exclusions"],
                         {"missing_required": 0, "outside_window": 1, "empty_name": 0, "duplicate_excess": 1})
        self.assertEqual([p["events"] for p in cleaning["partitions"]], [18, 9, 5])

        users = read_table(package / "users.csv").to_pylist()
        self.assertEqual(users, [{"user_idx": i, "user_id": user} for i, user in enumerate([10, 20, 30, 40, 60])])
        items = read_table(package / "items.csv").to_pylist()
        self.assertEqual([(r["item_idx"], r["artist_key"], r["recording_key"]) for r in items],
                         [(i, *key) for i, key in enumerate(KEYS)])
        self.assertEqual((items[0]["artist_name"], items[0]["recording_name"]), (" Cafe\u0301", "t00"))
        self.assertEqual((items[1]["artist_name"], items[1]["recording_name"]), ("Café ", "t01"))
        self.assertEqual([r["train_listener_count"] for r in items], [1, 2, 2, 2, 2, 1, 1, 1, 1, 1, 1])
        expected = [(0, 0, 2), (1, 1, 1), (1, 2, 1), (2, 3, 1), (3, 4, 1), (4, 1, 3)]
        expected += [(4, i, 1) for i in range(2, 11)]
        interactions = read_table(package / "train_interactions.csv")
        self.assertEqual(list(zip(*(col.to_pylist() for col in interactions.columns))), expected)
        matrix = matrix_from_csv(package)
        self.assertEqual((matrix.shape, matrix.nnz, int(matrix.sum())), ((5, 11), 15, 18))
        self.assertEqual([matrix.shape[1] - matrix.getrow(i).nnz for i in (0, 1)], [10, 9])
        self.assertEqual(pairs(package / "validation_targets.csv"), [(0, 1)])
        self.assertEqual(pairs(package / "test_targets.csv"), [(0, 1), (0, 2), (3, 3)])
        self.assertEqual((package / "validation_users.csv").read_text(encoding="utf-8-sig"), "user_idx\n0\n")
        self.assertEqual((package / "test_users.csv").read_text(encoding="utf-8-sig"), "user_idx\n0\n3\n")

        validation, test = (manifest["evaluation"]["periods"][period] for period in build.PERIODS)
        self.assertEqual(validation["user_exclusions"],
                         {"no_train": 1, "no_period_activity": 2, "no_target": 1, "fewer_than_10_candidates": 1})
        self.assertEqual(test["user_exclusions"],
                         {"no_train": 1, "no_period_activity": 2, "no_target": 0, "fewer_than_10_candidates": 1})
        for kind, values in {"new_item": (3, 1, 2, 2), "train_seen": (2, 2, 2, 2),
                             "known_item_no_train_user": (1, 1, 1, 1), "candidate": (3, 2, 2, 2)}.items():
            self.assertEqual(validation["period_pairs_by_kind"][kind],
                             dict(zip(("events", "unique_items", "user_item_pairs", "users"), values)))
        self.assertEqual(validation["candidate_repeat_events_collapsed"], 1)
        self.assertEqual(validation["candidate_pairs_excluded_for_fewer_than_10_candidates"], 1)
        self.assertEqual(validation["user_denominator_selected_cohort"], 6)

        consumer_code = (package / "README.md").read_text().split("```python\n", 1)[1].split("```", 1)[0]
        result = subprocess.run([sys.executable, "-c", consumer_code], cwd=package,
                                check=True, capture_output=True, text=True)
        first_item = read_table(package / "items.csv").slice(0, 1).to_pylist()[0]
        self.assertEqual(result.stdout.strip(), f"0 {first_item['recording_name']} "
                         f"{first_item['artist_name']} {first_item['train_listener_count']}")

    def test_csv_names_and_large_integer_roundtrip(self):
        names = ["쉼표,가수", '따옴표"가수', "개행\n가수", "NA", "NULL", "null", "곡\r\n제목"]
        rows = [(9223372036854775806, TRAIN, name, name) for name in names]
        with example_input(rows) as source:
            package = build.build_package(output_root=self.output, version="csv-names", **source)
            items = read_table(package / "items.csv").to_pylist()
            self.assertEqual({row["artist_name"] for row in items}, set(names))
            self.assertEqual({row["recording_name"] for row in items}, set(names))
            self.assertEqual(read_table(package / "users.csv")["user_id"].to_pylist(),
                             [9223372036854775806])
            self.assertEqual(len(list(package.glob("*.csv"))), 7)
            self.assertEqual(list(package.glob("*.parquet")), [])
            self.assertEqual(list(package.glob("*.npz")), [])
            self.assertTrue(independent.validate_dataset(package=package, **source)["matches"])
            manifest_path = package / "dataset_manifest.json"
            original_manifest = manifest_path.read_text()
            for name in ("train_interactions.csv", "validation_users.csv"):
                with self.subTest(missing_bom=name):
                    path = package / name
                    contents = path.read_bytes()
                    path.write_bytes(contents[3:])
                    manifest = json.loads(original_manifest)
                    manifest["files"][name].update(build.fingerprint(path))
                    manifest_path.write_text(json.dumps(manifest))
                    with self.assertRaisesRegex(ValueError, "UTF-8 BOM"):
                        independent.validate_dataset(package=package, **source)
                    path.write_bytes(contents)
                    manifest_path.write_text(original_manifest)

    def test_row_order_and_future_activity_invariance(self):
        baseline = run_example(self.output, "original")
        shuffled = run_example(self.output, "shuffled", "shuffled")
        future = run_example(self.output, "future", "future")
        for name in ("users", "items", "train_interactions"):
            table = read_table(baseline / f"{name}.csv")
            for variant in (shuffled, future):
                self.assertTrue(table.equals(read_table(variant / f"{name}.csv")))
        matrix = matrix_from_csv(baseline)
        for variant in (shuffled, future):
            other = matrix_from_csv(variant)
            self.assertEqual(matrix.shape, other.shape)
            for attribute in ("indptr", "indices", "data"):
                np.testing.assert_array_equal(getattr(matrix, attribute), getattr(other, attribute))
        for period in build.PERIODS:
            self.assertEqual(pairs(baseline / f"{period}_targets.csv"),
                             pairs(shuffled / f"{period}_targets.csv"))
            self.assertEqual((baseline / f"{period}_users.csv").read_bytes(),
                             (shuffled / f"{period}_users.csv").read_bytes())
        self.assertEqual(pairs(future / "validation_targets.csv"), [(0, 3)])
        self.assertEqual(pairs(future / "test_targets.csv"), [])

    def test_independent_validation_rejects_changed_contents_with_updated_hashes(self):
        with example_input(fixture_rows("original")) as source:
            package = build.build_package(output_root=self.output, version="verified", **source)
            result = independent.validate_dataset(package=package, **source)
            self.assertTrue(result["matches"], result)
            self.assertEqual(result["evaluation"]["validation"]["target_pairs"], 1)
            self.assertEqual(result["evaluation"]["test"]["target_pairs"], 3)

            # 규모와 파일 해시 검사가 통과해도 같은 값의 다른 좌표와 같은 합계의 다른 횟수를 잡아낸다.
            targets = package / "validation_targets.csv"
            original = read_table(targets)
            with targets.open("wb") as stream:
                stream.write(b"\xef\xbb\xbf")
                arrow_csv.write_csv(pa.Table.from_arrays(
                    [original["user_idx"], pa.array([2], type=pa.int32())],
                    schema=original.schema), stream)
            users = package / "test_users.csv"
            users.write_text("user_idx\n1\n3\n", encoding="utf-8-sig")
            counts = package / "train_interactions.csv"
            table = read_table(counts)
            values = table["listen_count"].to_pylist()
            values[0] -= 1
            values[1] += 1
            table = table.set_column(2, "listen_count", pa.array(values, type=pa.int64()))
            with counts.open("wb") as stream:
                stream.write(b"\xef\xbb\xbf")
                arrow_csv.write_csv(table, stream)
            manifest_path = package / "dataset_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for path in (targets, users, counts):
                manifest["files"][path.name].update(build.fingerprint(path))
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            changed = independent.validate_dataset(package=package, **source)
            self.assertFalse(changed["matches"])
            self.assertEqual(changed["set_differences"]["validation_targets"], {"missing": 1, "extra": 1})
            self.assertEqual(changed["set_differences"]["test_users"], {
                "expected_users": {"missing": 1, "extra": 1},
                "stored_target_users": {"missing": 1, "extra": 1},
            })
            self.assertEqual(changed["set_differences"]["train_interactions"], {"missing": 2, "extra": 2})

    def test_independent_validation_rejects_manifest_metadata_changes(self):
        changes = [
            (("mapping", "start"), 1), (("mapping", "users"), 0), (("mapping", "items"), 0),
            (("mapping", "dtype"), "int64"), (("mapping", "users_sha256"), "0" * 64),
            (("mapping", "items_sha256"), "0" * 64),
            (("files", "items.csv", "column_order"), ["item_idx"]),
            (("files", "items.csv", "columns"), {"item_idx": "string"}),
            (("files", "items.csv", "sorted_by"), ["artist_key"]),
            (("files", "items.csv", "nullable"), True),
            (("files", "items.csv", "encoding"), "UTF-16"),
            (("files", "items.csv", "format"), "parquet"),
            (("files", "test_users.csv", "columns"), {"user_idx": "string"}),
            (("files", "test_users.csv", "header"), False),
            (("files", "test_users.csv", "encoding"), "UTF-16"),
            (("source", "license"), "unsupported"),
            (("source", "snapshot_utc"), "2000-01-01"),
            (("execution", "source_and_config_sha256"), {}),
            (("execution", "source_and_config_sha256", "scripts/build_dataset.py"), "invalid"),
            (("execution", "source_and_config_sha256", "input_reference"), "0" * 64),
            (("evaluation", "user_exclusion_priority"), []),
        ]
        with example_input(fixture_rows("original")) as source:
            package = build.build_package(output_root=self.output, version="metadata", **source)
            path = package / "dataset_manifest.json"
            original = path.read_text(encoding="utf-8")
            for keys, value in changes:
                with self.subTest(field="/".join(keys)):
                    manifest = json.loads(original)
                    target = manifest
                    for key in keys[:-1]:
                        target = target[key]
                    target[keys[-1]] = value
                    path.write_text(json.dumps(manifest), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        independent.validate_dataset(package=package, **source)
            path.write_text(original, encoding="utf-8")
            self.assertTrue(independent.validate_dataset(package=package, **source)["matches"])

    def test_independent_validation_checks_npz_arrays_before_scipy(self):
        with example_input(fixture_rows("original")) as source:
            package = build.build_package(output_root=self.output, version="csr-structure", **source)
            convert_to_legacy_package(package)
            path = package / "train_counts.npz"
            with np.load(path, allow_pickle=False) as stored:
                original = {name: stored[name] for name in stored.files}
            manifest_path = package / "dataset_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for name in ("data", "indices", "indptr"):
                with self.subTest(array=name):
                    changed = dict(original)
                    changed[name] = np.append(original[name], np.array([0], dtype=original[name].dtype))
                    np.savez_compressed(path, **changed)
                    manifest["files"][path.name].update(build.fingerprint(path))
                    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                    with patch.object(independent.sparse, "load_npz") as loader:
                        with self.assertRaisesRegex(ValueError, "배열 차원 또는 길이"):
                            independent.validate_dataset(package=package, **source)
                        loader.assert_not_called()

            # 계약에서 허용한 int64 구조 인덱스도 소비자 API의 자료형 변환과 무관하게 검증한다.
            original["indices"] = original["indices"].astype(np.int64)
            original["indptr"] = original["indptr"].astype(np.int64)
            np.savez_compressed(path, **original)
            manifest["files"][path.name].update(build.fingerprint(path))
            manifest["files"][path.name].update(indices_dtype="int64", indptr_dtype="int64")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            self.assertTrue(independent.validate_dataset(package=package, **source)["matches"])

    def test_independent_validation_accepts_empty_train_and_evaluation(self):
        for name, rows in (
                ("empty-train", [(10, TEST, ARTIST, "future only")]),
                ("empty-evaluation", [(10, TRAIN, ARTIST, "train only")])):
            with self.subTest(name=name), example_input(rows) as source:
                package = build.build_package(output_root=self.output, version=name, **source)
                self.assertTrue(independent.validate_dataset(package=package, **source)["matches"])

    def test_validation_failure_cleans_staging_and_reuse_preserves_version(self):
        version = "guarded"
        write_csv = build.write_csv

        def corrupt_counts(con, staging):
            write_csv(con, staging)
            path = staging / "train_interactions.csv"
            table = read_table(path)
            values = table["listen_count"].to_pylist()
            values[0] -= 1
            values[1] += 1
            table = table.set_column(2, "listen_count", pa.array(values, type=pa.int64()))
            with path.open("wb") as stream:
                stream.write(b"\xef\xbb\xbf")
                arrow_csv.write_csv(table, stream)

        with patch.object(build, "write_csv", side_effect=corrupt_counts):
            with self.assertRaisesRegex(RuntimeError, "행 내용 또는 순서"):
                run_example(self.output, version)
        self.assertEqual(list(self.output.iterdir()), [])
        package = run_example(self.output, version)
        before = build.fingerprint(package / "dataset_manifest.json")
        with self.assertRaises(FileExistsError):
            run_example(self.output, version)
        self.assertEqual(build.fingerprint(package / "dataset_manifest.json"), before)
        self.assertEqual(list(self.output.iterdir()), [package])

    def test_empty_train_and_header_only_evaluation(self):
        package = run_example(self.output, "empty-train", rows=[(10, TEST, ARTIST, "future only")])
        matrix = matrix_from_csv(package)
        self.assertEqual((matrix.shape, matrix.nnz), ((0, 0), 0))
        for period in build.PERIODS:
            self.assertEqual(pairs(package / f"{period}_targets.csv"), [])
            self.assertEqual((package / f"{period}_users.csv").read_text(encoding="utf-8-sig"), "user_idx\n")

    def test_retry_same_version_after_process_exits_during_write(self):
        # 자식 프로세스를 정리 구문 실행 전에 종료해 부분 저장과 재시도를 확인한다.
        child = """
import os, sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, 'tests')
from test_build_dataset import build, run_example
with patch.object(build, 'write_csv', side_effect=lambda *_: os._exit(73)):
    run_example(Path(sys.argv[1]), 'retry')
"""
        destination = self.output / "packages"
        stopped = subprocess.run([sys.executable, "-c", child, str(destination)], cwd=REPO,
                                 env={**os.environ, "TMPDIR": str(self.output)}, timeout=30)
        self.assertEqual(stopped.returncode, 73)
        self.assertFalse((destination / "retry").exists())
        package = run_example(destination, "retry")
        self.assertEqual(pairs(package / "validation_targets.csv"), [(0, 1)])

    def test_concurrent_builds_publish_only_one_package(self):
        ready = Barrier(2, timeout=30)
        rename = Path.rename

        def simultaneous_rename(staging, final):
            ready.wait()
            return rename(staging, final)

        with patch.object(Path, "rename", simultaneous_rename), ThreadPoolExecutor(2) as workers:
            jobs = [workers.submit(run_example, self.output, "concurrent") for _ in range(2)]
            errors = [job.exception() for job in jobs]
        self.assertEqual(errors.count(None), 1)
        self.assertEqual(sum(isinstance(error, FileExistsError) for error in errors), 1)
        package = self.output / "concurrent"
        self.assertEqual(list(self.output.iterdir()), [package])
        self.assertEqual(pairs(package / "test_targets.csv"), [(0, 1), (0, 2), (3, 3)])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--variant", choices=VARIANTS, default="original")
    args = parser.parse_args()
    final = run_example(args.output_root, args.version, args.variant)
    print(f"{final}\ndataset_manifest.json SHA256 {build.base.sha256(final / 'dataset_manifest.json')}")
