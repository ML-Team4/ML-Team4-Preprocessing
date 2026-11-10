"""EDA 산출물을 별도 SQL 집계·그림 명세·보고서 및 선택적 재실행과 대조한다."""
from __future__ import annotations

import argparse
import csv
import json
import math
import tempfile
from pathlib import Path

import duckdb

import analyze_listening as eda

REPORT = eda.ROOT / "reports/listening-eda.md"


def keyed(rows: list[dict], fields: tuple[str, ...], expected: set[tuple], label: str) -> dict:
    """행 순서 대신 의미 키를 쓰며 누락·중복·잘못된 기간과 범주를 거부한다."""
    result = {}
    for row in rows:
        key = tuple(row[field] for field in fields)
        eda.require(key not in result, f"{label} 중복 범주: {key}")
        result[key] = row
    eda.require(set(result) == expected,
                f"{label} 범주 불일치: 누락={sorted(expected - result.keys())}, "
                f"초과={sorted(result.keys() - expected)}")
    return result


def report_table(report: Path, headers: list[str], expected: dict[str, list[str]]) -> None:
    """표의 기간·단위·분모와 표시 자릿수를 포함한 모든 셀을 대조한다."""
    matches = [table for table in eda.markdown_tables(report) if table[0] == headers]
    eda.require(len(matches) == 1, f"보고서 표 머리글·기간·단위·분모 불일치: {headers[0]}")
    actual = {}
    for row in matches[0][1:]:
        eda.require(len(row) == len(headers) and row[0] not in actual,
                    f"보고서 표 중복 범주·열 수 불일치: {headers[0]} {row[0]}")
        actual[row[0]] = row[1:]
    eda.require(set(actual) == set(expected), f"보고서 표 범주 누락·초과: {headers[0]}")
    for key, cells in expected.items():
        eda.require(actual[key] == cells, f"보고서 표 수치·비율·단위 불일치: {headers[0]} / {key}")


def verify_evaluation_report(output: Path, analysis: dict, report: Path) -> None:
    """평가 사용자·정답 요약·구간·사건과 고유 쌍을 집계표에 대조한다."""
    reasons = {"no_train": "train 이력 없음", "no_period_activity": "기간 활동 없음",
               "no_target": "활동은 있으나 후보 정답 없음",
               "fewer_than_10_candidates": "정답은 있으나 후보 10개 미만", "eligible": "평가 가능"}
    periods = eda.PERIODS
    users = keyed(read_table(output, "evaluation_users"), ("period", "status"),
                  {(p, r) for p in periods for r in reasons}, "평가 사용자 표")
    cohorts = {int(r["cohort_denominator"]) for r in users.values()}
    eda.require(len(cohorts) == 1, "평가 사용자 분모 불일치")
    cohort = cohorts.pop()
    expected = {}
    for reason, label in reasons.items():
        cells = []
        for period in periods:
            row = users[period, reason]
            value = f"{int(row['users']):,}명"
            if reason not in ("no_train", "fewer_than_10_candidates"):
                value += f"({float(row['cohort_share_pct']):.2f}%)"
            cells.append(value)
        expected[label] = cells
    report_table(report, [f"표본 {cohort:,}명에 대한 배타적 사용자 상태", *periods], expected)
    summary = keyed(read_table(output, "target_summary"), ("population", "metric"),
                    {(p, "target_tracks") for p in periods}, "정답 요약 표")
    def values(keys, spec):
        return [" / ".join(format(float(summary[p, "target_tracks"][k]), spec) for k in keys) for p in periods]
    report_table(report, ["평가 대상 사용자별 정답 수(고유곡/명)", *[
        f"{p} {int(summary[p, 'target_tracks']['users']):,}명" for p in periods]], {
        "전체 정답 쌍": [f"{analysis['evaluation'][p]['target_pairs']:,}쌍" for p in periods],
        "최소 / 중앙값 / 최대": values(("min", "median", "max"), ",.0f"),
        "평균": values(("mean",), ".2f"),
        "25분위수 / 75분위수": values(("p25", "p75"), ",.0f"),
        "95분위수": values(("p95",), ".2f")})
    band_labels = {"1": "1개", "2-9": "2~9개", "10-49": "10~49개",
                   "50-99": "50~99개", "100+": "100개 이상"}
    bands = keyed(read_table(output, "target_count_bands"), ("period", "target_count_band"),
                  {(p, b) for p in periods for b in band_labels}, "정답 구간 표")
    report_table(report, ["사용자별 정답 수 구간", *[f"{p} 사용자 수·평가 사용자 대비" for p in periods]], {
        label: [f"{int(bands[p, band]['users']):,}명 / {float(bands[p, band]['share_pct']):.2f}%"
                for p in periods] for band, label in band_labels.items()})
    kind_labels = {"new_item": "신규곡 제외", "train_seen": "train 기청취곡 제외",
                   "known_item_no_train_user": "학습 사용자 없는 기지곡", "candidate": "후보 교집합"}
    ledger = keyed(read_table(output, "evaluation_pair_accounting"), ("period", "kind"),
                   {(p, k) for p in periods for k in kind_labels}, "평가 사건·쌍 표")
    expected = {}
    for p in periods:
        for kind, label in kind_labels.items():
            r = ledger[p, kind]
            expected[f"{p} {label}"] = [f"{int(r['events']):,}건 / {float(r['event_share_pct']):.2f}%",
                f"{int(r['user_item_pairs']):,}쌍 / {float(r['pair_share_pct']):.2f}%"]
        r = ledger[p, "candidate"]
        expected[f"{p} 전체"] = [f"{int(r['period_events']):,}건 / 100.00%",
                                   f"{int(r['period_pairs']):,}쌍 / 100.00%"]
    report_table(report, ["기간·구분", "청취 사건 / 기간 전체 사건 대비",
                         "고유 사용자·곡 쌍 / 기간 전체 쌍 대비"], expected)


