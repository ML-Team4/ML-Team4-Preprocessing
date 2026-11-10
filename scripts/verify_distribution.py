"""동봉 원본 CSV·전처리 ZIP과 재생성 CSV 7개의 동일성을 검사한다."""
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

import preprocess_events as inputs
import raw_inputs
from audit_raw_data import sha256
from export_raw_csv import raw_stats
from package_dataset import NAME

ROOT = Path(__file__).resolve().parents[1]
CSV_NAMES = tuple(f"{name}.csv" for name in (
    "train_interactions", "users", "items", "validation_users", "validation_targets", "test_users", "test_targets"))


def verify(raw: Path, archive_path: Path, reproduced: Path | None = None) -> dict:
    """자체 manifest와 별도 고정 기준을 함께 확인하며 해제 사본에 의존하지 않는다."""
    before = inputs.verify_input_files(raw, inputs.read_reference(raw_inputs.CSV_REFERENCE))
    conversion = json.loads((raw / "distribution_manifest.json").read_text(encoding="utf-8"))
    with inputs.working_connection({"cache_dir": ROOT / ".cache/distribution-check", "threads": 2,
                                   "memory_limit": "3GB"}) as con:
        actual_weeks = []
        for entry in conversion["weeks"]:
            raw_inputs.bind_raw(con, raw / entry["file"], "week_check")
            stats = raw_stats(con, "week_check")
            outside = con.execute("SELECT count(*) FROM week_check WHERE listened_at IS NULL "
                "OR listened_at < ?::TIMESTAMP OR listened_at >= ?::TIMESTAMP",
                [entry["start_inclusive_utc"], entry["end_exclusive_utc"]]).fetchone()[0]
            if stats != {key: entry[key] for key in stats} or outside:
                raise ValueError(f"주별 원본 통계 또는 UTC 경계가 다릅니다: {entry['file']}")
            actual_weeks.append(stats["rows"])
        raw_inputs.bind_raw(con, raw)
        stats = raw_stats(con, "raw_input")
        inputs.validate_input_tables(con, raw)
        if stats != conversion["converted"] or sum(actual_weeks) != stats["rows"]:
            raise ValueError("전체 CSV 통계가 고정 변환 기록과 다릅니다")
    if before != inputs.fingerprint_inputs(raw):
        raise ValueError("검사 중 원본 파일이 변경됐습니다")
    reference = json.loads((ROOT / "config/listening-package-reference.json").read_text())
    origin = json.loads((ROOT / "config/verified-package-origin.json").read_text())
    mismatches = []
    with zipfile.ZipFile(archive_path) as archive:
        expected = {f"{NAME}/{name}" for name in (*CSV_NAMES, "README.md", "COPYING", "dataset_manifest.json")}
        if set(archive.namelist()) != expected or len(archive.namelist()) != len(expected):
            raise ValueError("ZIP에 CSV 7개·안내·manifest·COPYING이 정확히 한 폴더로 들어 있어야 합니다")
        manifest_bytes = archive.read(f"{NAME}/dataset_manifest.json")
        if hashlib.sha256(manifest_bytes).hexdigest() != reference["manifest_sha256"]:
            raise ValueError("배포 ZIP manifest가 고정 기준과 다릅니다")
        manifest = json.loads(manifest_bytes)
        if manifest["dataset_version"] != NAME:
            raise ValueError("배포 패키지명이 다릅니다")
        for name, entry in manifest["files"].items():
            with archive.open(f"{NAME}/{name}") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != entry["sha256"] or archive.getinfo(f"{NAME}/{name}").file_size != entry["bytes"]:
                raise ValueError(f"ZIP 내부 파일 무결성이 다릅니다: {name}")
            if name in CSV_NAMES:
                if {key: entry[key] for key in ("bytes", "sha256")} != origin["csv_files"][name]:
                    raise ValueError(f"검증된 기존 CSV와 다릅니다: {name}")
                if reproduced is not None:
                    path = reproduced / name
                    if not path.is_file() or path.stat().st_size != entry["bytes"] or sha256(path) != digest:
                        mismatches.append(name)
    return {"matches": not mismatches, "raw_rows": stats["rows"], "raw_users": stats["users"],
            "weekly_rows": actual_weeks, "raw_missing_rows": conversion["multiset_comparison"]["missing_rows"],
            "raw_extra_rows": conversion["multiset_comparison"]["extra_rows"],
            "zip_csv_files": len(CSV_NAMES), "original_csv_mismatch_files": 0,
            "reproduced_csv_mismatch_files": len(mismatches) if reproduced is not None else None,
            "mismatching_csv_files": mismatches}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=ROOT / "datasets/raw")
    parser.add_argument("--archive", type=Path, default=ROOT / f"datasets/{NAME}.zip")
    parser.add_argument("--reproduced", type=Path, help="동봉 ZIP과 CSV 7개를 비교할 새 패키지 폴더")
    args = parser.parse_args()
    result = verify(args.raw_dir, args.archive, args.reproduced)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["matches"]:
        raise SystemExit("재생성 CSV와 배포 CSV가 다릅니다")


if __name__ == "__main__":
    main()
