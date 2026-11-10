"""전달 원본에서 독립적으로 사건 키를 계산해 정제·분할을 대조한다. 집계만 출력한다."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import preprocess_events as events

EVENT_KEY = "user_id, listened_at, artist_key, recording_key"
TRACK_KEY = "artist_key, recording_key"


def build_reference(con, parquet: Path) -> None:
    """생산 코드의 분할 경계, 정규화 함수, 분류 관계를 읽지 않고 원본을 계산한다."""
    con.execute("""CREATE TABLE review_periods AS SELECT * FROM (VALUES
        ('train',       TIMESTAMP '2026-08-17', TIMESTAMP '2026-09-28'),
        ('validation',  TIMESTAMP '2026-09-28', TIMESTAMP '2026-10-05'),
        ('test',        TIMESTAMP '2026-10-05', TIMESTAMP '2026-10-12')
        ) AS periods(period, start_utc, end_utc)""")
    con.execute(r"""CREATE MACRO review_name(s) AS trim(regexp_replace(nfc_normalize(s),
        '[\x{0009}-\x{000D}\x{001C}-\x{0020}\x{0085}\x{00A0}\x{1680}\x{2000}-\x{200A}\x{2028}\x{2029}\x{202F}\x{205F}\x{3000}]+',
        ' ', 'g'))""")
    con.execute("""CREATE TABLE review_rows AS
        WITH names AS (
            SELECT user_id, listened_at, artist_name, recording_name,
                review_name(artist_name) AS artist_key, review_name(recording_name) AS recording_key
            FROM read_parquet(?)
        )
        SELECT *, CASE
            WHEN user_id IS NULL OR listened_at IS NULL OR artist_name IS NULL OR recording_name IS NULL
                THEN 'missing_required'
            WHEN listened_at < TIMESTAMP '2026-08-17' OR listened_at >= TIMESTAMP '2026-10-12'
                THEN 'outside_window'
            WHEN artist_key = '' OR recording_key = '' THEN 'empty_name'
            ELSE 'valid'
        END AS category
        FROM names""", [str(parquet)])
    con.execute(f"""CREATE TABLE review_events AS
        SELECT {EVENT_KEY}, count(*) AS valid_rows FROM review_rows
        WHERE category = 'valid' GROUP BY {EVENT_KEY}""")


def compare_partitions(con) -> dict:
    """반개구간으로 기대 집합을 만들고 각 키의 소속과 양방향 차이를 확인한다."""
    membership_errors = con.execute(f"""SELECT count(*) FROM (
        SELECT {EVENT_KEY}
        FROM review_events e LEFT JOIN review_periods p
          ON e.listened_at >= p.start_utc AND e.listened_at < p.end_utc
        GROUP BY {EVENT_KEY} HAVING count(p.period) <> 1
        )""").fetchone()[0]
    duplicates = events.base.one(con, f"""SELECT count(*) AS duplicated_event_keys,
        count(*) FILTER (WHERE periods > 1) AS cross_period_event_keys
        FROM (SELECT count(DISTINCT split) AS periods
            FROM clean_events GROUP BY {EVENT_KEY} HAVING count(*) > 1)""")
    unknown = con.execute("""SELECT count(*) FROM clean_events
        ANTI JOIN review_periods ON split = period""").fetchone()[0]
    partitions = []
    for period, start, end in con.execute(
            "SELECT * FROM review_periods ORDER BY start_utc").fetchall():
        # 양쪽 키의 중복은 별도로 검사한다. ANTI JOIN은 방향별 누락·초과를 센다.
        stats = events.base.one(con, f"""WITH expected AS (
            SELECT * FROM review_events WHERE listened_at >= ? AND listened_at < ?
        ), actual AS (
            SELECT {EVENT_KEY} FROM clean_events WHERE split = ?
        )
        SELECT count(*) AS events, sum(valid_rows) AS valid_rows,
            count(DISTINCT user_id) AS users, count(DISTINCT ({TRACK_KEY})) AS items,
            min(listened_at) AS first_utc, max(listened_at) AS last_utc,
            count(DISTINCT listened_at::DATE) AS observed_days,
            (SELECT count(*) FROM expected ANTI JOIN actual USING ({EVENT_KEY})) AS missing,
            (SELECT count(*) FROM actual ANTI JOIN expected USING ({EVENT_KEY})) AS extra
        FROM expected""", [start, end, period])
        partitions.append({"period": period, "start_utc": start, "end_utc": end, **stats})
    return {"reference_membership_errors": membership_errors, **duplicates,
            "unknown_production_split": unknown, "partitions": partitions}


def review_cleaning(con) -> dict:
    """동일 유효 행에서 곡 키·사용자-곡 쌍과 중복 처리의 영향을 집계한다."""
    categories = dict(con.execute("SELECT category, count(*) FROM review_rows GROUP BY ALL").fetchall())
    exclusions = {name: categories.get(name, 0)
                  for name in ("missing_required", "outside_window", "empty_name")}
    exclusions["duplicate_excess"] = con.execute(
        "SELECT sum(valid_rows - 1) FROM review_events").fetchone()[0]
    name_changes = events.base.one(con, """SELECT count(*) FILTER (
            WHERE artist_name <> artist_key OR recording_name <> recording_key) AS changed_valid_rows,
        count(*) FILTER (WHERE (artist_name <> artist_key OR recording_name <> recording_key)
            AND listened_at < TIMESTAMP '2026-09-28') AS changed_valid_train_rows,
        count(*) FILTER (WHERE trim(artist_name) IN ('?', '--')
            OR trim(recording_name) IN ('?', '--')) AS placeholder_like_valid_rows
        FROM review_rows WHERE category = 'valid'""")
    raw_key_duplicates = con.execute("""SELECT sum(n - 1) FROM (
        SELECT count(*) AS n FROM review_rows WHERE category = 'valid'
        GROUP BY user_id, listened_at, artist_name, recording_name)""").fetchone()[0]

    con.execute("""CREATE VIEW review_train_rows AS SELECT * FROM review_rows
        WHERE category = 'valid'
          AND listened_at >= TIMESTAMP '2026-08-17' AND listened_at < TIMESTAMP '2026-09-28'""")
    name_stats = {}
    for label, keys in (("raw", "artist_name, recording_name"), ("normalized", TRACK_KEY)):
        name_stats[label] = events.base.one(con, f"""SELECT count(*) AS items,
            sum(listeners) AS user_item_pairs,
            count(*) FILTER (WHERE listeners = 1) AS single_listener_items
            FROM (SELECT count(DISTINCT user_id) AS listeners
                  FROM review_train_rows GROUP BY {keys})""")

    # 기대 사용자-곡마다 초과 행을 포함한 수와 구별되는 시각의 수를 함께 유지한다.
    con.execute(f"""CREATE TABLE review_train_pairs AS
        SELECT user_id, {TRACK_KEY}, sum(valid_rows) AS valid_rows, count(*) AS events
        FROM review_events
        WHERE listened_at >= TIMESTAMP '2026-08-17' AND listened_at < TIMESTAMP '2026-09-28'
        GROUP BY user_id, {TRACK_KEY}""")
    con.execute(f"""CREATE TABLE review_train_items AS
        SELECT {TRACK_KEY}, count(*) AS listeners, sum(valid_rows) AS valid_rows, sum(events) AS events
        FROM review_train_pairs GROUP BY {TRACK_KEY}""")
    bands = events.base.rows(con, """SELECT least(listeners, 3) AS listener_band, count(*) AS items,
        sum(valid_rows) AS valid_rows, sum(events) AS events, sum(valid_rows - events) AS removed_rows,
        round(100.0 * sum(valid_rows - events) / sum(valid_rows), 4) AS removed_pct
        FROM review_train_items GROUP BY listener_band ORDER BY listener_band""")
    listener_differences = con.execute(f"""SELECT count(*) FROM review_train_items expected
        FULL JOIN (SELECT {TRACK_KEY}, count(*) AS listeners FROM train_pairs GROUP BY {TRACK_KEY})
          actual USING ({TRACK_KEY})
        WHERE expected.listeners IS DISTINCT FROM actual.listeners""").fetchone()[0]
    repeats = events.base.one(con, """SELECT count(*) FILTER (WHERE events > 1) AS repeated_pairs,
        sum(events - 1) AS events_after_first FROM review_train_pairs""")
    return {"raw_rows": sum(categories.values()), "valid_rows": categories["valid"], "exclusions": exclusions,
            "raw_key_duplicate_excess": raw_key_duplicates, **name_changes, "train_key_stats": name_stats,
            "listener_bands": bands, "items_with_different_listener_counts": listener_differences, **repeats}


def review_retention_signals(con) -> dict:
    """고활동 사용자와 기호가 있는 곡을 진단하고 처리 관계에는 그대로 둔다."""
    punctuation = events.base.one(con, r"""WITH items AS (
        SELECT events, regexp_matches(artist_key, '[\p{P}\p{S}]')
            OR regexp_matches(recording_key, '[\p{P}\p{S}]') AS has_punctuation_or_symbol
        FROM review_train_items
    )
    SELECT count(*) FILTER (WHERE has_punctuation_or_symbol) AS items,
        sum(events) FILTER (WHERE has_punctuation_or_symbol) AS events
    FROM items""")
    activity = events.base.one(con, """WITH user_days AS (
        SELECT user_id, listened_at::DATE AS day, count(*) AS events
        FROM review_events
        WHERE listened_at >= TIMESTAMP '2026-08-17' AND listened_at < TIMESTAMP '2026-09-28'
        GROUP BY user_id, day
    ), flagged_users AS (
        SELECT DISTINCT user_id FROM user_days WHERE events > 1440
    )
    SELECT count(*) AS observed_user_days,
        count(*) FILTER (WHERE events > 1440) AS user_days_over1440,
        sum(events) FILTER (WHERE events > 1440) AS events_on_days_over1440,
        (SELECT count(*) FROM flagged_users) AS users_over1440,
        (SELECT sum(events) FROM user_days SEMI JOIN flagged_users USING (user_id))
            AS train_events_by_flagged_users
    FROM user_days""")
    return {"train_punctuation_or_symbol": punctuation, "train_high_activity": activity}


def review_cohort(con) -> dict:
    con.execute(r"""CREATE MACRO review_legacy_name(s) AS
        nfc_normalize(regexp_replace(trim(s), '\s+', ' ', 'g'))""")
    return events.base.one(con, """WITH ranked AS (
        SELECT *, md5(user_id::VARCHAR || ':42') AS hash,
            row_number() OVER (ORDER BY md5(user_id::VARCHAR || ':42')) AS rank
        FROM selected_cohort
    ), legacy AS (
        SELECT user_id, count(DISTINCT (review_legacy_name(artist_name),
            review_legacy_name(recording_name))) AS items
        FROM review_rows
        WHERE listened_at >= TIMESTAMP '2026-08-17' AND listened_at < TIMESTAMP '2026-09-28'
            AND user_id IS NOT NULL AND review_legacy_name(artist_name) <> ''
            AND review_legacy_name(recording_name) <> ''
        GROUP BY user_id
    ), current AS (
        SELECT user_id, count(*) AS items FROM review_train_pairs GROUP BY user_id
    )
    SELECT count(*) AS users, min(train_unique_tracks) AS csv_min_items,
        count(*) FILTER (WHERE hash >= '8') AS outside_lower_hash_half,
        count(*) FILTER (WHERE rank <> user_index) AS within_cohort_rank_differences,
        count(*) FILTER (WHERE legacy.items IS DISTINCT FROM train_unique_tracks) AS legacy_count_differences,
        count(*) FILTER (WHERE current.items IS DISTINCT FROM train_unique_tracks) AS current_count_differences,
        count(*) FILTER (WHERE current.items IS NULL) AS users_without_current_train,
        min(current.items) AS current_min_items
    FROM ranked LEFT JOIN legacy USING (user_id) LEFT JOIN current USING (user_id)""")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    settings = events.read_settings(args.config)
    raw_dir = settings["raw_dir"]
    before = events.verify_input_files(raw_dir, events.read_reference())

    with events.working_connection(settings) as con:
        inputs = events.validate_input_tables(con, raw_dir)
        build_reference(con, raw_dir / "listens_8weeks_5000users.parquet")
        production = events.build_clean_events(con)
        comparison = compare_partitions(con)
        cleaning = review_cleaning(con)
        cohort = review_cohort(con)
        retention = review_retention_signals(con)

    unchanged = events.fingerprint_inputs(raw_dir) == before
    partitions = comparison["partitions"]
    total_events = sum(p["events"] for p in partitions)
    valid_rows = cleaning["valid_rows"]
    excluded = cleaning["exclusions"]
    matches = (
        unchanged
        and cleaning["raw_rows"] == production["raw_rows"] == inputs["raw_rows"]
        and excluded == production["exclusions"]
        and total_events == production["clean_events"] == valid_rows - excluded["duplicate_excess"]
        and cleaning["raw_rows"] == sum(excluded.values()) + total_events
        and not any(value for key, value in comparison.items() if key != "partitions")
        and all(p["missing"] == p["extra"] == 0 for p in partitions)
        and cleaning["items_with_different_listener_counts"] == 0
        and cohort["csv_min_items"] >= 10
        and cohort["outside_lower_hash_half"] == cohort["within_cohort_rank_differences"] == 0
        and cohort["legacy_count_differences"] == 0
    )
    print(json.dumps({"matches": matches, "inputs_unchanged": unchanged, "cohort": cohort,
                      "cleaning": cleaning, "retention_signals": retention, "comparison": comparison},
                     ensure_ascii=False, indent=2, default=str))
    if not matches:
        raise SystemExit("원본 기준과 정제·분할 결과가 일치하지 않습니다")


if __name__ == "__main__":
    main()