def verify_evaluation_tables(con, package: Path, output: Path, analysis: dict, manifest: dict) -> None:
    """정답은 별도 SQL, 시각 없는 제외 집계는 고정된 패키지 manifest에 대조한다."""
    reasons = ("no_train", "no_period_activity", "no_target", "fewer_than_10_candidates", "eligible")
    kinds = ("new_item", "train_seen", "known_item_no_train_user", "candidate")
    user_rows = keyed(read_table(output, "evaluation_users"), ("period", "status"),
                      {(p, r) for p in eda.PERIODS for r in reasons}, "평가 사용자 표")
    pair_rows = keyed(read_table(output, "evaluation_pair_accounting"), ("period", "kind"),
                      {(p, k) for p in eda.PERIODS for k in kinds}, "평가 사건·쌍 표")
    labels = {"1": "= 1", "2-9": "BETWEEN 2 AND 9", "10-49": "BETWEEN 10 AND 49",
              "50-99": "BETWEEN 50 AND 99", "100+": ">= 100"}
    band_rows = keyed(read_table(output, "target_count_bands"), ("period", "target_count_band"),
                      {(p, b) for p in eda.PERIODS for b in labels}, "정답 구간 표")
    for p in eda.PERIODS:
        entry = manifest["evaluation"]["periods"][p]
        count, distinct, eligible = con.execute(f"SELECT count(*), count(DISTINCT (user_idx, item_idx)), "
                                               f"count(DISTINCT user_idx) FROM {p}_targets").fetchone()
        eda.require(count == distinct == entry["target_pairs"] and eligible == entry["eligible_users"],
                    f"별도 SQL 평가 정답·사용자 집계 불일치: {p}")
        con.read_csv(str(package / f"{p}_users.csv"), header=True,
                     columns={"user_idx": "INTEGER"}).create_view(f"{p}_users")
        difference = con.execute(f"""SELECT count(*) FROM (
            (SELECT user_idx FROM {p}_users EXCEPT ALL SELECT user_idx FROM {p}_counts)
            UNION ALL (SELECT user_idx FROM {p}_counts EXCEPT ALL SELECT user_idx FROM {p}_users))""").fetchone()[0]
        seen = con.execute(f"SELECT count(*) FROM {p}_targets JOIN train USING (user_idx, item_idx)").fetchone()[0]
        invalid = con.execute(f"""SELECT count(*) FROM {p}_targets t LEFT JOIN user_counts u USING(user_idx)
            WHERE u.user_idx IS NULL OR t.item_idx < 0 OR t.item_idx >= ? OR ? - u.unique_tracks < 10""",
            [analysis["train"]["items"]] * 2).fetchone()[0]
        eda.require(difference == seen == invalid == 0, f"별도 SQL 평가 사용자·후보·기청취곡 불일치: {p}")
        saved = analysis["evaluation"][p]
        eda.require(saved["target_pairs"] == count and saved["eligible_users"] == eligible,
                    f"분석 manifest 평가 집계 불일치: {p}")
        cohort = entry["user_denominator_selected_cohort"]
        for reason in reasons:
            row = user_rows[p, reason]
            users = eligible if reason == "eligible" else entry["user_exclusions"][reason]
            eda.require(int(row["users"]) == users and int(row["cohort_denominator"]) == cohort
                        and math.isclose(float(row["cohort_share_pct"]), 100 * users / cohort),
                        f"평가 사용자·제외 사유·분모 불일치: {p} {reason}")
        for band, condition in labels.items():
            users = con.execute(f"SELECT count(*) FROM {p}_counts WHERE target_tracks {condition}").fetchone()[0]
            row = band_rows[p, band]
            eda.require(int(row["users"]) == users and int(row["eligible_denominator"]) == eligible
                        and math.isclose(float(row["share_pct"]), 100 * users / eligible),
                        f"별도 SQL 정답 구간·분모 불일치: {p} {band}")
        pairs = sum(r["user_item_pairs"] for r in entry["period_pairs_by_kind"].values())
        for kind in kinds:
            row, expected = pair_rows[p, kind], entry["period_pairs_by_kind"][kind]
            eda.require(all(int(row[k]) == v for k, v in expected.items())
                        and int(row["period_events"]) == entry["clean_events"] and int(row["period_pairs"]) == pairs
                        and math.isclose(float(row["event_share_pct"]), 100 * expected["events"] / entry["clean_events"])
                        and math.isclose(float(row["pair_share_pct"]), 100 * expected["user_item_pairs"] / pairs),
                        f"평가 사건·고유 쌍·분모 불일치: {p} {kind}")


