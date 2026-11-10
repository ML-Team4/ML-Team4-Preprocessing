"""검증된 8주 원본을 값·중복을 보존한 주별 gzip CSV로 변환한다."""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import date, timedelta
from pathlib import Path

import preprocess_events as inputs
import raw_inputs
from audit_raw_data import one, sha256, sql_literal


def raw_stats(con, relation: str) -> dict:
    result = one(con, f"""SELECT count(*) AS rows, count(DISTINCT user_id) AS users,
        min(listened_at) AS first_utc, max(listened_at) AS last_utc FROM {relation}""")
    return {key: str(value) if key.endswith("_utc") and value is not None else value
            for key, value in result.items()}


def compare_multiset(con, left: str, right: str) -> dict:
    """EXCEPT ALL을 양방향으로 적용해 같은 행의 출현 횟수 차이까지 검출한다."""
    return {name: con.execute(f"SELECT count(*) FROM (SELECT * FROM {a} EXCEPT ALL "
                             f"SELECT * FROM {b})").fetchone()[0]
            for name, a, b in (("missing_rows", left, right), ("extra_rows", right, left))}


def export_raw(raw: Path, output: Path, reference: Path, *, expected_rows: int = 9_350_628) -> dict:
    """기존 입력의 고정 무결성 기준을 확인한 뒤 새 위치에만 배포 파일을 만든다."""
    before = inputs.verify_input_files(raw, inputs.read_reference())
    if output.exists() or reference.exists():
        raise FileExistsError("기존 원본 배포본과 기준을 보존하도록 새 출력 경로를 지정하세요")
    output.mkdir(parents=True)
    settings = {"cache_dir": output.parent / ".raw-export-cache", "threads": 2, "memory_limit": "3GB"}
    try:
        with inputs.working_connection(settings) as con:
            raw_inputs.bind_raw(con, raw, "original")
            original = raw_stats(con, "original")
            if original["rows"] != expected_rows:
                raise ValueError("원본 행 수가 검증된 표본과 다릅니다")
            inputs.validate_input_tables(con, raw)
            weeks = []
            for i, name in enumerate(raw_inputs.WEEKLY_FILES):
                start = date(2026, 8, 17) + timedelta(weeks=i)
                end = start + timedelta(weeks=1)
                con.execute(f"""COPY (SELECT * FROM original
                    WHERE listened_at >= TIMESTAMP '{start}' AND listened_at < TIMESTAMP '{end}')
                    TO {sql_literal(str(output / name))} (FORMAT CSV, HEADER true,
                    COMPRESSION gzip, FORCE_QUOTE *, TIMESTAMPFORMAT '%Y-%m-%d %H:%M:%S.%f')""")
                raw_inputs.bind_raw(con, output / name, "week_check")
                stats = raw_stats(con, "week_check")
                outside = con.execute("SELECT count(*) FROM week_check WHERE listened_at IS NULL "
                    "OR listened_at < ?::TIMESTAMP OR listened_at >= ?::TIMESTAMP", [start, end]).fetchone()[0]
                if outside:
                    raise ValueError("주별 CSV에 UTC 주 경계 밖의 행이 있습니다")
                weeks.append({"file": name, "start_inclusive_utc": f"{start} 00:00:00",
                              "end_exclusive_utc": f"{end} 00:00:00", **stats})
            for name in raw_inputs.METADATA_FILES:
                shutil.copyfile(raw / name, output / name)
            raw_inputs.bind_raw(con, output, "converted")
            # 재로딩한 CSV를 고정해 양방향 대조 시 같은 행 집합을 읽는다.
            con.execute("CREATE TABLE csv_values AS SELECT * FROM converted")
            converted = raw_stats(con, "csv_values")
            differences = compare_multiset(con, "original", "csv_values")
            if original != converted or sum(w["rows"] for w in weeks) != original["rows"] or any(differences.values()):
                raise ValueError("원본과 CSV의 자료형·통계·중복 포함 전체 값이 다릅니다")
        if inputs.fingerprint_inputs(raw) != before:
            raise RuntimeError("변환 중 원본 파일이 변경됐습니다")
        manifest = {"format": "weekly gzip CSV", "timestamp_semantics": "UTC, timestamp[us]",
            "columns": raw_inputs.COLUMNS, "encoding": "UTF-8", "header": True,
            "preserved": ["duplicate rows", "case", "whitespace", "special characters", "microseconds"],
            "original_files": before,
            "original_manifest_scope": "source_manifest.json과 verified_8week_summary.json은 변환 전 Parquet 원본의 출처·검증 기록이다.",
            "distribution_scope": "주별 CSV의 고정 무결성 기준은 config/raw-csv-reference.toml이며 Parquet 배포를 요구하지 않는다.",
            "original": original, "converted": converted, "weeks": weeks,
            "multiset_comparison": {**differences, "matches": True},
            "files": {name: {"bytes": (output / name).stat().st_size, "sha256": sha256(output / name)}
                      for name in (*raw_inputs.WEEKLY_FILES, *raw_inputs.METADATA_FILES)}}
        (output / "distribution_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        reference.parent.mkdir(parents=True, exist_ok=True)
        with reference.open("x", encoding="utf-8") as stream:
            stream.write("# 동봉된 주별 CSV와 출처·검증 자료의 고정 무결성 기준. 기존 Parquet 기준과 구분한다.\n")
            for entry in inputs.fingerprint_inputs(output):
                stream.write(f'\n[files."{entry["file"]}"]\nbytes = {entry["bytes"]}\nsha256 = "{entry["sha256"]}"\n')
        return manifest
    except BaseException:
        shutil.rmtree(output)
        raise
    finally:
        if settings["cache_dir"].exists():
            settings["cache_dir"].rmdir()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, required=True, help="고정 기준으로 확인할 기존 Parquet 원본 폴더")
    parser.add_argument("--output-dir", type=Path, default=Path("datasets/raw"))
    parser.add_argument("--reference", type=Path, default=raw_inputs.CSV_REFERENCE)
    args = parser.parse_args()
    result = export_raw(args.raw_dir.resolve(), args.output_dir.resolve(), args.reference.resolve())
    print(json.dumps({key: result[key] for key in ("original", "converted", "weeks", "multiset_comparison")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
