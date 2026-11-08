"""2-2 입력 검증·공통 정제·UTC 분할. 최종 전달 패키지는 다음 단계에서 만든다.

대규모 사건은 DuckDB 파일 DB 안에서 처리한다. 공개 반환값은 파일명·해시와
집계뿐이며 원본 경로, 사용자 ID, 개인별 기록은 보고서에 포함하지 않는다.
정규화 구현과 고정 경계는 1단계 audit_raw_data에서 재사용한다.
"""
from __future__ import annotations

import argparse
import csv
import json
import platform
import re
import resource
import subprocess
import sys
import tempfile
import time
import tomllib
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import duckdb

import audit_raw_data as base

EXPECTED_SCHEMA = [
    ("user_id", "BIGINT"), ("listened_at", "TIMESTAMP"),
    ("artist_name", "VARCHAR"), ("recording_name", "VARCHAR"),
]
SPLITS = (
    ("train", base.WINDOW_START, base.TRAIN_END),
    ("validation", base.TRAIN_END, base.VALIDATION_END),
    ("test", base.VALIDATION_END, base.WINDOW_END),
)
EXCLUSION_REASONS = ("missing_required", "outside_window", "empty_name", "duplicate_excess")
REFERENCE = Path(__file__).resolve().parents[1] / "reports/raw-data-audit.md"


def read_reference(path: Path = REFERENCE) -> dict:
    """Git의 Markdown에 보존한 6개 입력 기준값을 읽는다. 로컬 JSON은 불필요하다."""
    text = path.read_text(encoding="utf-8")
    heading = "## 입력 파일 해시와 불변성\n"
    if text.count(heading) != 1:
        raise ValueError("Input reference section is missing or ambiguous")
    section = text.split(heading, 1)[1].split("\n## ", 1)[0]
    result = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 4 or not cells[0].startswith('"'):
            continue
        name, size, digest = json.loads(cells[0]), int(cells[1]), json.loads(cells[2])
        if name in result or size < 0 or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid or duplicate input reference record")
        result[name] = {"bytes": size, "sha256": digest}
    if set(result) != set(base.REQUIRED_FILES):
        raise ValueError("Input reference must describe exactly the six required files")
    return result


def fingerprint_inputs(raw_dir: Path) -> list[dict]:
    result = []
    for name in base.REQUIRED_FILES:
        path = raw_dir / name
        if not path.is_file():
            raise ValueError(f"Missing required input: {name}")
        result.append({"file": name, "bytes": path.stat().st_size, "sha256": base.sha256(path)})
    return result


def verify_input_files(raw_dir: Path, reference: dict) -> list[dict]:
    if set(reference) != set(base.REQUIRED_FILES):
        raise ValueError("Exactly six input reference records are required")
    actual = fingerprint_inputs(raw_dir)
    for entry in actual:
        measured = {k: entry[k] for k in ("bytes", "sha256")}
        if measured != reference[entry["file"]]:
            raise ValueError(f"Input bytes/SHA256 differ from phase 1: {entry['file']}")
    return actual


def validate_input_tables(con, raw_dir: Path, expected_users: int = 5000) -> dict:
    """5,000명을 재선정하지 않고 정확한 스키마와 기존 CSV의 사용자 집합을 검증한다."""
    con.read_parquet(str(raw_dir / base.REQUIRED_FILES[0])).create_view("raw_input")
    schema = [(r[0], r[1]) for r in con.execute("DESCRIBE raw_input").fetchall()]
    if schema != EXPECTED_SCHEMA:
        raise ValueError("Parquet schema differs from the exact phase-1 column order/types")
    with (raw_dir / "selected_users.csv").open(encoding="utf-8", newline="") as stream:
        header = next(csv.reader(stream), [])
    if header != ["user_id", "train_unique_tracks", "user_index"]:
        raise ValueError("Unexpected selected_users.csv header")
    con.execute("""CREATE TABLE selected_cohort AS SELECT * FROM read_csv(?, header=true,
        columns={'user_id':'BIGINT','train_unique_tracks':'BIGINT','user_index':'BIGINT'})""",
        [str(raw_dir / "selected_users.csv")])
    cohort = base.one(con, """SELECT count(*) AS rows, count(DISTINCT user_id) AS users,
        count(DISTINCT user_index) AS indices, min(user_index) AS min_index,
        max(user_index) AS max_index, count(*) FILTER (WHERE user_id IS NULL OR
        train_unique_tracks IS NULL OR user_index IS NULL) AS missing_fields FROM selected_cohort""")
    if (cohort["rows"], cohort["users"], cohort["indices"], cohort["min_index"],
            cohort["max_index"], cohort["missing_fields"]) != (
            expected_users, expected_users, expected_users, 1, expected_users, 0):
        raise ValueError("Selected cohort must be a non-null one-to-one mapping of the expected size")
    membership = base.one(con, """SELECT
        (SELECT count(*) FROM (SELECT DISTINCT user_id FROM raw_input WHERE user_id IS NOT NULL) r
            ANTI JOIN selected_cohort c USING(user_id)) AS raw_users_not_in_cohort,
        (SELECT count(*) FROM selected_cohort c ANTI JOIN
            (SELECT DISTINCT user_id FROM raw_input WHERE user_id IS NOT NULL) r USING(user_id))
            AS cohort_users_without_raw_rows""")
    if any(membership.values()):
        raise ValueError("Parquet and selected cohort user sets differ (IDs withheld)")
    return {"schema": [{"column": n, "type": t} for n, t in schema],
            "cohort": {**cohort, **membership},
            "raw_rows": con.execute("SELECT count(*) FROM raw_input").fetchone()[0]}