def read_table(output: Path, name: str) -> list[dict]:
    path = output / "tables" / f"{name}.csv"
    with path.open("rb") as stream:
        eda.require(stream.read(3) == b"\xef\xbb\xbf", f"집계표 BOM 누락: {name}")
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    eda.require(all(r["dataset_version"] == eda.VERSION for r in rows), f"집계 버전 불일치: {name}")
    return rows


def verify_contents(package: Path, output: Path, reproduce: bool) -> dict:
    package, output = package.resolve(), output.resolve()
    manifest = eda.check_package_reference(package)
    eda.check_output_inventory(output, complete=True)
    analysis = json.loads((output / "analysis_manifest.json").read_text(encoding="utf-8"))
    eda.require(analysis["dataset_version"] == eda.VERSION, "분석 manifest 버전 불일치")
    eda.require(set(analysis["output_files"]) == eda.ARTIFACT_PATHS,
                "분석 manifest의 산출물 목록이 예정된 CSV 11개·PNG 2개와 다릅니다")
    eda.require(set(analysis["checks"]["input_fingerprints"]) == set(manifest["files"]) | {"dataset_manifest.json"},
                "분석 manifest 입력 목록 불일치")
    for name, entry in manifest["files"].items():
        eda.require(eda.fingerprint(package / name) == {k: entry[k] for k in ("bytes", "sha256")},
                    f"고정 manifest의 파일 무결성 불일치: {name}")
    for name, expected in analysis["checks"]["input_fingerprints"].items():
        eda.require(eda.fingerprint(package / name) == expected, f"분석 후 입력 변경: {name}")
    for name, expected in analysis["source_fingerprints"].items():
        eda.require(eda.fingerprint(eda.ROOT / name) == expected, f"분석 후 코드·근거 변경: {name}")
    for name, expected in analysis["output_files"].items():
        eda.require(eda.fingerprint(output / name) == expected, f"분석 후 산출물 변경: {name}")
    checks = {"fixed_package_reference_match": True, "exact_output_inventory": True,
              "input_source_output_integrity": True}
    with duckdb.connect() as con:
        con.read_csv(str(package / "train_interactions.csv"), header=True, columns={
            "user_idx": "INTEGER", "item_idx": "INTEGER", "listen_count": "BIGINT"}).create_view("train")
        con.execute("CREATE TABLE item_counts AS SELECT item_idx, count(DISTINCT user_idx) AS listeners, "
                    "sum(listen_count) AS events FROM train GROUP BY item_idx")
        pairs, unique_pairs, events, users, items = con.execute("""SELECT count(*),
            count(DISTINCT (user_idx, item_idx)), sum(listen_count), count(DISTINCT user_idx),
            count(DISTINCT item_idx) FROM train""").fetchone()
        stats = analysis["train"]
        saved = read_table(output, "dataset_summary")[0]
        eda.require(all(float(saved[key]) == value if isinstance(value, float) else int(saved[key]) == value
                        for key, value in stats.items()), "요약 CSV와 분석 manifest 불일치")
        eda.require(pairs == unique_pairs == stats["user_item_pairs"] and events == stats["events"]
                    and users == stats["users"] and items == stats["items"], "별도 SQL 학습 집계 불일치")
        eda.require(stats["matrix_density"] == pairs / (users * items)
                    and stats["repeat_events_after_first_pair"] == events - pairs, "별도 SQL 밀도·반복 사건 집계 불일치")
        for row in read_table(output, "popularity_bands"):
            condition = "listeners >= 3" if row["listener_band"] == "3+" else f"listeners = {int(row['listener_band'])}"
            tracks, pairs_in_band, events_in_band = con.execute(
                f"SELECT count(*), sum(listeners), sum(events) FROM item_counts WHERE {condition}").fetchone()
            eda.require([tracks, pairs_in_band, events_in_band] == [int(row[k]) for k in ("tracks", "user_item_pairs", "events")],
                        "별도 SQL 인기도 구간 집계 불일치")
            eda.require(math.isclose(float(row["track_share_pct"]), 100 * tracks / items)
                        and math.isclose(float(row["event_share_pct"]), 100 * events_in_band / events), "구간 분모 불일치")
        con.execute("CREATE TABLE user_counts AS SELECT user_idx, count(DISTINCT item_idx) AS unique_tracks, "
                    "sum(listen_count) AS listen_events FROM train GROUP BY user_idx")
        for row in read_table(output, "user_history_histogram"):
            count = con.execute("SELECT count(*) FROM user_counts WHERE unique_tracks >= ? AND unique_tracks < ?",
                                [int(row["lower_inclusive"]), int(row["upper_exclusive"])]).fetchone()[0]
            eda.require(count == int(row["users"]), "별도 SQL 사용자 이력 구간 불일치")
        summary_rows = read_table(output, "user_summary")
        for period in eda.PERIODS:
            con.read_csv(str(package / f"{period}_targets.csv"), header=True, columns={
                "user_idx": "INTEGER", "item_idx": "INTEGER"}).create_view(f"{period}_targets")
            con.execute(f"CREATE TABLE {period}_counts AS SELECT user_idx, count(*) AS target_tracks "
                        f"FROM {period}_targets GROUP BY user_idx")
        summary_rows += read_table(output, "target_summary")
        keyed(summary_rows, ("population", "metric"),
              {("train", "unique_tracks"), ("train", "listen_events"),
               *((p, "target_tracks") for p in eda.PERIODS)}, "분위수 표")
        for row in summary_rows:
            metric, population = row["metric"], row["population"]
            relation = "user_counts" if population == "train" else f"{population}_counts"
            eda.require(metric in ("unique_tracks", "listen_events", "target_tracks"), "알 수 없는 집계 지표")
            mean, quantiles = con.execute(f"SELECT avg({metric}), quantile_cont({metric}, "
                f"[0, .25, .5, .75, .9, .95, .99, 1]) FROM {relation}").fetchone()
            values = dict(zip(("min", "p25", "median", "p75", "p90", "p95", "p99", "max"), quantiles))
            values["mean"] = mean
            eda.require(int(row["users"]) == con.execute(f"SELECT count(*) FROM {relation}").fetchone()[0],
                        "별도 SQL 분위수 사용자 분모 불일치")
            eda.require(all(math.isclose(value, float(row[key]), rel_tol=1e-10, abs_tol=1e-8)
                            for key, value in values.items()), "별도 SQL 분위수 집계 불일치")
        verify_evaluation_tables(con, package, output, analysis, manifest)
    checks["independent_sql_counts_shares_histogram_quantiles"] = True
    checks["evaluation_counts_exclusions_target_bands_pair_accounting"] = True
    bands = read_table(output, "popularity_bands")
    hist = read_table(output, "user_history_histogram")
    figures = analysis["figure_specifications"]
    popularity = figures["popularity-shares.png"]
    eda.require(popularity["track_share_pct"] == [float(r["track_share_pct"]) for r in bands]
                and popularity["event_share_pct"] == [float(r["event_share_pct"]) for r in bands]
                and popularity["track_denominator"] == stats["items"] and popularity["event_denominator"] == stats["events"]
                and popularity["version"] == eda.VERSION and popularity["y_unit"] == "percent", "인기도 그림 명세 불일치")
    history = figures["user-training-tracks.png"]
    eda.require(history["users"] == [int(r["users"]) for r in hist] and history["user_denominator"] == stats["users"]
                and history["bins"] == [f"{int(r['lower_inclusive']):,}\u2013{int(r['upper_exclusive']) - 1:,}" for r in hist]
                and history["version"] == eda.VERSION and history["y_unit"] == "users", "사용자 그림 명세 불일치")
    report = "reports/listening-eda.md"
    band_rows = keyed(bands, ("listener_band",), {("1",), ("2",), ("3+",)}, "인기도 표")
    expected = {}
    for band, label in (("1", "1명"), ("2", "2명"), ("3+", "3명 이상")):
        row = band_rows[band,]
        expected[label] = [f"{int(row['tracks']):,}개", f"{float(row['track_share_pct']):.2f}%",
                           f"{int(row['user_item_pairs']):,}쌍", f"{int(row['events']):,}건",
                           f"{float(row['event_share_pct']):.2f}%"]
    expected["합계"] = [f"{stats['items']:,}개", "100.00%", f"{stats['user_item_pairs']:,}쌍",
                        f"{stats['events']:,}건", "100.00%"]
    report_table(REPORT, ["고유 train 청취자", "곡 수", "전체 학습곡 대비", "고유 학습 쌍",
                          "청취 사건", "전체 학습 사건 대비"], expected)
    periods = keyed(read_table(output, "period_activity"), ("period",),
                    {(p,) for p in ("train", *eda.PERIODS)}, "기간 활동 표")
    from datetime import datetime, timedelta
    expected = {}
    for (p,), row in periods.items():
        end = datetime.fromisoformat(row['end_exclusive_utc']) - timedelta(days=1)
        expected[p] = [f"{row['start_inclusive_utc'][5:10]}~{end:%m-%d}", f"{int(row['observed_days'])}일",
                       f"{int(row['active_users']):,}명 / {float(row['active_user_share_pct']):.2f}%",
                       f"{int(row['events']):,}건", f"{float(row['events_per_day']):,.2f}"]
    report_table(REPORT, ["구간", "UTC 포함 날짜(2026년)", "실제 관측 날짜 수",
                          f"활동 사용자 / 표본 {stats['users']:,}명", "사건 합계", "일평균 사건(건/관측일)"], expected)
    checks["figure_values_denominators_units_versions_and_report_tables"] = True
    verify_evaluation_report(output, analysis, REPORT)
    checks["all_evaluation_report_tables_periods_categories_units_denominators_rounding"] = True
    if reproduce:
        with tempfile.TemporaryDirectory(prefix="listening-reproduce-") as folder:
            fresh = eda.analyze(package, Path(folder))
            eda.require(fresh == analysis, "재실행 집계·그림·내부 manifest가 다릅니다")
        checks["reproduced_tables_figures_and_analysis_manifest"] = True
    result = {"matches": all(checks.values()), "status": "passed", "dataset_version": eda.VERSION,
              "checks": checks, "mismatches": 0,
              "verification_sources": {name: eda.fingerprint(eda.ROOT / name) for name in
                                       (report, "scripts/verify_listening_analysis.py")},
              "analysis_manifest_fingerprint": eda.fingerprint(output / "analysis_manifest.json"),
              "scope": "CSV 별도 SQL 집계·집계표·그림 명세·보고서 표; 그림 배치는 별도 육안 검사"}
    return result


