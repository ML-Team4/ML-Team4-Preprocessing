"""정규화 충돌·반복 보존·미래 신규곡 제외·원본 불변성을 확인하는 작은 회귀 검사."""
import importlib.util
import tempfile
import unittest
import unicodedata
from datetime import datetime
from pathlib import Path

import duckdb

MODULE = Path(__file__).resolve().parents[1]/"scripts/audit_raw_data.py"
spec = importlib.util.spec_from_file_location("audit_raw_data", MODULE)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class AuditRulesTest(unittest.TestCase):
    def test_unicode_normalizer_matches_python_and_preserves_versions(self):
        con = duckdb.connect()
        self.addCleanup(con.close)
        audit.install_normalizer(con)
        examples = [None, "", "  A\t B\u00a0", "e\u0301", "가", "가", "Song (Live)",
                    "Song (Remaster)", "A—B!", "a", "A", "x\u200by"]
        examples += [" " + chr(c) + " A" + chr(c) + " B " for c in audit.WHITESPACE]
        for text in examples:
            expected = None if text is None else " ".join(unicodedata.normalize("NFC",text).split())
            self.assertEqual(con.execute("SELECT norm_name(?)",[text]).fetchone()[0], expected)

    def test_diagnostic_deduplication_repeat_and_temporal_targets(self):
        with tempfile.TemporaryDirectory() as folder:
            raw_dir = Path(folder)
            con = duckdb.connect()
            self.addCleanup(con.close)
            con.execute("CREATE TABLE fixture (user_id BIGINT,listened_at TIMESTAMP,artist_name VARCHAR,recording_name VARCHAR)")
            records = [(1,"2026-08-17 00:00:00"," Café "," Song "),
                       (1,"2026-08-17 00:00:00","Cafe\u0301","Song"),
                       (1,"2026-08-17 00:00:00"," Café "," Song "),
                       (1,"2026-08-18 00:00:00","Café","Song"),
                       (1,"2026-09-28 00:00:00","Café","Song"),
                       (1,"2026-09-28 00:01:00","Artist","Track0"),
                       (1,"2026-09-28 00:02:00","Artist","Track0"),
                       (1,"2026-10-05 00:00:00","Future","New"),
                       (1,"2026-10-12 00:00:00","Artist","Outside"),
                       (1,"2026-08-19 00:00:00"," ","Invalid"),
                       (1,"2026-08-19 00:00:00",None,"Invalid"),
                       (2,"2026-08-17 00:00:00","a|b","c"),
                       (2,"2026-08-17 00:00:00","a","b|c")]
            records += [(2,"2026-08-20 00:00:00","Artist",f"Track{i}") for i in range(15)]
            con.executemany("INSERT INTO fixture VALUES (?,?,?,?)",records)
            con.execute("COPY fixture TO ? (FORMAT PARQUET)",[str(raw_dir/audit.REQUIRED_FILES[0])])
            (raw_dir/"selected_users.csv").write_text("user_id,train_unique_tracks,user_index\n1,1,1\n2,17,2\n")
            original_hash = audit.sha256(raw_dir/audit.REQUIRED_FILES[0])
            result = audit.audit(con,raw_dir,2,log=lambda *a,**k:None)
            self.assertEqual(result["raw_exact"]["excess_duplicate_rows"],1)
            self.assertEqual(result["diagnostic"]["excess_duplicate_rows"],2)
            self.assertEqual(result["diagnostic"]["invalid_or_outside_rows"],3)
            self.assertEqual(result["train"]["items"],18)  # 구분자를 포함한 두 튜플도 별개
            self.assertEqual(result["repeats"]["train_repeat_events_beyond_first"],1)
            self.assertEqual(result["cohort"]["train_item_count_mismatches"],0)
            self.assertEqual(result["evaluation"]["validation"]["target_pairs"],1)
            self.assertEqual(result["evaluation"]["validation"]["eligible_users"],1)
            self.assertEqual(result["evaluation"]["validation"]["seen_in_train"]["events"],1)
            self.assertEqual(result["evaluation"]["test"]["new_item"]["events"],1)
            self.assertEqual(result["evaluation"]["test"]["target_pairs"],0)
            self.assertEqual(audit.sha256(raw_dir/audit.REQUIRED_FILES[0]),original_hash)


if __name__ == "__main__":
    unittest.main()
