"""원본을 변경하지 않고 품질 통계와 제한된 사례만 저장하는 원본 점검.

외부 코드를 복사하지 않고 작성했다. API와 SQL 출처 및 매개변수 설명은
docs/audit-code-guide.md 참조. 정규화/중복 제거는 임시 진단 집계이며 전달용
데이터나 모델·가중치·평가 지표를 만들지 않는다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import resource
import subprocess
import sys
import tempfile
import time
import tomllib
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import raw_inputs

WINDOW_START = "2026-08-17 00:00:00"
TRAIN_END = "2026-09-28 00:00:00"
VALIDATION_END = "2026-10-05 00:00:00"
WINDOW_END = "2026-10-12 00:00:00"
# Python str.isspace()와 동일한 29개 코드포인트를 고정해 런타임 차이를 막는다.
WHITESPACE = tuple(range(0x09, 0x0E)) + tuple(range(0x1C, 0x21)) + (
    0x85, 0xA0, 0x1680, *range(0x2000, 0x200B), 0x2028, 0x2029,
    0x202F, 0x205F, 0x3000,
)
SPACE_PATTERN = "[" + "".join(r"\x{" + f"{c:X}" + "}" for c in WHITESPACE) + "]+"
REQUIRED_FILES = raw_inputs.LEGACY_FILES


def sha256(path: Path) -> str:
    """스트림으로 읽어 원본 파일 전체를 메모리에 올리지 않는다."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def sql_literal(value: str) -> str:
    """SQL 문자열 리터럴만 이스케이프한다. 식별자/SQL 조각에는 쓰지 않는다."""
    return "'" + value.replace("'", "''") + "'"


def rows(con, sql: str, params=None) -> list[dict]:
    cursor = con.execute(sql, params) if params is not None else con.execute(sql)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def one(con, sql: str, params=None) -> dict:
    return rows(con, sql, params)[0]


def install_normalizer(con) -> None:
    con.execute("CREATE MACRO norm_name(s) AS trim(regexp_replace(nfc_normalize(s), "
                + sql_literal(SPACE_PATTERN) + ", ' ', 'g'))")


def git_trace(repo: Path) -> dict:
    def run(*args):
        if not (repo / ".git").exists():
            return None
        result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else None
    return {"head": run("rev-parse", "--verify", "HEAD"),
            "branch": run("symbolic-ref", "--short", "HEAD"),
            "status": run("status", "--porcelain"),
            "audit_script_sha256": sha256(Path(__file__))}


