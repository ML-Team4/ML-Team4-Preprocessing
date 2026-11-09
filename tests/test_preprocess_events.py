"""정제 SQL을 독립 Python 기준과 대조하고 입력 오류·품질 신호 보존을 확인한다."""
import hashlib
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
import unicodedata
from unittest.mock import Mock, patch
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import preprocess_events as events


def fixture_connection(records, users=(1, 2), timezone="UTC"):
    con = duckdb.connect()
    con.execute("SET TimeZone = ?", [timezone])
    con.execute("""CREATE TABLE raw_input(user_id BIGINT, listened_at TIMESTAMP,
        artist_name VARCHAR, recording_name VARCHAR)""")
    if records:
        con.executemany("INSERT INTO raw_input VALUES(?,?,?,?)", records)
    con.execute("""CREATE TABLE selected_cohort(user_id BIGINT,
        train_unique_tracks BIGINT, user_index BIGINT)""")
    con.executemany("INSERT INTO selected_cohort VALUES(?,?,?)",
                    [(u, 10, i) for i, u in enumerate(users, 1)])
    return con


def python_reference(records):
    """SQL·SQL 집계 결과를 재사용하지 않고 원본 레코드에서 기대 사건을 계산한다."""
    start, train_end = datetime(2026, 8, 17), datetime(2026, 9, 28)
    validation_end, end = datetime(2026, 10, 5), datetime(2026, 10, 12)
    excluded = dict.fromkeys(events.EXCLUSION_REASONS, 0)
    grouped = defaultdict(list)
    for user, stamp, artist, title in records:
        if any(v is None for v in (user, stamp, artist, title)):
            excluded["missing_required"] += 1
            continue
        stamp = datetime.fromisoformat(stamp) if isinstance(stamp, str) else stamp
        if not start <= stamp < end:
            excluded["outside_window"] += 1
            continue
        a = " ".join(unicodedata.normalize("NFC", artist).split())
        r = " ".join(unicodedata.normalize("NFC", title).split())
        if not a or not r:
            excluded["empty_name"] += 1
            continue
        grouped[user, stamp, a, r].append((artist, title))
    expected, variants, pairs = [], defaultdict(Counter), Counter()
    for (user, stamp, a, r), originals in grouped.items():
        excluded["duplicate_excess"] += len(originals) - 1
        artist, title = min(originals, key=lambda p: (p[0].encode(), p[1].encode()))
        split = "train" if stamp < train_end else "validation" if stamp < validation_end else "test"
        expected.append((user, stamp, a, r, artist, title, split))
        if split == "train":
            variants[a, r][artist, title] += 1
            pairs[user, a, r] += 1
    display = []
    for (a, r), counts in variants.items():
        pair, count = min(counts.items(), key=lambda v: (-v[1], v[0][0].encode(), v[0][1].encode()))
        display.append((a, r, *pair, count))
    return sorted(expected), excluded, sorted(display), sorted((*k, v) for k, v in pairs.items())


def actual_events(con):
    return sorted(con.execute("SELECT * FROM clean_events").fetchall())


