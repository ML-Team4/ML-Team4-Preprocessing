"""Apply one explicit raw schema to weekly gzip CSVs and legacy Parquet files."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config/paths.example.toml"
PARQUET = "listens_8weeks_5000users.parquet"
METADATA_FILES = ("README.txt", "selected_users.csv", "source_manifest.json",
                  "verified_8week_summary.json", "COPYING")
LEGACY_FILES = (PARQUET, *METADATA_FILES)
WEEKLY_FILES = tuple(f"listens_{date(2026, 8, 17) + timedelta(weeks=i)}.csv.gz" for i in range(8))
CSV_FILES = (*WEEKLY_FILES, *METADATA_FILES, "distribution_manifest.json")
CSV_REFERENCE = ROOT / "config/raw-csv-reference.toml"
PARQUET_REFERENCE = ROOT / "config/raw-input-reference.toml"
COLUMNS = {"user_id": "BIGINT", "listened_at": "TIMESTAMP",
           "artist_name": "VARCHAR", "recording_name": "VARCHAR"}


def is_csv(raw: Path) -> bool:
    """Detect a directory containing weekly gzip CSVs."""
    return raw.is_dir() and any(raw.glob("listens_*.csv.gz"))


def required_files(raw: Path) -> tuple[str, ...]:
    """Return the required input filenames for the detected raw format."""
    if is_csv(raw):
        actual = {p.name for p in raw.glob("listens_*.csv.gz")}
        if actual != set(WEEKLY_FILES):
            raise ValueError("원본 주별 CSV 8개의 파일 목록이 UTC 주 경계와 다릅니다")
        return CSV_FILES
    return LEGACY_FILES


def resolve_reference(raw: Path, reference: Path) -> Path:
    """Select the CSV-specific fixed reference when a caller uses the legacy default."""
    return CSV_REFERENCE if is_csv(raw) and reference.resolve() == PARQUET_REFERENCE else reference


def bind_raw(con, source: Path, view: str = "raw_input") -> None:
    """Register the raw files under view with explicit types to preserve timestamp precision."""
    if not view.isidentifier():
        raise ValueError("원본 뷰 이름이 올바르지 않습니다")
    if is_csv(source) or source.name.endswith(".csv.gz"):
        if source.is_dir():
            required_files(source)
        files = [source / name for name in WEEKLY_FILES] if source.is_dir() else [source]
        paths = "[" + ",".join("'" + str(path).replace("'", "''") + "'" for path in files) + "]"
        # Preserve quoted empty strings as names; only unquoted empty fields represent NULL.
        con.sql(f"""SELECT * FROM read_csv({paths}, header=true,
            columns={{'user_id':'BIGINT','listened_at':'TIMESTAMP',
                      'artist_name':'VARCHAR','recording_name':'VARCHAR'}},
            auto_detect=false, nullstr='', allow_quoted_nulls=false, strict_mode=true,
            delim=',', quote='"', escape='"',
            timestampformat='%Y-%m-%d %H:%M:%S.%f')""").create_view(view)
    else:
        path = source / PARQUET if source.is_dir() else source
        if pq.read_schema(path).field("listened_at").type != pa.timestamp("us"):
            raise ValueError("원본 시각의 정밀도 또는 시간대 표기가 고정 스키마와 다릅니다")
        con.read_parquet(str(path)).create_view(view)