def audit(con, raw_dir: Path, sample_size: int, log=print) -> dict:
    """집계·샘플만 반환한다. 외부 경로/원본 user_id는 결과에 내보내지 않는다."""
    log("[audit] schema·기간·결측값 점검", flush=True)
    raw_inputs.bind_raw(con, raw_dir, "raw")
    schema = rows(con, "DESCRIBE raw")
    expected = [("user_id", "BIGINT"), ("listened_at", "TIMESTAMP"),
                ("artist_name", "VARCHAR"), ("recording_name", "VARCHAR")]
    actual = [(r["column_name"], r["column_type"]) for r in schema]
    if actual != expected:
        raise ValueError(f"입력 schema가 요구한 자료형과 다릅니다: {actual!r}")
    install_normalizer(con)
    con.execute("CREATE TABLE cohort AS SELECT * FROM read_csv(?, header=true, "
                "columns={'user_id':'BIGINT','train_unique_tracks':'BIGINT','user_index':'BIGINT'})",
                [str(raw_dir / "selected_users.csv")])
    cohort = one(con, """SELECT count(*) AS rows, count(DISTINCT user_id) AS users,
        count(DISTINCT user_index) AS indices, min(user_index) AS min_index,
        max(user_index) AS max_index, min(train_unique_tracks) AS min_claimed_train_items,
        count(*) FILTER (WHERE user_id IS NULL OR user_index IS NULL OR
        train_unique_tracks IS NULL) AS missing_rows FROM cohort""")
    if cohort["rows"] != cohort["users"] or cohort["rows"] != cohort["indices"] or cohort["missing_rows"]:
        raise ValueError("selected_users.csv는 NULL 없는 일대일 매핑이어야 합니다")
    basic = one(con, """SELECT count(*) AS rows, count(DISTINCT user_id) AS users,
        count(DISTINCT cast(listened_at AS DATE)) AS dates,
        min(listened_at) AS min_time, max(listened_at) AS max_time,
        count(*) FILTER (WHERE listened_at < TIMESTAMP '2026-08-17' OR
        listened_at >= TIMESTAMP '2026-10-12') AS outside_window FROM raw""")
    missing = one(con, "SELECT " + ", ".join(
        f"count(*) FILTER (WHERE {column} IS NULL) AS {column}" for column, _ in expected) + " FROM raw")
    strings = one(con, """SELECT
        count(*) FILTER (WHERE artist_name = '') AS empty_artist,
        count(*) FILTER (WHERE recording_name = '') AS empty_recording,
        count(*) FILTER (WHERE artist_name <> '' AND norm_name(artist_name) = '') AS whitespace_artist,
        count(*) FILTER (WHERE recording_name <> '' AND norm_name(recording_name) = '') AS whitespace_recording,
        count(*) FILTER (WHERE artist_name <> norm_name(artist_name)) AS changed_artist_rows,
        count(*) FILTER (WHERE recording_name <> norm_name(recording_name)) AS changed_recording_rows,
        count(*) FILTER (WHERE artist_name <> nfc_normalize(artist_name) OR
        recording_name <> nfc_normalize(recording_name)) AS nfc_changed_rows,
        count(*) FILTER (WHERE artist_name <> norm_name(artist_name) OR
        recording_name <> norm_name(recording_name)) AS any_name_changed_rows FROM raw""")
    cohort.update(one(con, """SELECT
        (SELECT count(*) FROM (SELECT DISTINCT user_id FROM raw) r ANTI JOIN cohort c USING(user_id)) AS raw_users_not_in_cohort,
        (SELECT count(*) FROM cohort c ANTI JOIN (SELECT DISTINCT user_id FROM raw) r USING(user_id)) AS cohort_users_without_raw_rows"""))
    raw_parts = rows(con, """SELECT CASE WHEN listened_at < TIMESTAMP '2026-09-28' THEN 'train'
        WHEN listened_at < TIMESTAMP '2026-10-05' THEN 'validation' ELSE 'test' END AS split,
        count(*) AS events, count(DISTINCT user_id) AS users FROM raw
        WHERE listened_at >= TIMESTAMP '2026-08-17' AND listened_at < TIMESTAMP '2026-10-12'
        GROUP BY split ORDER BY split""")
    raw_exact = one(con, """SELECT count(*) AS unique_events,
        (SELECT count(*) FROM raw) - count(*) AS excess_duplicate_rows FROM
        (SELECT DISTINCT user_id, listened_at, artist_name, recording_name FROM raw)""")
    raw_items = one(con, "SELECT count(*) AS items FROM (SELECT DISTINCT artist_name, recording_name FROM raw)")
    log("[audit] 임시 정규화와 중복 진단", flush=True)
    # 한 번에 9백만 행을 Python 객체로 바꾸지 않고 SQL로 집계한다.
    con.execute("""CREATE TABLE normalized AS SELECT user_id, listened_at,
        norm_name(artist_name) AS artist_name, norm_name(recording_name) AS recording_name
        FROM raw WHERE user_id IS NOT NULL AND listened_at IS NOT NULL
        AND listened_at >= TIMESTAMP '2026-08-17' AND listened_at < TIMESTAMP '2026-10-12'
        AND artist_name IS NOT NULL AND recording_name IS NOT NULL
        AND norm_name(artist_name) <> '' AND norm_name(recording_name) <> ''""")
    con.execute("""CREATE TABLE duplicate_groups AS SELECT user_id, listened_at,
        artist_name, recording_name, count(*) AS multiplicity FROM normalized GROUP BY ALL""")
    diag = one(con, """SELECT count(*) AS events, count(DISTINCT user_id) AS users,
        sum(multiplicity) - count(*) AS excess_duplicate_rows,
        count(*) FILTER (WHERE multiplicity > 1) AS duplicate_groups,
        max(multiplicity) AS max_multiplicity,
        (SELECT count(*) FROM raw) - sum(multiplicity) AS invalid_or_outside_rows FROM duplicate_groups""")
    con.execute("CREATE VIEW events AS SELECT user_id, listened_at, artist_name, recording_name FROM duplicate_groups")
    con.execute("""CREATE TABLE train_pairs AS SELECT user_id, artist_name, recording_name,
        count(*) AS listens FROM events WHERE listened_at < TIMESTAMP '2026-09-28' GROUP BY ALL""")
    con.execute("""CREATE TABLE catalog AS SELECT artist_name, recording_name, count(*) AS listeners
        FROM train_pairs GROUP BY artist_name, recording_name""")
    normalized_items = one(con, "SELECT count(*) AS items FROM (SELECT DISTINCT artist_name, recording_name FROM events)")
    partitions = rows(con, """SELECT CASE WHEN listened_at < TIMESTAMP '2026-09-28' THEN 'train'
        WHEN listened_at < TIMESTAMP '2026-10-05' THEN 'validation' ELSE 'test' END AS split,
        count(*) AS events, count(DISTINCT user_id) AS users,
        count(DISTINCT (artist_name, recording_name)) AS items FROM events GROUP BY split ORDER BY split""")
    train = one(con, """SELECT count(*) AS items,
        count(*) FILTER (WHERE listeners=1) AS single_listener_items,
        count(*) FILTER (WHERE listeners<=2) AS one_two_listener_items,
        max(listeners) AS max_listeners, (SELECT count(*) FROM train_pairs) AS user_item_pairs,
        (SELECT count(DISTINCT user_id) FROM train_pairs) AS users,
        (SELECT sum(listens) FROM train_pairs) AS events FROM catalog""")
    cohort.update(one(con, """SELECT count(*) AS train_item_count_mismatches FROM cohort c
        LEFT JOIN (SELECT user_id, count(*) AS n FROM train_pairs GROUP BY user_id) t USING(user_id)
        WHERE coalesce(t.n,0) <> c.train_unique_tracks"""))
    cohort.update(one(con, """SELECT count(*) AS no_train_users FROM cohort c
        ANTI JOIN (SELECT DISTINCT user_id FROM train_pairs) t USING(user_id)"""))
    cohort.update(one(con, """SELECT min(n) AS min_current_train_items FROM
        (SELECT user_id, count(*) AS n FROM train_pairs GROUP BY user_id)"""))
    cohort.update(one(con, """SELECT count(*) AS within_cohort_hash_rank_mismatches FROM
        (SELECT user_index, row_number() OVER (ORDER BY md5(cast(user_id AS VARCHAR)||':42')) AS rank
        FROM cohort) WHERE user_index<>rank"""))
    # 기존 로컬 analyze_window_audit.py의 표현을 재현해 차이를 검증한다.
    # 이 규칙으로 현재 표본을 재선정하거나 새 전처리 규칙을 바꾸지 않는다.
    con.execute(r"CREATE MACRO legacy_name(s) AS nfc_normalize(regexp_replace(trim(s), '\s+', ' ', 'g'))")
    con.execute("""CREATE TABLE legacy_events AS SELECT DISTINCT user_id, listened_at,
        legacy_name(artist_name) AS artist_name, legacy_name(recording_name) AS recording_name
        FROM raw WHERE user_id IS NOT NULL AND listened_at IS NOT NULL
        AND listened_at >= TIMESTAMP '2026-08-17' AND listened_at < TIMESTAMP '2026-10-12'
        AND artist_name IS NOT NULL AND recording_name IS NOT NULL
        AND legacy_name(artist_name)<>'' AND legacy_name(recording_name)<>''""")
    historical = one(con, """SELECT count(*) AS legacy_events,
        count(DISTINCT (artist_name,recording_name)) AS legacy_items,
        count(*) FILTER (WHERE listened_at < TIMESTAMP '2026-09-28') AS legacy_train_events,
        count(DISTINCT (artist_name,recording_name)) FILTER (WHERE listened_at < TIMESTAMP '2026-09-28') AS legacy_train_items,
        count(DISTINCT (user_id,artist_name,recording_name)) FILTER (WHERE listened_at < TIMESTAMP '2026-09-28') AS legacy_train_pairs
        FROM legacy_events""")
    historical.update(one(con, """SELECT count(*) AS legacy_cohort_item_count_mismatches FROM cohort c
        LEFT JOIN (SELECT user_id, count(DISTINCT (artist_name,recording_name)) AS n FROM legacy_events
        WHERE listened_at < TIMESTAMP '2026-09-28' GROUP BY user_id) t USING(user_id)
        WHERE coalesce(n,0)<>train_unique_tracks"""))
    historical["current_minus_legacy_events"] = diag["events"]-historical["legacy_events"]
    historical["current_minus_legacy_items"] = normalized_items["items"]-historical["legacy_items"]
    legacy_difference_samples = rows(con, f"""SELECT artist_name AS raw_artist, recording_name AS raw_recording,
        legacy_name(artist_name) AS legacy_artist, legacy_name(recording_name) AS legacy_recording,
        norm_name(artist_name) AS current_artist, norm_name(recording_name) AS current_recording,
        count(*) AS events FROM raw WHERE legacy_name(artist_name)<>norm_name(artist_name)
        OR legacy_name(recording_name)<>norm_name(recording_name) GROUP BY ALL
        ORDER BY events DESC, raw_artist, raw_recording LIMIT {sample_size}""")
    cohort_delta_samples = rows(con, f"""SELECT c.user_index AS audit_user_index, c.train_unique_tracks AS csv_items,
        t.n AS current_items, t.n-c.train_unique_tracks AS difference FROM cohort c JOIN
        (SELECT user_id, count(*) AS n FROM train_pairs GROUP BY user_id) t USING(user_id)
        WHERE t.n<>c.train_unique_tracks ORDER BY c.user_index LIMIT {sample_size}""")
    log("[audit] 내용·이름·활동량의 제한 표본 점검", flush=True)
    names = rows(con, f"""SELECT artist_name AS raw_artist, recording_name AS raw_recording,
        norm_name(artist_name) AS normalized_artist, norm_name(recording_name) AS normalized_recording,
        count(*) AS raw_events FROM raw
        WHERE artist_name <> norm_name(artist_name) OR recording_name <> norm_name(recording_name)
        GROUP BY ALL ORDER BY raw_events DESC, raw_artist, raw_recording LIMIT {sample_size}""")
    duplicate_samples = rows(con, f"""SELECT c.user_index AS audit_user_index, d.listened_at,
        d.artist_name, d.recording_name, d.multiplicity FROM duplicate_groups d JOIN cohort c USING(user_id)
        WHERE multiplicity > 1 ORDER BY multiplicity DESC, c.user_index, listened_at,
        artist_name, recording_name LIMIT {sample_size}""")
    repeats = one(con, """SELECT count(*) FILTER (WHERE listens > 1) AS repeated_train_user_item_pairs,
        coalesce(sum(listens - 1),0) AS train_repeat_events_beyond_first,
        max(listens) AS max_train_pair_listens FROM train_pairs""")
    repeat_samples = rows(con, f"""SELECT c.user_index AS audit_user_index, t.artist_name, t.recording_name,
        t.listens FROM train_pairs t JOIN cohort c USING(user_id) WHERE listens>1
        ORDER BY listens DESC, c.user_index, artist_name, recording_name LIMIT {sample_size}""")
    con.execute("""CREATE TABLE user_days AS SELECT user_id, cast(listened_at AS DATE) AS day,
        count(*) AS events, count(DISTINCT (artist_name, recording_name)) AS items,
        count(DISTINCT listened_at) AS timestamps FROM events GROUP BY ALL""")
    activity = one(con, """SELECT min(events) AS min_active_day_events,
        quantile_cont(events,0.5) AS median_active_day_events,
        quantile_cont(events,0.99) AS p99_active_day_events,
        max(events) AS max_active_day_events, count(*) AS active_user_days FROM user_days""")
    activity.update(one(con, """SELECT min(n) AS min_user_events, quantile_cont(n,0.5) AS median_user_events,
        quantile_cont(n,0.95) AS p95_user_events, max(n) AS max_user_events FROM
        (SELECT user_id, count(*) AS n FROM events GROUP BY user_id)"""))
    activity_samples = rows(con, f"""SELECT c.user_index AS audit_user_index, d.day, d.events, d.items,
        d.timestamps FROM user_days d JOIN cohort c USING(user_id)
        ORDER BY d.events DESC, c.user_index, d.day LIMIT {sample_size}""")
    # 문자열 검색은 진단 신호다. 제목 속 podcast 등은 음악에도 존재하므로 삭제 근거가 아니다.
    suspicious_pattern = "podcast|audiobook|audio book|chapter [0-9]|episode [0-9]|팟캐스트|오디오북"
    suspicious = one(con, """SELECT count(*) AS events, count(DISTINCT user_id) AS users,
        count(DISTINCT (artist_name,recording_name)) AS items FROM events
        WHERE regexp_matches(lower(artist_name || ' ' || recording_name), ?)""", [suspicious_pattern])
    suspicious_samples = rows(con, f"""SELECT artist_name, recording_name, count(*) AS events FROM events
        WHERE regexp_matches(lower(artist_name || ' ' || recording_name), ?)
        GROUP BY ALL ORDER BY events DESC, artist_name, recording_name LIMIT {sample_size}""", [suspicious_pattern])
    # 소문자화했을 때만 일치하는 서로 다른 튜플을 확인하되 실제 키는 변경하지 않는다.
    case_samples = rows(con, f"""WITH variants AS (SELECT DISTINCT artist_name, recording_name FROM events)
        SELECT lower(artist_name) AS folded_artist, lower(recording_name) AS folded_recording,
        count(*) AS variants, min(artist_name || ' / ' || recording_name) AS first_example,
        max(artist_name || ' / ' || recording_name) AS last_example FROM variants
        GROUP BY lower(artist_name),lower(recording_name) HAVING count(*) > 1
        ORDER BY variants DESC, folded_artist, folded_recording LIMIT {sample_size}""")
    log("[audit] 평가 대상 제외 진단", flush=True)
    evaluation = {}
    for split, start, end in [("validation", TRAIN_END, VALIDATION_END), ("test", VALIDATION_END, WINDOW_END)]:
        con.execute("CREATE OR REPLACE TABLE period AS SELECT * FROM events WHERE listened_at >= ?::TIMESTAMP AND listened_at < ?::TIMESTAMP", [start,end])
        evaluation[split] = one(con, """SELECT count(*) AS events, count(DISTINCT user_id) AS active_users,
            count(DISTINCT (user_id,artist_name,recording_name)) AS user_item_pairs FROM period""")
        for label, condition in [
            ("new_item", "NOT EXISTS (SELECT 1 FROM catalog i WHERE i.artist_name=p.artist_name AND i.recording_name=p.recording_name)"),
            ("seen_in_train", "EXISTS (SELECT 1 FROM train_pairs t WHERE t.user_id=p.user_id AND t.artist_name=p.artist_name AND t.recording_name=p.recording_name)"),
        ]:
            stats = one(con, f"""SELECT count(*) AS events, count(DISTINCT user_id) AS users,
                count(DISTINCT (artist_name,recording_name)) AS items,
                count(DISTINCT (user_id,artist_name,recording_name)) AS pairs FROM period p WHERE {condition}""")
            evaluation[split][label] = stats
        con.execute("""CREATE OR REPLACE TABLE targets AS SELECT DISTINCT p.user_id, p.artist_name, p.recording_name
            FROM period p SEMI JOIN catalog i USING(artist_name,recording_name)
            ANTI JOIN train_pairs t USING(user_id,artist_name,recording_name)""")
        evaluation[split].update(one(con, """WITH sizes AS (SELECT c.user_id,
            count(t.artist_name) AS history_items, (SELECT count(*) FROM catalog) - count(t.artist_name) AS candidates
            FROM cohort c LEFT JOIN train_pairs t USING(user_id) GROUP BY c.user_id),
            target_counts AS (SELECT user_id, count(*) AS n FROM targets GROUP BY user_id)
            SELECT count(*) FILTER (WHERE history_items>0 AND coalesce(n,0)>0 AND candidates>=10) AS eligible_users,
            coalesce(sum(n) FILTER (WHERE history_items>0 AND candidates>=10),0) AS target_pairs,
            count(*) FILTER (WHERE history_items=0) AS no_train_users,
            count(*) FILTER (WHERE history_items>0 AND coalesce(n,0)=0) AS no_target_users,
            count(*) FILTER (WHERE history_items>0 AND candidates<10) AS insufficient_candidate_users,
            min(candidates) FILTER (WHERE history_items>0) AS min_candidate_count
            FROM sizes LEFT JOIN target_counts USING(user_id)"""))
    daily = rows(con, """SELECT cast(listened_at AS DATE) AS date, count(*) AS events,
        count(DISTINCT user_id) AS users FROM events GROUP BY date ORDER BY date""")
    return dict(schema=schema, raw=basic, nulls=missing, strings=strings, cohort=cohort,
                raw_partitions=raw_parts, raw_exact=raw_exact, raw_items=raw_items,
                diagnostic=diag, diagnostic_items=normalized_items, diagnostic_partitions=partitions,
                train=train, repeats=repeats, activity=activity, evaluation=evaluation, daily=daily,
                historical_comparison=historical,
                samples=dict(names=names, duplicates=duplicate_samples, repeats=repeat_samples,
                             high_activity=activity_samples, content_signal=suspicious_samples,
                             case_variants=case_samples,legacy_rule_differences=legacy_difference_samples,
                             cohort_count_differences=cohort_delta_samples),
                content_signal={"pattern": suspicious_pattern, **suspicious})