def build_clean_events(con) -> dict:
    """raw_input에서 재사용할 사건·분할 뷰와 학습 집계를 만든다."""
    base.install_normalizer(con)
    # CASE 순서로 제외 사유를 한 번만 귀속한다. 곡 키는 항상 두 열의 튜플이다.
    con.execute(f"""CREATE VIEW classified_input AS WITH names AS (
        SELECT *, norm_name(artist_name) AS artist_key,
            norm_name(recording_name) AS recording_key FROM raw_input)
        SELECT *, CASE
            WHEN user_id IS NULL OR listened_at IS NULL OR artist_name IS NULL
                OR recording_name IS NULL THEN 'missing_required'
            WHEN listened_at < TIMESTAMP '{base.WINDOW_START}'
                OR listened_at >= TIMESTAMP '{base.WINDOW_END}' THEN 'outside_window'
            WHEN artist_key = '' OR recording_key = '' THEN 'empty_name'
            ELSE NULL END AS exclusion_reason FROM names""")
    counts = base.one(con, """SELECT count(*) AS raw_rows,
        count(*) FILTER (WHERE exclusion_reason='missing_required') AS missing_required,
        count(*) FILTER (WHERE exclusion_reason='outside_window') AS outside_window,
        count(*) FILTER (WHERE exclusion_reason='empty_name') AS empty_name,
        count(*) FILTER (WHERE exclusion_reason IS NULL) AS valid_rows FROM classified_input""")
    # arg_min의 값과 정렬 키 모두 STRUCT 쌍이다. 독립 min으로 원문을 합성하지 않는다.
    # encode는 UTF-8 BLOB이므로 입력 순서나 문자열 collation에 의존하지 않는다.
    con.execute(f"""CREATE TABLE clean_events AS WITH chosen AS (
        SELECT user_id, listened_at, artist_key, recording_key,
            arg_min(struct_pack(artist_name := artist_name, recording_name := recording_name),
                struct_pack(artist_bytes := encode(artist_name),
                    recording_bytes := encode(recording_name))) AS original
        FROM classified_input WHERE exclusion_reason IS NULL
        GROUP BY user_id, listened_at, artist_key, recording_key)
        SELECT user_id, listened_at, artist_key, recording_key,
            original.artist_name AS artist_name, original.recording_name AS recording_name,
            CASE WHEN listened_at < TIMESTAMP '{base.TRAIN_END}' THEN 'train'
                WHEN listened_at < TIMESTAMP '{base.VALIDATION_END}' THEN 'validation'
                ELSE 'test' END AS split FROM chosen""")
    partitions = []
    for split, start, end in SPLITS:
        con.execute(f"CREATE VIEW {split}_events AS SELECT * FROM clean_events WHERE split = '{split}'")
        stats = base.one(con, f"""SELECT count(*) AS events, count(DISTINCT user_id) AS users,
            count(DISTINCT (artist_key, recording_key)) AS items FROM {split}_events""")
        partitions.append({"split": split, "start_inclusive_utc": start,
                           "end_exclusive_utc": end, **stats})
    kept = sum(p["events"] for p in partitions)
    exclusions = {k: counts[k] for k in EXCLUSION_REASONS[:-1]}
    exclusions["duplicate_excess"] = counts["valid_rows"] - kept
    if min(exclusions.values()) < 0 or counts["raw_rows"] != sum(exclusions.values()) + kept:
        raise RuntimeError("Raw/exclusion/split accounting does not reconcile")
    # train만 읽는다. 1~9곡 사용자도 남기고 미래에만 활동한 사용자에게 학습 행을 만들지 않는다.
    con.execute("""CREATE TABLE train_pairs AS SELECT user_id, artist_key, recording_key,
        count(*)::BIGINT AS listen_count FROM train_events GROUP BY ALL""")
    con.execute("""CREATE VIEW train_display_names AS WITH variants AS (SELECT artist_key, recording_key,
        artist_name, recording_name, count(*)::BIGINT AS representative_events
        FROM train_events GROUP BY ALL) SELECT * FROM variants
        QUALIFY row_number() OVER (PARTITION BY artist_key, recording_key ORDER BY representative_events DESC,
            encode(artist_name), encode(recording_name)) = 1""")
    train = base.one(con, """SELECT count(*) AS user_item_pairs,
        count(DISTINCT user_id) AS users, coalesce(sum(listen_count),0) AS events,
        count(DISTINCT (artist_key,recording_key)) AS items FROM train_pairs""")
    no_train = con.execute("""SELECT count(*) FROM selected_cohort ANTI JOIN
        (SELECT DISTINCT user_id FROM train_pairs) t USING(user_id)""").fetchone()[0]
    return {"raw_rows": counts["raw_rows"], "exclusions": exclusions,
            "clean_events": kept, "accounting_matches": True, "partitions": partitions,
            "train": train, "cohort_users_without_train": no_train}