def verify(package: Path, output: Path, reproduce: bool) -> dict:
    """검사 시작·실패·성공을 기록하고 실패 시 기존 성공 기록을 대체한다."""
    package, output = package.resolve(), output.resolve()
    eda.require(not output.is_relative_to(package) and not package.is_relative_to(output), "검증 출력과 입력 패키지 경로가 겹칩니다")
    eda.require(output.is_dir(), "검사할 EDA 결과 폴더가 없습니다")
    eda.write_verification_record(output, {"status": "checking", "matches": None})
    try:
        result = verify_contents(package, output, reproduce)
    except Exception as error:
        eda.write_verification_record(output, {"status": "failed", "matches": False,
            "mismatches": None, "error": str(error), "scope": "검증을 중단했으므로 불일치 총수는 확정하지 않음"})
        raise
    eda.write_verification_record(output, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", default=Path("datasets/listenbrainz-preprocessing"), type=Path, help="분석에 사용한 CSV 패키지")
    parser.add_argument("--output-dir", default=eda.ROOT / "reports/eda", type=Path, help="검사할 EDA 결과 폴더")
    parser.add_argument("--reproduce", action="store_true", help="별도 경로에서 재실행해 모든 생성 결과를 대조")
    args = parser.parse_args()
    result = verify(args.package, args.output_dir, args.reproduce)
    print(json.dumps({key: value for key, value in result.items()
                      if key not in ("verification_sources", "analysis_manifest_fingerprint")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
