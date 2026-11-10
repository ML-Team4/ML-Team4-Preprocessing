"""주별 CSV의 값·중복·경계 정밀도와 동일 전처리 결과를 검사한다."""
import csv
import gzip
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import raw_inputs
import preprocess_events as events
from export_raw_csv import compare_multiset
from package_dataset import create_zip
from test_build_dataset import example_input, fixture_rows, build


def write_weekly(raw, rows):
    for i, name in enumerate(raw_inputs.WEEKLY_FILES):
        start = datetime(2026, 8, 17) + timedelta(weeks=i)
        with gzip.open(raw / name, "wt", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream, quoting=csv.QUOTE_ALL)
            writer.writerow(raw_inputs.COLUMNS)
            for user, stamp, artist, title in rows:
                stamp = datetime.fromisoformat(stamp) if isinstance(stamp, str) else stamp
                if start <= stamp < start + timedelta(weeks=1):
                    writer.writerow([user, stamp.strftime("%Y-%m-%d %H:%M:%S.%f"), artist, title])
    (raw / "distribution_manifest.json").write_text('{}\n')


class RawCsvTest(unittest.TestCase):
    def test_special_names_duplicate_counts_and_week_microseconds(self):
        rows = [(9223372036854775806, datetime(2026, 8, 17, 0, 0, 0, 1), ' NA\t"가수,\n', 'NULL'),
                (1, datetime(2026, 8, 23, 23, 59, 59, 999999), 'é', ' Cafe\u0301\r\n'),
                (1, datetime(2026, 8, 24), '', 'null'),
                (1, datetime(2026, 10, 11, 23, 59, 59, 999999), 'Artist', 'Song')]
        rows += [rows[0]] * 2
        with tempfile.TemporaryDirectory() as folder, duckdb.connect() as con:
            raw = Path(folder)
            write_weekly(raw, rows)
            raw_inputs.bind_raw(con, raw)
            self.assertCountEqual(con.execute('SELECT * FROM raw_input').fetchall(), rows)
            con.execute('CREATE TABLE expected(user_id BIGINT, listened_at TIMESTAMP, artist_name VARCHAR, recording_name VARCHAR)')
            con.executemany('INSERT INTO expected VALUES (?,?,?,?)', rows)
            self.assertEqual(compare_multiset(con, 'expected', 'raw_input'), {'missing_rows': 0, 'extra_rows': 0})
            # DISTINCT만 비교하면 놓치는 동일 행 1개 유실을 검출한다.
            con.execute('CREATE TABLE changed AS SELECT * FROM raw_input')
            con.execute('DELETE FROM changed WHERE rowid=(SELECT max(rowid) FROM changed WHERE user_id=9223372036854775806)')
            self.assertEqual(compare_multiset(con, 'raw_input', 'changed'), {'missing_rows': 1, 'extra_rows': 0})
            (raw / raw_inputs.WEEKLY_FILES[0]).unlink()
            with self.assertRaisesRegex(ValueError, '8개'):
                raw_inputs.bind_raw(con, raw)

    def test_csv_and_parquet_build_identical_seven_files(self):
        rows = [r for r in fixture_rows('original') if datetime.fromisoformat(r[1]) < datetime(2026, 10, 12)]
        with example_input(rows) as source, tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original = build.build_package(output_root=root / 'parquet', version='listenbrainz-preprocessing', **source)
            raw = root / 'raw'
            raw.mkdir()
            source_raw = events.read_settings(source['config'])['raw_dir']
            for name in raw_inputs.METADATA_FILES:
                shutil.copyfile(source_raw / name, raw / name)
            write_weekly(raw, rows)
            config = root / 'csv.toml'
            config.write_text('raw_dir="raw"\ncache_dir="cache"\n')
            reference = root / 'csv-reference.toml'
            reference.write_text('\n'.join(f'[files."{r["file"]}"]\nbytes={r["bytes"]}\nsha256="{r["sha256"]}"'
                for r in events.fingerprint_inputs(raw)))
            converted = build.build_package(config, root / 'csv', 'listenbrainz-preprocessing',
                reference_path=reference, expected_users=source['expected_users'])
            for original_csv in original.glob('*.csv'):
                self.assertEqual(original_csv.read_bytes(), (converted / original_csv.name).read_bytes(), original_csv.name)
            from validate_dataset import validate_dataset
            self.assertTrue(validate_dataset(config, converted, reference_path=reference,
                expected_users=source['expected_users'])['matches'])
            archive = root / 'distribution.zip'
            create_zip(converted, archive)
            import zipfile
            with zipfile.ZipFile(archive) as z:
                self.assertEqual(len(z.namelist()), 10)
                self.assertTrue(all(name.startswith('listenbrainz-preprocessing/') and name.count('/') == 1
                                    for name in z.namelist()))
            with self.assertRaises(FileExistsError):
                create_zip(converted, archive)


if __name__ == '__main__':
    unittest.main()