def quality_diagnostics(con) -> dict:
    """학습 기간의 별도 테이블에서 플래그를 추적한다. 사건 삭제·계정 병합은 하지 않는다."""
    # 대표에서 탈락한 원문 변형의 신호도 동일 사건의 진단에서 빠뜨리지 않는다.
    con.execute(r"""CREATE TABLE quality_train_names AS WITH signals AS (
        SELECT user_id,listened_at,artist_key,recording_key,
            contains(artist_name,chr(65533)) OR contains(recording_name,chr(65533)) AS replacement,
            regexp_matches(artist_name||recording_name,'[\x{200B}\x{FEFF}]') AS zero_width_or_bom,
            regexp_matches(artist_name||recording_name,
                '[\x{0000}-\x{0008}\x{000E}-\x{001B}\x{007F}-\x{0084}\x{0086}-\x{009F}]')
                AS nonwhitespace_control
        FROM classified_input WHERE exclusion_reason IS NULL AND listened_at < ?::TIMESTAMP)
        SELECT user_id,listened_at,artist_key,recording_key,
            bool_or(replacement) AS replacement, bool_or(zero_width_or_bom) AS zero_width_or_bom,
            bool_or(nonwhitespace_control) AS nonwhitespace_control FROM signals
        WHERE replacement OR zero_width_or_bom OR nonwhitespace_control GROUP BY ALL""", [base.TRAIN_END])
    con.execute("""CREATE TABLE quality_train_timestamps AS SELECT user_id,listened_at,
        count(*) AS tracks FROM train_events GROUP BY user_id,listened_at""")
    con.execute("""CREATE TABLE quality_train_days AS SELECT user_id,cast(listened_at AS DATE) AS day,
        count(*) AS events FROM train_events GROUP BY user_id,day""")
    con.execute("""CREATE TABLE quality_profile_pairs AS WITH shared AS (
        SELECT a.user_id AS first_user, b.user_id AS second_user, count(*) AS shared_items
        FROM train_pairs a JOIN train_pairs b USING(artist_key,recording_key)
        WHERE a.user_id < b.user_id GROUP BY a.user_id,b.user_id),
        sizes AS (SELECT user_id,count(*) AS items FROM train_pairs GROUP BY user_id)
        SELECT s.*, a.items AS first_items, b.items AS second_items,
            shared_items*1.0/(a.items+b.items-shared_items) AS jaccard,
            shared_items=a.items AND a.items=b.items AS identical_itemset
        FROM shared s JOIN sizes a ON a.user_id=s.first_user JOIN sizes b ON b.user_id=s.second_user
        WHERE shared_items>=10 OR (shared_items=a.items AND a.items=b.items)""")
    con.execute("""CREATE VIEW quality_train_user_flags AS SELECT user_id,
        EXISTS(SELECT 1 FROM quality_train_names n WHERE n.user_id=u.user_id) AS abnormal_name,
        EXISTS(SELECT 1 FROM quality_train_timestamps t WHERE t.user_id=u.user_id AND tracks>1) AS multitrack,
        EXISTS(SELECT 1 FROM quality_train_timestamps t WHERE t.user_id=u.user_id AND tracks>=10) AS burst_ge10,
        EXISTS(SELECT 1 FROM quality_train_days d WHERE d.user_id=u.user_id AND events>1440) AS day_over1440,
        EXISTS(SELECT 1 FROM quality_profile_pairs p WHERE p.identical_itemset AND
            (p.first_user=u.user_id OR p.second_user=u.user_id)) AS identical_itemset
        FROM (SELECT DISTINCT user_id FROM train_pairs) u""")
    names = base.one(con, """SELECT count(*) AS affected_events,
        count(*) FILTER(WHERE replacement) AS replacement_events,
        count(*) FILTER(WHERE zero_width_or_bom) AS zero_width_or_bom_events,
        count(*) FILTER(WHERE nonwhitespace_control) AS nonwhitespace_control_events FROM quality_train_names""")
    timestamps = base.one(con, """SELECT count(*) FILTER(WHERE tracks>1) AS multitrack_timestamps,
        coalesce(sum(tracks) FILTER(WHERE tracks>1),0) AS multitrack_events,
        count(*) FILTER(WHERE tracks>=10) AS timestamps_ge10,
        coalesce(sum(tracks) FILTER(WHERE tracks>=10),0) AS events_ge10,
        count(*) FILTER(WHERE tracks>=100) AS timestamps_ge100,
        coalesce(max(tracks),0) AS max_tracks FROM quality_train_timestamps""")
    activity = base.one(con, """SELECT count(*) FILTER(WHERE events>720) AS active_days_over720,
        count(*) FILTER(WHERE events>1440) AS active_days_over1440,
        coalesce(max(events),0) AS max_day_events FROM quality_train_days""")
    profiles = base.one(con, """SELECT count(*) FILTER(WHERE shared_items>=10) AS pairs_with_at_least10_shared_items,
        coalesce(max(jaccard),0) AS max_jaccard,
        count(*) FILTER(WHERE identical_itemset) AS identical_itemset_pairs FROM quality_profile_pairs""")
    users = base.one(con, """SELECT count(*) FILTER(WHERE abnormal_name) AS abnormal_name,
        count(*) FILTER(WHERE multitrack) AS multitrack,
        count(*) FILTER(WHERE burst_ge10) AS burst_ge10,
        count(*) FILTER(WHERE day_over1440) AS day_over1440,
        count(*) FILTER(WHERE identical_itemset) AS identical_itemset FROM quality_train_user_flags""")
    return {"period": "train only", "name_signals": names, "timestamps": timestamps,
            "activity": activity, "profiles": profiles, "flagged_users": users,
            "events_removed_by_flags": 0, "accounts_merged": 0}


