"""반복 청취 분모·미래 정보 분리·평가 후보와 무결성 오류를 검사한다."""
import copy
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import analyze_listening as eda
import verify_listening_analysis as verification
from test_build_dataset import fixture_rows, run_example


class ListeningAnalysisTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix="listening-analysis-")
        cls.addClassCleanup(cls.folder.cleanup)
        cls.package = run_example(Path(cls.folder.name), eda.VERSION)
        cls.all_train_package = run_example(Path(cls.folder.name) / "all-train", eda.VERSION,
            rows=[row for row in fixture_rows("original") if row[0] != 50])

    def setUp(self):
        reference = self.reference_for(self.package)
        patcher = patch.object(eda, "REFERENCE", reference)
        patcher.start()
        self.addCleanup(patcher.stop)

    def reference_for(self, package):
        path = Path(self.folder.name) / (package.parent.name + "-reference.json")
        path.write_text(json.dumps({"dataset_version": eda.VERSION, "matches": True,
            "checks": {"synthetic_fixture": True},
            "manifest_sha256": eda.sha256(package / "dataset_manifest.json")}))
        return path

    def test_repeat_events_and_unique_listener_denominators(self):
        manifest, data, _ = eda.load_package(self.package)
        stats, tables, tracks, events = eda.aggregate_train(data, manifest)
        self.assertEqual((stats["users"], stats["items"], stats["user_item_pairs"], stats["events"]), (5, 11, 15, 18))
        self.assertEqual([(r["tracks"], r["events"]) for r in tables["popularity_bands"]], [(7, 8), (4, 10), (0, 0)])
        self.assertAlmostEqual(tables["popularity_bands"][0]["track_share_pct"], 100 * 7 / 11)
        self.assertAlmostEqual(tables["popularity_bands"][0]["event_share_pct"], 100 * 8 / 18)
        self.assertEqual(sorted(tracks), [1, 1, 1, 2, 10])
        self.assertEqual(sorted(events), [1, 1, 2, 2, 12])
        self.assertAlmostEqual(stats["matrix_density"], 15 / (5 * 11))
        data["validation_targets"]["item_idx"][:] = 999
        self.assertEqual(eda.aggregate_train(data, manifest)[0], stats)
        data["items"]["train_listener_count"][0] += 1
        with self.assertRaisesRegex(ValueError, "고유 청취자"):
            eda.aggregate_train(data, manifest)

    def test_targets_and_manifest_exclusions(self):
        manifest, data, _ = eda.load_package(self.package)
        _, _, tracks, _ = eda.aggregate_train(data, manifest)
        evaluation, summaries, excluded, bands = eda.aggregate_evaluation(data, manifest, tracks)
        self.assertEqual(evaluation["validation"]["eligible_users"], 1)
        self.assertEqual(evaluation["test"]["target_pairs"], 3)
        self.assertEqual(summaries[0]["median"], 1)
        self.assertEqual(sum(r["users"] for r in bands if r["period"] == "test"), 2)
        self.assertEqual(next(r for r in excluded if r["period"] == "validation" and r["kind"] == "new_item")["events"], 3)
        changed = copy.deepcopy(data)
        changed["validation_targets"]["item_idx"][0] = 0
        with self.assertRaisesRegex(ValueError, "기청취곡"):
            eda.aggregate_evaluation(changed, manifest, tracks)
        changed = copy.deepcopy(data)
        changed["test_users"]["user_idx"] = np.array([0])
        with self.assertRaisesRegex(ValueError, "평가 사용자"):
            eda.aggregate_evaluation(changed, manifest, tracks)

    def test_three_listener_band_and_exact_large_integer_events(self):
        count = 2**53 + 1
        data = {"users": {"user_idx": np.arange(3), "user_id": np.array([10, 20, 30])},
                "items": {"item_idx": np.arange(3), "train_listener_count": np.array([1, 2, 3])},
                "train_interactions": {"user_idx": np.array([0, 0, 0, 1, 1, 2]),
                    "item_idx": np.array([0, 1, 2, 1, 2, 2]),
                    "listen_count": np.array([count, 2, 1, 3, 1, 1], dtype=np.int64)}}
        manifest = {"mapping": {"users": 3, "items": 3, "dtype": "int32", "start": 0},
                    "processing": {"cleaning": {"train": {"users": 3, "items": 3,
                        "user_item_pairs": 6, "events": count + 8}}}}
        stats, tables, _, _ = eda.aggregate_train(data, manifest)
        self.assertEqual(stats["events"], count + 8)
        self.assertEqual([r["events"] for r in tables["popularity_bands"]], [count, 5, 3])
        self.assertEqual([r["user_item_pairs"] for r in tables["popularity_bands"]], [1, 2, 3])

    def test_integrity_and_bom_corruption(self):
        path = self.package / "users.csv"
        original = path.read_bytes()
        try:
            path.write_bytes(original[3:])
            with self.assertRaisesRegex(ValueError, "무결성"):
                eda.load_package(self.package)
            real_fingerprint = eda.fingerprint
            # 무결성 기록까지 바뀌어도 BOM 없는 파일을 독립적으로 거부한다.
            def recorded(p):
                if p == path:
                    entry = json.loads((self.package / "dataset_manifest.json").read_text())["files"]["users.csv"]
                    return {k: entry[k] for k in ("bytes", "sha256")}
                return real_fingerprint(p)
            with patch.object(eda, "fingerprint", side_effect=recorded):
                with self.assertRaisesRegex(ValueError, "BOM"):
                    eda.load_package(self.package)
        finally:
            path.write_bytes(original)

    def test_histogram_boundaries_and_csv_roundtrip(self):
        rows = eda.histogram(np.array([9, 10, 49, 50, 10000, 20000]), "train")
        self.assertEqual([r["users"] for r in rows[:3]], [1, 2, 1])
        self.assertEqual(rows[-1]["users"], 2)
        self.assertEqual(sum(r["users"] for r in rows), 6)
        path = Path(self.folder.name) / "quoted.csv"
        eda.write_table(path, [{"name": 'NA,"NULL"\n곡', "integer": 2**60}])
        self.assertEqual(path.read_bytes()[:3], b"\xef\xbb\xbf")

    def test_exclusion_ledgers_with_same_totals_are_rejected(self):
        manifest, data, _ = eda.load_package(self.package)
        _, _, tracks, _ = eda.aggregate_train(data, manifest)
        changed = copy.deepcopy(manifest)
        reasons = changed["evaluation"]["periods"]["validation"]["user_exclusions"]
        reasons["no_train"] += 1
        reasons["no_period_activity"] -= 1
        with self.assertRaisesRegex(ValueError, "train 사용자 수와 제외 사유"):
            eda.aggregate_evaluation(data, changed, tracks)
        changed = copy.deepcopy(manifest)
        changed["evaluation"]["periods"]["validation"]["period_pairs_by_kind"]["candidate"]["users"] -= 1
        with self.assertRaisesRegex(ValueError, "후보 사용자 수"):
            eda.aggregate_evaluation(data, changed, tracks)
        with patch.object(eda, "REFERENCE", self.reference_for(self.all_train_package)):
            manifest, data, _ = eda.load_package(self.all_train_package)
        _, _, tracks, _ = eda.aggregate_train(data, manifest)
        reasons = manifest["evaluation"]["periods"]["validation"]["user_exclusions"]
        reasons["no_period_activity"] += 1
        reasons["no_target"] -= 1
        with self.assertRaisesRegex(ValueError, "기간 활동 사용자 수와 제외 사유"):
            eda.aggregate_evaluation(data, manifest, tracks)

    def test_failed_verification_replaces_old_success(self):
        output = Path(self.folder.name) / "verification-failure"
        eda.write_verification_record(output, {"matches": True, "status": "passed"})
        (output / "analysis_manifest.json").write_text(json.dumps({"dataset_version": "invalid"}))
        with self.assertRaisesRegex(ValueError, "산출물 파일 목록 불일치"):
            verification.verify(self.package, output, False)
        result = json.loads((output / "verification.json").read_text())
        self.assertIs(result["matches"], False)
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["mismatches"])

    def test_regenerated_analysis_invalidates_old_verification(self):
        output = Path(self.folder.name) / "analysis-failure"
        eda.write_verification_record(output, {"matches": True, "status": "passed"})
        def reuse(manifest, stats, tables, data):
            tables.update({name: [{}] for name in eda.TABLE_NAMES if name not in tables})
        with patch.object(eda, "reuse_reports", side_effect=reuse), patch.object(eda, "draw_figures", side_effect=ValueError("그림 생성 실패")):
            with self.assertRaisesRegex(ValueError, "그림 생성 실패"):
                eda.analyze(self.package, output)
        result = json.loads((output / "verification.json").read_text())
        self.assertIsNone(result["matches"])
        self.assertEqual(result["status"], "not_run")


    def test_csv_and_own_manifest_changed_together_fail_fixed_reference(self):
        package = Path(self.folder.name) / "changed-package"
        shutil.copytree(self.package, package)
        path = package / "items.csv"
        import csv
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        rows[0]["artist_name"] += "변경"
        eda.write_table(path, rows)
        manifest_path = package / "dataset_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["files"]["items.csv"].update(eda.fingerprint(path))
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False))
        before = eda.REFERENCE.read_bytes()
        rejected = Path(self.folder.name) / "rejected-verification"
        rejected.mkdir()
        for run in (lambda: eda.load_package(package),
                    lambda: eda.analyze(package, Path(self.folder.name) / "rejected-analysis"),
                    lambda: verification.verify(package, rejected, False)):
            with self.subTest(command=run):
                with self.assertRaisesRegex(ValueError, "고정 패키지 기준 불일치.*식별값"):
                    run()
        self.assertEqual(before, eda.REFERENCE.read_bytes())
        self.assertEqual(eda.check_package_reference(self.package)["dataset_version"], eda.VERSION)

    def test_wrong_version_and_unsuccessful_reference_are_rejected(self):
        reference = json.loads(eda.REFERENCE.read_text())
        reference["matches"] = False
        path = Path(self.folder.name) / "failed-reference.json"
        path.write_text(json.dumps(reference))
        with patch.object(eda, "REFERENCE", path), self.assertRaisesRegex(ValueError, "성공 기록"):
            eda.check_package_reference(self.package)
        package = Path(self.folder.name) / "wrong-version"
        package.mkdir()
        (package / "dataset_manifest.json").write_text(json.dumps({"dataset_version": "other"}))
        with self.assertRaisesRegex(ValueError, "dataset_version"):
            eda.check_package_reference(package)

    def test_exact_inventory_reports_missing_extra_and_preserves_files(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            for name in eda.ARTIFACT_PATHS | eda.RECORD_PATHS:
                path = output / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"test")
            eda.check_output_inventory(output, complete=True)
            extra = output / "tables/old-run.csv"
            extra.write_bytes(b"user data")
            (output / "figures/popularity-shares.png").unlink()
            for run in (lambda: eda.check_output_inventory(output, complete=True),
                        lambda: verification.verify(self.package, output, False)):
                with self.assertRaisesRegex(ValueError, "누락=.*figures/popularity-shares.png.*초과=.*tables/old-run.csv"):
                    run()
            with self.assertRaisesRegex(ValueError, "초과=.*tables/old-run.csv"):
                eda.analyze(self.package, output)
            self.assertEqual(extra.read_bytes(), b"user data")
            self.assertEqual(json.loads((output / "verification.json").read_text())["status"], "failed")
            extra.unlink()
            eda.check_output_inventory(output, complete=False)
            with self.assertRaisesRegex(ValueError, "누락=.*figures/popularity-shares.png"):
                verification.verify(self.package, output, False)

    def test_unlisted_file_cannot_be_accepted_by_analysis_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            for name in eda.ARTIFACT_PATHS | eda.RECORD_PATHS:
                path = output / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"test")
            (output / "analysis_manifest.json").write_text(json.dumps({
                "dataset_version": eda.VERSION, "output_files": {"tables/unexpected.csv": {}}}))
            with self.assertRaisesRegex(ValueError, "manifest의 산출물 목록"):
                verification.verify(self.package, output, False)