class EventCleaningTest(unittest.TestCase):
    def assert_matches_reference(self, con, records):
        expected, excluded, display, pairs = python_reference(records)
        result = events.build_clean_events(con)
        self.assertEqual(actual_events(con), expected)
        self.assertEqual(result["exclusions"], excluded)
        self.assertEqual(sorted(con.execute("SELECT * FROM train_display_names").fetchall()), display)
        self.assertEqual(sorted(con.execute("SELECT * FROM train_pairs").fetchall()), pairs)
        self.assertEqual(result["raw_rows"], sum(excluded.values()) + result["clean_events"])
        return result

    def test_utc_boundaries_precision_and_exclusive_exclusion_precedence(self):
        stamps = ["2026-08-17 00:00:00", "2026-09-27 23:59:59.999999",
                  "2026-09-28 00:00:00", "2026-10-04 23:59:59.999999",
                  "2026-10-05 00:00:00", "2026-10-11 23:59:59.999999"]
        records = [(1, t, "Artist", f"Track{i}") for i, t in enumerate(stamps)]
        records += [(None, "2026-10-12 00:00:00", "", ""), (1, None, "", ""),
                    (1, "2026-08-18 00:00:00", None, ""),
                    (1, "2026-08-16 23:59:59.999999", "", ""),
                    (1, "2026-10-12 00:00:00", "", ""),
                    (1, "2026-08-18 00:00:00", "\u3000\t", "Invalid")]
        for zone in ["UTC", "Asia/Seoul", "America/Los_Angeles"]:
            with self.subTest(timezone=zone):
                con = fixture_connection(records)
                self.addCleanup(con.close)
                con.execute("SET TimeZone = ?", [zone])
                result = self.assert_matches_reference(con, records)
                self.assertEqual(result["exclusions"], {"missing_required": 3, "outside_window": 2,
                                                      "empty_name": 1, "duplicate_excess": 0})
                self.assertEqual([p["events"] for p in result["partitions"]], [2, 2, 2])

    def test_partition_faults_cannot_be_counted_as_duplicates(self):
        records = [(1, "2026-08-17", "Artist", "Song"),
                   (1, "2026-09-28", "Artist", "Song"),
                   (1, "2026-10-05", "Artist", "Song")]
        for fault, message in [
            ("DELETE FROM clean_events WHERE split = 'validation'", "모든 정제 사건"),
            ("UPDATE clean_events SET split = NULL WHERE split = 'validation'", "모든 정제 사건"),
            ("UPDATE clean_events SET split = 'train' WHERE split = 'validation'", "train의 UTC 구간 밖"),
        ]:
            with self.subTest(fault=fault), fixture_connection(records) as con:
                # 중복 집계가 끝난 뒤 분할에만 오류를 주입한다.
                def execute_with_fault(sql, *params):
                    if sql.startswith("CREATE VIEW train_events AS"):
                        con.execute(fault)
                    return con.execute(sql, *params)

                wrapped = Mock(wraps=con)
                wrapped.execute.side_effect = execute_with_fault
                with self.assertRaisesRegex(RuntimeError, message):
                    events.build_clean_events(wrapped)

    def test_paired_originals_modal_display_and_row_order_invariance(self):
        first, second = (" A", "B "), ("A ", " B")
        records = [(1, "2026-08-17 00:00:00", *first)] * 7
        records += [(1, "2026-08-17 00:00:00", *second),
                    (1, "2026-08-18 00:00:00", *second),
                    (1, "2026-08-19 00:00:00", *second),
                    (1, "2026-08-20 00:00:00", " C", "D "),
                    (1, "2026-08-21 00:00:00", "C ", " D"),
                    (2, "2026-08-17 00:00:00", "e\u0301", "Song"),
                    (2, "2026-08-17 00:00:00", "é", "Song")]
        future = [(1, datetime(2026, 10, 5) + timedelta(seconds=i), *first) for i in range(20)]
        baseline = None
        for seed, future_records in [(0, []), (17, future), (43, list(reversed(future)))]:
            shuffled = records + future_records
            random.Random(seed).shuffle(shuffled)
            con = fixture_connection(shuffled)
            self.addCleanup(con.close)
            self.assert_matches_reference(con, shuffled)
            chosen = con.execute("""SELECT artist_name,recording_name FROM train_events
                WHERE artist_key='A' ORDER BY listened_at LIMIT 1""").fetchone()
            self.assertEqual(chosen, first)  # 독립 min이면 관측되지 않은 (' A', ' B')가 된다.
            display = sorted(con.execute("SELECT * FROM train_display_names").fetchall())
            self.assertIn(("A", "B", *second, 2), display)  # 원본 중복 7행은 빈도 7이 아니다.
            self.assertIn(("C", "D", " C", "D ", 1), display)  # 원문 쌍 빈도 동률
            current = (sorted(con.execute("SELECT * FROM train_pairs").fetchall()), display)
            if baseline is not None:
                self.assertEqual(current, baseline)
            baseline = current

    def test_tuple_keys_versions_repeats_and_simultaneous_tracks_are_preserved(self):
        stamp = "2026-08-17 00:00:00"
        records = [(1, stamp, a, r) for a, r in [
            ("a|b", "c"), ("a", "b|c"), ("Artist", "Song (Live)"),
            ("Artist", "Song (Remaster)"), ("Artist", "Song"), ("artist", "Song")]]
        records += [(1, "2026-08-18 00:00:00", "Artist", "Song"),
                    (2, stamp, " A\t\u3000B ", " Tune "),
                    (2, stamp, "A B", "Tune")]
        con = fixture_connection(records)
        self.addCleanup(con.close)
        result = self.assert_matches_reference(con, records)
        self.assertEqual(result["clean_events"], 8)
        self.assertEqual(result["exclusions"]["duplicate_excess"], 1)
        self.assertEqual(con.execute("SELECT listen_count FROM train_pairs WHERE artist_key='Artist' AND recording_key='Song'").fetchone(), (2,))

    def test_small_train_histories_and_future_only_cohort_are_not_resampled(self):
        records = [(1, "2026-08-17", "Artist", "Single"), (2, "2026-09-28", "Future", "Only")]
        con = fixture_connection(records)
        self.addCleanup(con.close)
        result = self.assert_matches_reference(con, records)
        self.assertEqual(result["train"]["users"], 1)
        self.assertEqual(result["cohort_users_without_train"], 1)
        self.assertEqual(con.execute("SELECT count(*) FROM selected_cohort").fetchone(), (2,))
        self.assertEqual(con.execute("SELECT count(*) FROM validation_events").fetchone(), (1,))

    def test_train_quality_flags_do_not_delete_events_or_use_future(self):
        records = [(u, "2026-08-17", "Artist", f"Track{i}") for u in (1, 2) for i in range(12)]
        records += [(3, datetime(2026, 8, 18) + timedelta(seconds=i), "Repeat", "Song") for i in range(1441)]
        records += [(4, "2026-08-19", "Signal", name) for name in ["\ufffd", "A\u200b", "B\x01"]]
        records += [(5, "2026-10-05", "Future", "\ufffd\u200b\x01")]
        con = fixture_connection(records, users=(1, 2, 3, 4, 5))
        self.addCleanup(con.close)
        self.assert_matches_reference(con, records)
        before = actual_events(con)
        result = events.quality_diagnostics(con)
        self.assertEqual(actual_events(con), before)
        self.assertEqual(result["name_signals"], {"affected_events": 3, "replacement_events": 1,
            "zero_width_or_bom_events": 1, "nonwhitespace_control_events": 1})
        self.assertEqual(result["timestamps"]["timestamps_ge10"], 2)
        self.assertEqual(result["timestamps"]["events_ge10"], 24)
        self.assertEqual(result["activity"]["active_days_over1440"], 1)
        self.assertEqual(result["profiles"]["identical_itemset_pairs"], 1)
        self.assertEqual(result["flagged_users"]["identical_itemset"], 2)
        self.assertEqual(result["flagged_users"]["abnormal_name"], 1)
        self.assertEqual(result["events_removed_by_flags"], 0)
        self.assertEqual(result["accounts_merged"], 0)

    def test_identical_small_profiles_are_diagnosed_below_ten_items(self):
        records = [(u, "2026-08-17", "Artist", "Song") for u in (1, 2, 3)]
        con = fixture_connection(records, users=(1, 2, 3))
        self.addCleanup(con.close)
        self.assert_matches_reference(con, records)
        quality = events.quality_diagnostics(con)
        self.assertEqual(quality["profiles"]["identical_itemset_pairs"], 3)
        self.assertEqual(quality["profiles"]["pairs_with_at_least10_shared_items"], 0)


class InputValidationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raw = Path(self.tmp.name) / "raw"
        self.raw.mkdir()
        with duckdb.connect() as con:
            con.execute("""COPY (SELECT user_id, TIMESTAMP '2026-08-17' AS listened_at,
                'Artist' AS artist_name, 'Song' AS recording_name FROM (VALUES(1::BIGINT),(2::BIGINT)) t(user_id))
                TO ? (FORMAT PARQUET)""", [str(self.raw / events.base.REQUIRED_FILES[0])])
        for name in events.base.REQUIRED_FILES[1:]:
            (self.raw / name).write_text("fixture", encoding="utf-8")
        self.csv = self.raw / "selected_users.csv"
        self.csv.write_text("user_id,train_unique_tracks,user_index\n1,999,1\n2,999,2\n")

    def write_input_reference(self):
        reference = {}
        lines = []
        for name in events.base.REQUIRED_FILES:
            data = (self.raw / name).read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            reference[name] = {"bytes": len(data), "sha256": digest}
            lines.extend([f'[files."{name}"]', f"bytes = {len(data)}", f'sha256 = "{digest}"', ""])
        config = Path(self.tmp.name) / "config"
        config.mkdir()
        reference_path = config / "raw-input-reference.toml"
        reference_path.write_text("\n".join(lines), encoding="utf-8")
        return reference_path, reference

    def test_six_hashes_and_sizes_match_fixed_reference_and_tampering_fails(self):
        reference_path, reference = self.write_input_reference()
        self.assertEqual(events.read_reference(reference_path), reference)
        self.assertEqual(len(events.verify_input_files(self.raw, reference)), 6)
        for name in events.base.REQUIRED_FILES:
            with self.subTest(file=name):
                path = self.raw / name
                original = path.read_bytes()
                path.write_bytes(bytes([original[0] ^ 1]) + original[1:])  # 크기는 같아도 거부
                with self.assertRaisesRegex(ValueError, "bytes/SHA256"):
                    events.verify_input_files(self.raw, reference)
                path.write_bytes(original)
        del reference["COPYING"]
        with self.assertRaisesRegex(ValueError, "6개"):
            events.verify_input_files(self.raw, reference)

    def test_cli_rejects_changed_input_even_when_audit_report_matches_it(self):
        root = Path(self.tmp.name)
        reference_path, _ = self.write_input_reference()
        baseline = reference_path.read_bytes()
        (self.raw / "README.txt").write_text("changed", encoding="utf-8")
        reports = root / "reports"
        reports.mkdir()
        lines = ["## 입력 파일 해시와 불변성", "", "| file | bytes | sha256 | unchanged |",
                 "| --- | --- | --- | --- |"]
        for entry in events.fingerprint_inputs(self.raw):
            lines.append(f'| "{entry["file"]}" | {entry["bytes"]} | "{entry["sha256"]}" | True |')
        (reports / "raw-data-audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        scripts = root / "scripts"
        scripts.mkdir()
        for module in (events, events.base):
            shutil.copy(module.__file__, scripts)
        cfg = root / "paths.local.toml"
        cfg.write_text('raw_dir="raw"\ncache_dir="cache"\n')
        result = subprocess.run(
            [sys.executable, str(scripts / "preprocess_events.py"), "--config", str(cfg), "--inputs-only"],
            cwd=root, capture_output=True, text=True, timeout=30,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("입력 bytes/SHA256이 고정 기준과 다릅니다: README.txt", result.stderr)
        self.assertEqual(reference_path.read_bytes(), baseline)

    def test_schema_cohort_size_unique_mapping_and_membership_are_strict(self):
        with duckdb.connect() as con:
            result = events.validate_input_tables(con, self.raw, expected_users=2)
            self.assertEqual(result["cohort"]["raw_users_not_in_cohort"], 0)
            self.assertEqual(result["raw_rows"], 2)
        with duckdb.connect() as con, self.assertRaisesRegex(ValueError, "지정된 크기"):
            events.validate_input_tables(con, self.raw)  # CLI 기준은 항상 5,000명
        original = self.csv.read_text()
        for text, message in [
            ("user_id,train_unique_tracks,user_index\n1,999,1\n2,999,1\n", "일대일"),
            ("user_id,train_unique_tracks,user_index\n1,999,1\n3,999,2\n", "사용자 집합"),
            ("user_index,train_unique_tracks,user_id\n1,999,1\n2,999,2\n", "header"),
        ]:
            self.csv.write_text(text)
            with duckdb.connect() as con, self.assertRaisesRegex(ValueError, message):
                events.validate_input_tables(con, self.raw, expected_users=2)
        self.csv.write_text(original)
        with duckdb.connect() as con:
            con.execute("""COPY (SELECT 1::INTEGER AS user_id, TIMESTAMP '2026-08-17' AS listened_at,
                'Artist' AS artist_name, 'Song' AS recording_name) TO ? (FORMAT PARQUET)""",
                [str(self.raw / events.base.REQUIRED_FILES[0])])
        with duckdb.connect() as con, self.assertRaisesRegex(ValueError, "schema"):
            events.validate_input_tables(con, self.raw, expected_users=2)

    def test_disk_workspace_cleans_up_after_exception_and_raw_cache_is_rejected(self):
        cfg = Path(self.tmp.name) / "paths.local.toml"
        cfg.write_text('raw_dir="raw"\ncache_dir="cache"\nmemory_limit="128MB"\nthreads=1\n')
        settings = events.read_settings(cfg)
        with self.assertRaisesRegex(RuntimeError, "테스트용 오류"):
            with events.working_connection(settings) as con:
                con.execute("CREATE TABLE fixture AS SELECT 1 AS value")
                raise RuntimeError("테스트용 오류")
        self.assertEqual(list(settings["cache_dir"].iterdir()), [])
        cfg.write_text('raw_dir="raw"\ncache_dir="raw/cache"\n')
        with self.assertRaisesRegex(ValueError, "raw_dir 밖"):
            events.read_settings(cfg)

    def test_cli_refuses_to_overwrite_existing_report_before_work_starts(self):
        cfg = Path(self.tmp.name) / "paths.local.toml"
        cfg.write_text('raw_dir="raw"\ncache_dir="cache"\n')
        report = Path(self.tmp.name) / "history.md"
        report.write_text("original report and historical hashes\n")
        before = report.read_bytes()
        with patch.object(sys, "argv", ["preprocess_events.py", "--config", str(cfg),
                                        "--report", str(report)]):
            with self.assertRaisesRegex(ValueError, "아직 존재하지 않는 .md"):
                events.main()
        self.assertEqual(report.read_bytes(), before)
        self.assertFalse((Path(self.tmp.name) / "cache").exists())


if __name__ == "__main__":
    unittest.main()