def read_settings(config_path: Path) -> dict:
    config_path = config_path.resolve()
    cfg = tomllib.loads(config_path.read_text(encoding="utf-8"))
    settings = {k: (config_path.parent / Path(cfg[k]).expanduser()).resolve()
                for k in ("raw_dir", "cache_dir")}
    settings.update(memory_limit=cfg.get("memory_limit", "3GB"), threads=cfg.get("threads", 2))
    if type(settings["threads"]) is not int or not 1 <= settings["threads"] <= 4:
        raise ValueError("threads must be an integer from 1 through 4")
    raw, cache = settings["raw_dir"], settings["cache_dir"]
    if cache == raw or raw in cache.parents:
        raise ValueError("Cache must not be inside raw_dir")
    return settings


@contextmanager
def working_connection(settings: dict):
    """기본 3GB·2스레드로 실행하고 DB와 spill을 종료 시 정리한다."""
    settings["cache_dir"].mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="events-", dir=settings["cache_dir"]) as folder:
        con = duckdb.connect(str(Path(folder) / "events.duckdb"))
        try:
            con.execute("SET memory_limit = ?", [settings["memory_limit"]])
            con.execute("SET threads = ?", [settings["threads"]])
            con.execute("SET TimeZone = 'UTC'")
            con.execute("SET preserve_insertion_order = false")
            con.execute("SET temp_directory = ?", [str(Path(folder) / "spill")])
            con.execute("SET max_temp_directory_size = '12GiB'")
            yield con
        finally:
            con.close()


