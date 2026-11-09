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
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

import duckdb
import numpy as np
import pyarrow.parquet as pq
from scipy import sparse

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import build_dataset as build

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


def run_example(output_root, version, variant="original", *, rows=None):
    """별도 가상 원본·입력 기준을 사용하고 실제 패키지 생성 경로를 호출한다."""
    rows = fixture_rows(variant) if rows is None else rows
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
        return build.build_package(config, output_root, version, reference_path=reference,
                                   expected_users=len(cohort), example_script=Path(__file__).resolve(),
                                   example_variant=variant)


def pairs(path):
    return [(r["user_idx"], r["item_idx"]) for r in pq.read_table(path).to_pylist()]


class DatasetBuildTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="dataset-test-")
        self.addCleanup(directory.cleanup)
        self.output = Path(directory.name).resolve()
        # 여러 row group과 CSR 배치 경계, 동일 사용자 행의 배치 경계 통과도 확인한다.
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

        users = pq.read_table(package / "users.parquet").to_pylist()
        self.assertEqual(users, [{"user_idx": i, "user_id": user} for i, user in enumerate([10, 20, 30, 40, 60])])
        items = pq.read_table(package / "items.parquet").to_pylist()
        self.assertEqual([(r["item_idx"], r["artist_key"], r["recording_key"]) for r in items],
                         [(i, *key) for i, key in enumerate(KEYS)])
        self.assertEqual((items[0]["artist_name"], items[0]["recording_name"]), (" Cafe\u0301", "t00"))
        self.assertEqual((items[1]["artist_name"], items[1]["recording_name"]), ("Café ", "t01"))
        self.assertEqual([r["train_listener_count"] for r in items], [1, 2, 2, 2, 2, 1, 1, 1, 1, 1, 1])
        expected = [(0, 0, 2), (1, 1, 1), (1, 2, 1), (2, 3, 1), (3, 4, 1), (4, 1, 3)]
        expected += [(4, i, 1) for i in range(2, 11)]
        interactions = pq.read_table(package / "train_interactions.parquet")
        self.assertEqual(list(zip(*(col.to_pylist() for col in interactions.columns))), expected)
        matrix = sparse.load_npz(package / "train_counts.npz")
        self.assertEqual((matrix.shape, matrix.nnz, int(matrix.sum())), ((5, 11), 15, 18))
        self.assertEqual([matrix.shape[1] - matrix.getrow(i).nnz for i in (0, 1)], [10, 9])
        self.assertEqual(pairs(package / "validation_targets.parquet"), [(0, 1)])
        self.assertEqual(pairs(package / "test_targets.parquet"), [(0, 1), (0, 2), (3, 3)])
        self.assertEqual((package / "validation_users.csv").read_text(), "user_idx\n0\n")
        self.assertEqual((package / "test_users.csv").read_text(), "user_idx\n0\n3\n")

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
        self.assertIn("baseline (5, 11) 15", result.stdout)
        self.assertIn("test 2 targets for the first evaluation user", result.stdout)

    def test_row_order_and_future_activity_invariance(self):
        baseline = run_example(self.output, "original")
        shuffled = run_example(self.output, "shuffled", "shuffled")
        future = run_example(self.output, "future", "future")
        for name in ("users", "items", "train_interactions"):
            table = pq.read_table(baseline / f"{name}.parquet")
            for variant in (shuffled, future):
                self.assertTrue(table.equals(pq.read_table(variant / f"{name}.parquet")))
        matrix = sparse.load_npz(baseline / "train_counts.npz")
        for variant in (shuffled, future):
            other = sparse.load_npz(variant / "train_counts.npz")
            self.assertEqual(matrix.shape, other.shape)
            for attribute in ("indptr", "indices", "data"):
                np.testing.assert_array_equal(getattr(matrix, attribute), getattr(other, attribute))
        for period in build.PERIODS:
            self.assertEqual(pairs(baseline / f"{period}_targets.parquet"),
                             pairs(shuffled / f"{period}_targets.parquet"))
            self.assertEqual((baseline / f"{period}_users.csv").read_bytes(),
                             (shuffled / f"{period}_users.csv").read_bytes())
        self.assertEqual(pairs(future / "validation_targets.parquet"), [(0, 3)])
        self.assertEqual(pairs(future / "test_targets.parquet"), [])

    def test_validation_failure_cleans_staging_and_reuse_preserves_version(self):
        version = "guarded"
        write_csr = build.write_csr

        def corrupt_counts(staging, train):
            write_csr(staging, train)
            path = staging / "train_counts.npz"
            matrix = sparse.load_npz(path)
            matrix.data[0] -= 1
            matrix.data[1] += 1  # nnz, shape, 양수, 합계는 같아도 좌표별 값 검증에서 거부
            sparse.save_npz(path, matrix)

        with patch.object(build, "write_csr", side_effect=corrupt_counts):
            with self.assertRaisesRegex(RuntimeError, "coordinates/counts differ"):
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
        matrix = sparse.load_npz(package / "train_counts.npz")
        self.assertEqual((matrix.shape, matrix.nnz), ((0, 0), 0))
        for period in build.PERIODS:
            self.assertEqual(pairs(package / f"{period}_targets.parquet"), [])
            self.assertEqual((package / f"{period}_users.csv").read_text(), "user_idx\n")

    def test_retry_same_version_after_process_exits_during_write(self):
        # 자식 프로세스를 정리 구문 실행 전에 종료해 부분 저장과 재시도를 확인한다.
        child = """
import os, sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, 'tests')
from test_build_dataset import build, run_example
with patch.object(build, 'write_csr', side_effect=lambda *_: os._exit(73)):
    run_example(Path(sys.argv[1]), 'retry')
"""
        destination = self.output / "packages"
        stopped = subprocess.run([sys.executable, "-c", child, str(destination)], cwd=REPO,
                                 env={**os.environ, "TMPDIR": str(self.output)}, timeout=30)
        self.assertEqual(stopped.returncode, 73)
        self.assertFalse((destination / "retry").exists())
        package = run_example(destination, "retry")
        self.assertEqual(pairs(package / "validation_targets.parquet"), [(0, 1)])

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
        self.assertEqual(pairs(package / "test_targets.parquet"), [(0, 1), (0, 2), (3, 3)])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--variant", choices=VARIANTS, default="original")
    args = parser.parse_args()
    final = run_example(args.output_root, args.version, args.variant)
    print(f"{final}\ndataset_manifest.json SHA256 {build.base.sha256(final / 'dataset_manifest.json')}")