class EvaluationReportTest(unittest.TestCase):
    """게시할 집계표와 보고서의 임시 사본에서 평가 수치 변조를 검사한다."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="evaluation-report-")
        self.addCleanup(self.folder.cleanup)
        self.report = Path(self.folder.name) / "report.md"
        self.text = (ROOT / "reports/listening-eda.md").read_text()
        self.output = ROOT / "reports/eda"
        self.analysis = json.loads((self.output / "analysis_manifest.json").read_text())

    def check_text(self, text):
        self.report.write_text(text)
        verification.verify_evaluation_report(self.output, self.analysis, self.report)

    def test_correct_report_and_reordered_rows_pass(self):
        self.check_text(self.text)
        lines = self.text.splitlines()
        indices = [i for i, line in enumerate(lines) if line.startswith("| validation ") and "제외" in line]
        lines[indices[0]], lines[indices[1]] = lines[indices[1]], lines[indices[0]]
        self.check_text("\n".join(lines))

    def test_validation_single_target_169_to_999_fails(self):
        self.assertIn("169명 / 4.31%", self.text)
        with self.assertRaisesRegex(ValueError, "보고서 표 수치.*1개"):
            self.check_text(self.text.replace("169명 / 4.31%", "999명 / 4.31%"))

    def test_all_displayed_evaluation_values_units_and_shares_are_checked(self):
        lines = self.text.splitlines()
        active = False
        tested = 0
        for index, line in enumerate(lines):
            if line.startswith("| ") and any(line.startswith("| " + header) for header in (
                    "표본 5,000명에 대한", "평가 대상 사용자별", "사용자별 정답 수 구간", "기간·구분")):
                active = True
                continue
            if not line.startswith("|"):
                active = False
            if not active or not re.search(r"\d", line):
                continue
            cells = line.split("|")
            for column in range(2, len(cells) - 1):
                cell = cells[column]
                for match in re.finditer(r"\d[\d,.]*|명|쌍|건|%", cell):
                    changed = cells.copy()
                    changed[column] = cell[:match.start()] + "오류" + cell[match.end():]
                    edited = lines.copy()
                    edited[index] = "|".join(changed)
                    with self.subTest(row=cells[1], column=column, token=match.group()):
                        with self.assertRaisesRegex(ValueError, "보고서 표"):
                            self.check_text("\n".join(edited))
                    tested += 1
        self.assertGreater(tested, 150)

    def test_missing_duplicate_wrong_period_category_and_denominator_fail(self):
        line = "| 1개 | 169명 / 4.31% | 157명 / 4.08% |"
        edits = [self.text.replace(line, ""), self.text.replace(line, line + "\n" + line),
                 self.text.replace("| validation 신규곡 제외 |", "| train 신규곡 제외 |"),
                 self.text.replace("| 100개 이상 |", "| 101개 이상 |"),
                 self.text.replace("validation 3,922명", "validation 5,000명"),
                 self.text.replace("validation 사용자 수·평가 사용자 대비", "test 사용자 수·평가 사용자 대비"),
                 self.text.replace("기간 전체 쌍 대비", "기간 전체 사건 대비")]
        for text in edits:
            with self.subTest(edit=text[:50]), self.assertRaisesRegex(ValueError, "보고서 표"):
                self.check_text(text)


if __name__ == "__main__":
    unittest.main()