def render_report(result: dict) -> str:
    lines = ["# 2-2 입력 검증·정제·UTC 분할 실행 기록", "",
             "최종 전달 패키지 생성 전의 전체 입력 검증·임시 정제 실행이다. "
             "원본 사용자 ID·개인별 기록·개인 경로는 포함하지 않는다.", "",
             "## 입력 파일 대조", "", "| 파일 | bytes | SHA256 |", "| --- | --- | --- |"]
    for entry in result["input_files"]:
        lines.append(f"| {entry['file']} | {entry['bytes']} | `{entry['sha256']}` |")
    for heading, key in [("입력 스키마·선정 사용자", "input_validation"),
                         ("정제 제외와 분할 집계", "cleaning"),
                         ("학습 기간 품질 진단", "quality"), ("실행 추적", "execution")]:
        if key in result:
            lines += ["", "## " + heading, "", "```json",
                      json.dumps(result[key], ensure_ascii=False, indent=2), "```"]
    lines += ["", "6개 입력의 실행 전후 바이트 수·SHA256 일치를 확인했다. "
              "품질 플래그는 삭제 조건이 아니며 각 범주는 겹칠 수 있다. "
              "진단의 개인별 플래그는 임시 DB 안에만 있고 종료 시 삭제한다.", "",
              "중복 대표 원문은 원문 쌍의 UTF-8 바이트 순으로 선택한다. "
              "표시 이름은 정제된 train 사건의 원문 쌍 빈도, 같은 바이트 동률 순서로 결정한다. "
              "매핑·CSR·평가 파일·manifest와 패키지 재로딩 검증은 다음 단계에 남아 있다.", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--inputs-only", action="store_true", help="6입력·스키마·사용자 집합 검증만 실행")
    parser.add_argument("--report", type=Path, help="새 Markdown 집계 보고서. 기존 파일은 덮어쓰지 않음")
    args = parser.parse_args()
    settings = read_settings(args.config)
    if args.report:
        report_path = args.report.resolve()
        if report_path == settings["raw_dir"] or settings["raw_dir"] in report_path.parents:
            raise ValueError("Report must not be inside raw_dir")
        if report_path.suffix != ".md" or report_path.exists():
            raise ValueError("Report must be a new .md file; existing reports are preserved")
    started = time.monotonic()
    before = verify_input_files(settings["raw_dir"], read_reference())
    result = {"input_files": before}
    with working_connection(settings) as con:
        result["input_validation"] = validate_input_tables(con, settings["raw_dir"])
        if not args.inputs_only:
            print("[preprocessing] input verified; cleaning and splitting", flush=True)
            result["cleaning"] = build_clean_events(con)
            print("[preprocessing] train-only quality diagnostics", flush=True)
            result["quality"] = quality_diagnostics(con)
    if fingerprint_inputs(settings["raw_dir"]) != before:
        raise RuntimeError("Input changed during execution; no report written")
    repo = Path(__file__).resolve().parents[1]
    def git(*parts):
        completed = subprocess.run(["git", *parts], cwd=repo, capture_output=True, text=True)
        return completed.stdout.strip() if completed.returncode == 0 else None
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    result["execution"] = {
        "finished_utc": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "peak_rss_bytes": rss if sys.platform == "darwin" else rss * 1024,
        "python": platform.python_version(), "duckdb": duckdb.__version__,
        "memory_limit": settings["memory_limit"], "threads": settings["threads"],
        "branch": git("branch", "--show-current"), "head_at_execution": git("rev-parse", "HEAD"),
        "uncommitted_at_execution": bool(git("status", "--porcelain=v1")),
        "script_sha256": base.sha256(Path(__file__)),
        "normalizer_script_sha256": base.sha256(Path(base.__file__)),
        "config_sha256": base.sha256(args.config), "inputs_unchanged": True,
        "final_datasets_created": False,
    }
    if args.report:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with report_path.open("x", encoding="utf-8") as stream:
            stream.write(render_report(result))
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