def render_report(result: dict) -> str:
    """데이터 점검 결과를 표로 렌더한다. 표의 JSON 문자열은 줄바꿈/HTML을 이스케이프한다."""
    def table(records):
        if isinstance(records, dict):
            records = [{"항목":k,"값":v} for k,v in records.items()]
        if not records:
            return "관측 사례 없음.\n"
        keys = list(records[0])
        def cell(value):
            text = json.dumps(value,ensure_ascii=False,default=str) if not isinstance(value,(int,float)) else str(value)
            return text.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace("|","&#124;")
        return "| " + " | ".join(keys) + " |\n| " + " | ".join("---" for _ in keys) + " |\n" + "\n".join(
            "| " + " | ".join(cell(r.get(k)) for k in keys) + " |" for r in records) + "\n"
    lines = ["# 원본 데이터 점검 결과\n",
        "이 문서는 `scripts/audit_raw_data.py`의 실제 실행 결과다. 원본 통계와 고정 규칙을 적용한 진단 집계를 기록한다. 모든 날짜는 UTC다.\n",
        "## 검증 범위와 근거\n",
        "원본 전체를 읽어 스키마·통계와 입력 파일의 실행 전후 무결성을 검사했다. 이름 정규화는 NFC와 고정된 유니코드 공백 29종의 축약이다. 결측·빈 이름·범위 밖 행을 진단 집계에서 분리하고 동일 사용자·시각·정규화 튜플은 한 건으로 집계했다. 원본은 수정하지 않았다.\n",
        "부속 README·source_manifest·기존 검증 JSON의 추출 방법 및 공식 덤프 규모는 **기존 설명에 기록된 사실**이다. 원본 TAR, 20,284명 전체 후보군, 표본의 상위 5,000명 선정 순위는 다시 검증하지 않았다. 로컬 CSV와 현재 학습 이력의 일치 여부만 직접 검증했다. TIMESTAMP에는 시간대가 내장되어 있지 않아 UTC 해석은 부속 자료의 명세를 따른다.\n"]
    for title,key in [("컬럼과 실제 자료형","schema"),("원본 전체 통계","raw"),("결측 수","nulls"),
                      ("빈 문자열과 이름 정규화 영향","strings"),("선정 사용자 CSV 대조","cohort"),
                      ("원본 기간별 규모","raw_partitions"),("원문 키 중복 후보","raw_exact"),
                      ("원문 이름 튜플 수","raw_items"),("정규화 후 중복 후보 진단","diagnostic"),
                      ("정규화 후 전체 이름 튜플 수","diagnostic_items"),("진단 집계의 기간별 규모","diagnostic_partitions"),
                      ("진단 집계의 학습 카탈로그","train"),("과거 규칙 재현과 차이 확인","historical_comparison"),
                      ("다른 시각의 반복 청취","repeats"),
                      ("청취량 분포","activity"),("비음악 콘텐츠 의심 문자열 신호","content_signal")]:
        lines.extend([f"## {title}\n",table(result[key])])
    lines.append("## 평가 데이터 제외 규모 진단\n\n성능 지표를 계산한 것이 아니다. 신규 곡과 학습 기청취곡은 서로 겹치지 않는다. 각 범주의 사용자 수는 서로 겹칠 수 있으므로 합산하지 않는다. no_target_users는 비활동 사용자를 포함한 원래 표본 기준이며 후보 부족과는 별도 조건이다.\n")
    for split,metrics in result["evaluation"].items():
        lines.extend([f"### {split}\n",table(metrics)])
    lines.append("## 제한된 표본 점검\n\n각 범주에서 최대 설정 sample_size개를 빈도 내림차순과 명시적 동률 순서로 선정했다. 무작위 대표 표본이 아니다. audit_user_index는 기존 CSV의 1부터 시작하는 표시 ID이며 공통 user_idx와 다르다. 제목 문자열은 JSON 이스케이프로 공백·제어문자를 표시한다.\n")
    for label,records in result["samples"].items():
        lines.extend([f"### {label}\n",table(records)])
    lines.extend(["## 해석과 한계\n",
        "과거 규칙은 `NFC(regexp_replace(trim(s), '\\s+', ' ', 'g'))`이며 로컬 기존 분석 스크립트에서 확인했다. 이를 같은 파일에 재적용한 수치와 현재 규칙을 위에서 대조한다. RE2의 `\\s`가 정리하지 않는 유니코드 공백과 trim 적용 순서의 차이가 있으므로 CSV의 train_unique_tracks를 새 규칙의 기대값으로 강제하지 않는다. 사용자 선정 순서는 유지한다.\n",
        "동일 사용자·동일 시각·동일 정규화 튜플의 초과 행만 중복 제거 대상으로 삼는다. 다른 시각의 반복은 실제 선호 신호일 수 있어 보존한다. 대소문자 차이나 라이브·리믹스·리마스터 표기는 의미가 다를 수 있어 자동 병합하지 않는다.\n",
        "과도한 청취량과 podcast·audiobook 등의 문자열은 검토 신호일 뿐이다. 네 컬럼만으로 재생 길이, 실제 사람의 청취, 음악 여부를 판정할 수 없으므로 해당 신호로 자동 삭제하지 않는다. 동명 곡의 오병합과 표기 차이의 과분리는 남는다.\n",
        "모델 입력의 ID·청취 횟수·제외 사유·평가 정답은 패키지 생성과 독립 검증에서 대조한다. source_manifest는 공식 TAR의 출처를 기록한다.\n",
        "## 진단 일별 규모\n",table(result["daily"]),
        "규칙과 전달 규격은 `docs/preprocessing-rules.md`, `docs/data-contract.md`를 따른다. 원본 사용자 식별자는 보고서에 싣지 않았다.\n"])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",type=Path,default=raw_inputs.DEFAULT_CONFIG,help="원본과 작업 위치 설정")
    args = parser.parse_args()
    config_path = args.config.resolve()
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    def resolve(key):
        return (config_path.parent / Path(config[key]).expanduser()).resolve()
    raw_dir, report_dir, cache_dir = (resolve(k) for k in ("raw_dir","report_dir","cache_dir"))
    # 원본 아래에는 보고서나 임시파일을 만들지 않도록 경로를 먼저 검증한다.
    for destination in (report_dir,cache_dir):
        if destination == raw_dir or raw_dir in destination.parents:
            raise ValueError("출력·cache 폴더는 raw_dir 밖에 지정해야 합니다")
    sample_size = config.get("sample_size",5)
    threads = config.get("threads",2)
    if not isinstance(sample_size,int) or not 1 <= sample_size <= 20:
        raise ValueError("sample_size는 1부터 20 사이여야 합니다")
    if not isinstance(threads,int) or not 1 <= threads <= 4:
        raise ValueError("threads는 1부터 4 사이여야 합니다")
    started = time.monotonic()
    import preprocess_events as inputs
    reference = inputs.read_reference(raw_inputs.resolve_reference(raw_dir, inputs.REFERENCE))
    before = {entry["file"]: entry["sha256"] for entry in inputs.verify_input_files(raw_dir, reference)}
    expected_hash = config.get("expected_sha256")
    if expected_hash and REQUIRED_FILES[0] in before and before[REQUIRED_FILES[0]] != expected_hash:
        raise ValueError("원본 Parquet SHA256이 기준과 다릅니다")
    cache_dir.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="audit-",dir=cache_dir) as work:
        con = duckdb.connect(str(Path(work)/"diagnostic.duckdb"))
        try:
            con.execute("SET memory_limit = ?",[config.get("memory_limit","3GB")])
            con.execute("SET threads = ?",[threads])
            con.execute("SET TimeZone = 'UTC'")
            con.execute("SET preserve_insertion_order = false")
            con.execute("SET temp_directory = ?",[str(Path(work)/"spill")])
            con.execute("SET max_temp_directory_size = '12GiB'")
            result = audit(con,raw_dir,sample_size)
        finally:
            con.close()
    after = {name:sha256(raw_dir/name) for name in raw_inputs.required_files(raw_dir)}
    if before != after:
        raise RuntimeError("점검 중 입력 파일이 변경돼 보고서 생성을 중단했습니다")
    result["input_files"] = [{"file":name,"bytes":(raw_dir/name).stat().st_size,
                              "sha256":digest,"unchanged":before[name]==after[name]}
                             for name,digest in before.items()]
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    result["execution"] = dict(started_note="finished_utc는 보고서 생성 시각",
        finished_utc=datetime.now(timezone.utc).isoformat(),python=platform.python_version(),
        duckdb=duckdb.__version__,platform=platform.system()+" "+platform.machine(),
        memory_limit=config.get("memory_limit","3GB"),threads=threads,sample_size=sample_size,
        elapsed_seconds=round(time.monotonic()-started,2),
        peak_rss_bytes=rss if sys.platform=="darwin" else rss*1024,
        normalization="NFC 후 고정 공백 codepoint 29종을 ASCII 공백으로 축약하고 양끝 공백 제거",
        python_unicode_version=unicodedata.unidata_version,
        whitespace_codepoints=[f"U+{c:04X}" for c in WHITESPACE],
        final_preprocessing_files_created=False)
    result["code_trace"] = git_trace(Path(__file__).resolve().parents[1])
    report_dir.mkdir(parents=True,exist_ok=True)
    for filename,content in [("raw-data-audit.json",json.dumps(result,ensure_ascii=False,indent=2,default=str)+"\n"),
                             ("raw-data-audit.md",render_report(result))]:
        (report_dir/filename).write_text(content,encoding="utf-8")
    print(json.dumps({"raw":result["raw"],"diagnostic":result["diagnostic"],
                      "execution":result["execution"]},ensure_ascii=False,default=str),flush=True)


if __name__ == "__main__":
    main()
