"""검증된 CSV 패키지의 학습 인기도·사용자 분포와 평가 범위를 집계한다."""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pyarrow as pa

from audit_raw_data import sha256
from build_dataset import read_csv_batches
from validate_dataset import SCHEMAS

ROOT = Path(__file__).resolve().parents[1]
VERSION = "listenbrainz-preprocessing"
PERIODS = ("validation", "test")
BOUNDS = ("2026-08-17 00:00:00", "2026-09-28 00:00:00",
          "2026-10-05 00:00:00", "2026-10-12 00:00:00")
SOURCES = ("reports/csv-dataset-validation.md", "reports/temporal-split-review.md",
           "reports/model-readiness-review.md")
REFERENCE = ROOT / "config/listening-package-reference.json"
TABLE_NAMES = ("dataset_summary", "deduplication_impact", "evaluation_pair_accounting",
               "evaluation_users", "normalization_impact", "period_activity", "popularity_bands",
               "target_count_bands", "target_summary", "user_history_histogram", "user_summary")
ARTIFACT_PATHS = frozenset({f"tables/{name}.csv" for name in TABLE_NAMES} | {
    "figures/popularity-shares.png", "figures/user-training-tracks.png"})
RECORD_PATHS = frozenset({"analysis_manifest.json", "verification.json"})


def require(condition: bool, message: str) -> None:
    """불일치를 발견하면 결과를 저장하기 전에 검사를 중단한다."""
    if not condition:
        raise ValueError(message)


def fingerprint(path: Path) -> dict:
    return {"bytes": path.stat().st_size, "sha256": sha256(path)}


def check_output_inventory(output: Path, *, complete: bool) -> None:
    """고정된 예정 상대경로와 실제 파일 목록을 양방향으로 대조한다."""
    actual = {str(p.relative_to(output)) for p in output.rglob("*") if p.is_file() or p.is_symlink()}
    extra = actual - ARTIFACT_PATHS - RECORD_PATHS
    missing = (ARTIFACT_PATHS | RECORD_PATHS) - actual if complete else set()
    require(not missing and not extra,
            f"산출물 파일 목록 불일치: 누락={sorted(missing)}, 초과={sorted(extra)}")


def check_package_reference(package: Path) -> dict:
    """원본 대조 성공 기록의 고정 식별값을 적용하며 입력에서 기준을 만들지 않는다."""
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    require(reference["dataset_version"] == VERSION and reference["matches"] is True
            and bool(reference["checks"]) and all(v is True for v in reference["checks"].values()),
            "저장소 패키지 기준의 버전·원본 대조 성공 기록이 유효하지 않습니다")
    path = package / "dataset_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    require(manifest.get("dataset_version") == reference["dataset_version"],
            "고정 패키지 기준 불일치: dataset_version이 원본 검증 성공 버전과 다릅니다")
    require(sha256(path) == reference["manifest_sha256"],
            "고정 패키지 기준 불일치: dataset_manifest.json 식별값이 원본 검증 성공 기록과 다릅니다 "
            "(config/listening-package-reference.json)")
    return manifest


def write_verification_record(output: Path, result: dict) -> None:
    """최신 검증 상태를 기록해 이전 성공을 현재 결과로 오인하지 않게 한다."""
    output.mkdir(parents=True, exist_ok=True)
    (output / "verification.json").write_text(
        json.dumps({"dataset_version": VERSION, **result}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def markdown_tables(path: Path) -> list[list[list[str]]]:
    """기존 검증표를 재사용하도록 Markdown의 표 셀을 읽는다."""
    tables, current = [], []
    for line in path.read_text(encoding="utf-8").splitlines() + [""]:
        if line.startswith("|"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if not all(set(cell) <= set("-: ") for cell in cells):
                current.append(cells)
        elif current:
            tables.append(current)
            current = []
    return tables


def source_table(path: str, first_headers: list[str]) -> list[list[str]]:
    matches = [t for t in markdown_tables(ROOT / path) if t[0][:len(first_headers)] == first_headers]
    require(len(matches) == 1, f"기존 보고서의 표를 식별할 수 없습니다: {path} {first_headers}")
    return matches[0][1:]


def integer(value: str) -> int:
    return int(value.replace(",", "").replace("−", "-"))


def load_package(package: Path) -> tuple[dict, dict, dict]:
    """BOM·자료형·순서·전체 파일 무결성을 검사하고 수치 열만 보관한다."""
    manifest = check_package_reference(package)
    require(manifest["dataset_version"] == VERSION and manifest["contract_version"] == "2.0.0"
            and manifest["rules_version"] == "1.0.0", "공통 CSV의 데이터·계약·규칙 버전이 다릅니다")
    schemas = {name: (fields, order) for name, (fields, order) in SCHEMAS.items()}
    schemas.update({f"{p}_users": ([("user_idx", pa.int32())], ["user_idx"]) for p in PERIODS})
    expected_files = {f"{name}.csv" for name in schemas} | {"README.md", "COPYING"}
    require(set(manifest["files"]) == expected_files, "manifest의 CSV 7개·문서 목록이 다릅니다")
    before = {name: fingerprint(package / name) for name in sorted(expected_files | {"dataset_manifest.json"})}
    for name, entry in manifest["files"].items():
        require(before[name] == {key: entry[key] for key in ("bytes", "sha256")},
                f"manifest 무결성 불일치: {name}")
    data, file_checks = {}, {}
    for name, (fields, order) in schemas.items():
        filename = f"{name}.csv"
        path, entry = package / filename, manifest["files"][filename]
        schema = pa.schema([pa.field(key, dtype, nullable=False) for key, dtype in fields])
        expected_columns = {key: str(dtype) for key, dtype in fields}
        if name.endswith("_users"):
            expected_columns = {"user_idx": "decimal int32"}
        require(entry["columns"] == expected_columns and entry["column_order"] == schema.names
                and entry["encoding"] == "UTF-8 BOM" and entry["header"] is True
                and entry["sorted_by"] == order, f"CSV 스키마 기록 불일치: {filename}")
        with path.open("rb") as stream:
            require(stream.read(3) == b"\xef\xbb\xbf", f"BOM 누락: {filename}")
        numeric = {key: [] for key, dtype in fields if pa.types.is_integer(dtype)}
        previous, rows = -1, 0
        for batch in read_csv_batches(path, schema):
            columns = {key: batch[key].to_numpy().astype(np.int64) for key in numeric}
            require(all(np.all(columns[key] >= 0) for key in order), f"음수 ID: {filename}")
            keys = columns[order[0]]
            if len(order) == 2:
                keys = (keys << 32) + columns[order[1]]
            require(keys[0] > previous and np.all(keys[1:] > keys[:-1]),
                    f"중복 또는 정렬 오류: {filename}")
            previous = int(keys[-1])
            rows += batch.num_rows
            for key in numeric:
                numeric[key].append(columns[key])
        require(rows == entry["rows"], f"행 수 불일치: {filename}")
        data[name] = {key: np.concatenate(parts) if parts else np.array([], dtype=np.int64)
                      for key, parts in numeric.items()}
        file_checks[filename] = {"rows": rows, "integrity_schema_order": True}
    return manifest, data, {"input_fingerprints": before, "csv_checks": file_checks}


def aggregate_train(data: dict, manifest: dict) -> tuple[dict, dict, np.ndarray, np.ndarray]:
    """고유 좌표와 int64 횟수를 열 단위로 집계하며 dense matrix를 만들지 않는다."""
    n_users, n_items = len(data["users"]["user_idx"]), len(data["items"]["item_idx"])
    require(n_users > 0 and n_items > 0, "빈 학습 패키지는 이 EDA의 대상이 아닙니다")
    require(np.array_equal(data["users"]["user_idx"], np.arange(n_users))
            and np.array_equal(data["items"]["item_idx"], np.arange(n_items)), "공통 ID가 연속적이지 않습니다")
    require(np.all(np.diff(data["users"]["user_id"]) > 0), "원본 사용자 ID 순서·유일성 불일치")
    train = data["train_interactions"]
    u, i, count = (train[key] for key in ("user_idx", "item_idx", "listen_count"))
    require(np.all((u >= 0) & (u < n_users)) and np.all((i >= 0) & (i < n_items))
            and np.all(count > 0), "학습 좌표 또는 양의 정수 횟수 오류")
    keys = (u << 32) + i
    require(np.all(np.diff(keys) > 0), "학습 쌍이 중복되거나 정렬되지 않았습니다")
    user_tracks, listeners = np.bincount(u, minlength=n_users), np.bincount(i, minlength=n_items)
    user_events, item_events = np.zeros(n_users, dtype=np.int64), np.zeros(n_items, dtype=np.int64)
    np.add.at(user_events, u, count)
    np.add.at(item_events, i, count)
    require(np.all(user_tracks > 0) and np.all(listeners > 0), "학습 이력이 없는 ID가 있습니다")
    mismatches = int(np.count_nonzero(listeners != data["items"]["train_listener_count"]))
    require(mismatches == 0, "CSV 학습 쌍에서 재계산한 곡별 고유 청취자 수가 다릅니다")
    totals = {"users": n_users, "items": n_items, "user_item_pairs": len(u), "events": int(count.sum())}
    require(totals == manifest["processing"]["cleaning"]["train"], "CSV 학습 집계와 manifest가 다릅니다")
    require(manifest["mapping"]["users"] == n_users and manifest["mapping"]["items"] == n_items
            and manifest["mapping"]["dtype"] == "int32" and manifest["mapping"]["start"] == 0,
            "manifest 공통 ID 정의가 다릅니다")
    bands = []
    for label, mask in (("1", listeners == 1), ("2", listeners == 2), ("3+", listeners >= 3)):
        tracks, events = int(mask.sum()), int(item_events[mask].sum())
        bands.append({"listener_band": label, "tracks": tracks,
                      "user_item_pairs": int(listeners[mask].sum()), "events": events,
                      "track_denominator": n_items, "event_denominator": totals["events"],
                      "track_share_pct": 100 * tracks / n_items,
                      "event_share_pct": 100 * events / totals["events"]})
    stats = {**totals, "matrix_density": len(u) / (n_users * n_items),
             "listener_count_mismatches": mismatches,
             "repeat_events_after_first_pair": totals["events"] - len(u)}
    return stats, {"popularity_bands": bands}, user_tracks, user_events


def distribution(values: np.ndarray, population: str, metric: str) -> dict:
    quantiles = np.quantile(values, [0, .25, .5, .75, .9, .95, .99, 1], method="linear")
    return {"population": population, "metric": metric, "users": len(values),
            "mean": float(np.mean(values)), **dict(zip(
                ("min", "p25", "median", "p75", "p90", "p95", "p99", "max"), map(float, quantiles)))}


def histogram(values: np.ndarray, population: str) -> list[dict]:
    edges = [0, 10, 50, 100, 250, 500, 1000, 2500, 5000, 10000]
    edges.append(max(10001, int(values.max()) + 1))
    counts, _ = np.histogram(values, bins=edges)
    return [{"population": population, "lower_inclusive": low, "upper_exclusive": high,
             "users": int(count), "user_denominator": len(values), "share_pct": 100 * count / len(values)}
            for low, high, count in zip(edges[:-1], edges[1:], counts)]


def aggregate_evaluation(data: dict, manifest: dict, user_tracks: np.ndarray) -> tuple[dict, list, list, list]:
    """정답 CSV를 검사하고 시각·제외 사유는 manifest의 검증된 집계를 사용한다."""
    train = data["train_interactions"]
    train_keys = (train["user_idx"] << 32) + train["item_idx"]
    n_users, n_items = len(user_tracks), len(data["items"]["item_idx"])
    stats, summaries, excluded, target_bands = {}, [], [], []
    evaluation = manifest["evaluation"]
    require(evaluation["min_candidates"] == 10 and evaluation["min_targets"] == 1
            and manifest["processing"]["train_fixed_for_both_periods"] is True
            and evaluation["user_exclusion_priority"] == ["no_train", "no_period_activity", "no_target", "fewer_than_10_candidates"],
            "원래 train을 고정한 평가 기준이 다릅니다")
    for period in PERIODS:
        target = data[f"{period}_targets"]
        u, i = target["user_idx"], target["item_idx"]
        require(np.all((u >= 0) & (u < n_users)) and np.all((i >= 0) & (i < n_items)), "정답 ID 범위 오류")
        keys = (u << 32) + i
        require(np.all(np.diff(keys) > 0), "정답 쌍 중복·순서 오류")
        positions = np.searchsorted(train_keys, keys)
        in_bounds = positions < len(train_keys)
        seen = np.zeros(len(keys), dtype=bool)
        seen[in_bounds] = train_keys[positions[in_bounds]] == keys[in_bounds]
        require(not seen.any(), "정답에 train 기청취곡이 있습니다")
        eligible = data[f"{period}_users"]["user_idx"]
        require(np.array_equal(np.unique(u), eligible), "평가 사용자 CSV와 정답 사용자가 다릅니다")
        require(len(eligible) > 0 and np.all(n_items - user_tracks[eligible] >= 10), "평가 후보 수 미달")
        counts = np.bincount(u, minlength=n_users)[eligible]
        entry = evaluation["periods"][period]
        require(len(u) == entry["target_pairs"] and len(eligible) == entry["eligible_users"], "평가 집계 불일치")
        cohort = entry["user_denominator_selected_cohort"]
        exclusions = entry["user_exclusions"]
        require(set(exclusions) == set(evaluation["user_exclusion_priority"])
                and all(type(value) is int and value >= 0 for value in exclusions.values())
                and exclusions["no_train"] == cohort - n_users, "train 사용자 수와 제외 사유가 모순됩니다")
        require(cohort == manifest["input_validation"]["cohort"]["users"]
                and sum(exclusions.values()) + len(eligible) == cohort, "사용자 제외 집계 불일치")
        if cohort == n_users:
            partition = next(row for row in manifest["processing"]["cleaning"]["partitions"] if row["split"] == period)
            require(exclusions["no_period_activity"] == cohort - partition["users"],
                    "기간 활동 사용자 수와 제외 사유가 모순됩니다")
        kinds = entry["period_pairs_by_kind"]
        require(set(kinds) == {"new_item", "train_seen", "known_item_no_train_user", "candidate"}
                and all(set(row) == {"events", "unique_items", "user_item_pairs", "users"}
                        and all(type(value) is int and value >= 0 for value in row.values())
                        and row["events"] >= row["user_item_pairs"] >= max(row["unique_items"], row["users"])
                        for row in kinds.values()), "기간 청취 범주 집계가 모순됩니다")
        require(kinds["candidate"]["users"] == len(eligible) + exclusions["fewer_than_10_candidates"],
                "후보 사용자 수와 평가 사용자 수가 모순됩니다")
        if exclusions["fewer_than_10_candidates"] == 0:
            require(entry["candidate_pairs_excluded_for_fewer_than_10_candidates"] == 0
                    and kinds["candidate"]["unique_items"] == len(np.unique(i)), "후보 곡·추가 제외 집계 불일치")
        total_pairs = sum(row["user_item_pairs"] for row in kinds.values())
        require(sum(row["events"] for row in kinds.values()) == entry["clean_events"], "평가 사건 보존 집계 불일치")
        require(kinds["candidate"]["user_item_pairs"] - entry["candidate_pairs_excluded_for_fewer_than_10_candidates"] == len(u)
                and kinds["candidate"]["events"] - kinds["candidate"]["user_item_pairs"] == entry["candidate_repeat_events_collapsed"],
                "후보 쌍·반복 집계 불일치")
        stats[period] = {"eligible_users": len(eligible), "target_pairs": len(u),
                         "cohort_users": cohort, "eligible_pct": 100 * len(eligible) / cohort,
                         "period_unique_pairs": total_pairs, "clean_events": entry["clean_events"],
                         "candidate_repeat_events_collapsed": entry["candidate_repeat_events_collapsed"]}
        summaries.append(distribution(counts, period, "target_tracks"))
        for kind, row in kinds.items():
            excluded.append({"period": period, "kind": kind, **row, "period_events": entry["clean_events"],
                             "period_pairs": total_pairs, "event_share_pct": 100 * row["events"] / entry["clean_events"],
                             "pair_share_pct": 100 * row["user_item_pairs"] / total_pairs})
        for label, mask in (("1", counts == 1), ("2-9", (counts >= 2) & (counts <= 9)),
                            ("10-49", (counts >= 10) & (counts <= 49)),
                            ("50-99", (counts >= 50) & (counts <= 99)), ("100+", counts >= 100)):
            target_bands.append({"period": period, "target_count_band": label, "users": int(mask.sum()),
                                 "eligible_denominator": len(counts), "share_pct": 100 * mask.sum() / len(counts)})
    return stats, summaries, excluded, target_bands


def reuse_reports(manifest: dict, stats: dict, tables: dict, data: dict) -> None:
    """기존 원본 검증·정규화·중복 처리·시간 분할 표를 재사용한다."""
    csv_rows = source_table(SOURCES[0], ["파일", "CSV 행 수"])
    for name, rows, difference in csv_rows:
        require(integer(rows) == len(next(iter(data[name.removesuffix(".csv")].values())))
                and integer(difference) == 0, f"CSV 원본 대조 보고서 집계 불일치: {name}")
    keys = source_table(SOURCES[1], ["지표", "원문 키", "정규화 키"])
    tables["normalization_impact"] = [
        {"metric": label, "original_key": integer(before), "normalized_key": integer(after), "delta": integer(delta)}
        for label, before, after, delta in keys]
    normalized = [row["normalized_key"] for row in tables["normalization_impact"]]
    require(normalized == [stats["items"], stats["user_item_pairs"], tables["popularity_bands"][0]["tracks"]],
            "정규화 영향 표와 CSV 집계 불일치")
    require(all(row["normalized_key"] - row["original_key"] == row["delta"]
                for row in tables["normalization_impact"]), "정규화 전후 차이 불일치")
    dedup = source_table(SOURCES[1], ["고유 train 청취자", "곡", "처리 전 유효 행"])
    tables["deduplication_impact"] = []
    for row, band in zip(dedup[:3], tables["popularity_bands"], strict=True):
        _, tracks, before, after, removed, _ = row
        tracks, before, after, removed = map(integer, (tracks, before, after, removed))
        require(tracks == band["tracks"] and after == band["events"] and before - after == removed,
                "중복 제거 영향 표와 CSV 집계 불일치")
        tables["deduplication_impact"].append({"listener_band": band["listener_band"], "tracks": tracks,
            "valid_rows_before": before, "events_after": after, "removed_rows": removed,
            "removal_pct": 100 * removed / before})
    require([integer(v) for v in dedup[3][1:5]] == [
        sum(row[key] for row in tables["deduplication_impact"])
        for key in ("tracks", "valid_rows_before", "events_after", "removed_rows")], "중복 처리 합계 표 불일치")
    partitions = manifest["processing"]["cleaning"]["partitions"]
    period_rows = source_table(SOURCES[1], ["구간", "UTC 범위 [시작, 끝)"])
    require(len(partitions) == len(period_rows) == 3, "UTC 분할 표의 구간 수가 다릅니다")
    tables["period_activity"] = []
    for index, (entry, row) in enumerate(zip(partitions, period_rows, strict=True)):
        period, window, _, _, days, users, items, valid, events, differences = row
        start, end = BOUNDS[index:index + 2]
        require(entry["split"] == period == ("train", *PERIODS)[index]
                and entry["start_inclusive_utc"] == start and entry["end_exclusive_utc"] == end
                and window == f"{start[5:]} ~ {end[5:]}" and differences == "0 / 0", "UTC 분할 경계 불일치")
        duration = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days
        require(integer(days) == duration and integer(users) == entry["users"]
                and integer(items) == entry["items"] and integer(events) == entry["events"], "기간 집계 불일치")
        if period != "train":
            require(entry["events"] == manifest["evaluation"]["periods"][period]["clean_events"], "기간 사건 불일치")
        tables["period_activity"].append({"period": period, "start_inclusive_utc": start,
            "end_exclusive_utc": end, "observed_days": integer(days), "active_users": integer(users),
            "unique_items": integer(items), "valid_rows_before": integer(valid), "events": integer(events),
            "events_per_day": integer(events) / integer(days),
            "active_user_share_pct": 100 * integer(users) / manifest["input_validation"]["cohort"]["users"]})
    cleaning = manifest["processing"]["cleaning"]
    require(cleaning["clean_events"] == sum(row["events"] for row in tables["period_activity"])
            and cleaning["raw_rows"] == cleaning["clean_events"] + sum(cleaning["exclusions"].values())
            and cleaning["accounting_matches"] is True, "전체 정제 사건·제외 보존 집계 불일치")


def write_table(path: Path, rows: list[dict]) -> None:
    """헤더와 BOM이 있는 CSV를 저장하고 인용·개행을 포함한 값을 재대조한다."""
    require(bool(rows), f"빈 집계표: {path.name}")
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        restored = list(csv.DictReader(stream))
    require(restored == [{key: str(value) for key, value in row.items()} for row in rows],
            f"집계표 재로딩 불일치: {path.name}")


def draw_figures(output: Path, stats: dict) -> dict:
    """저장된 집계표를 다시 읽어 그래프 수치·분모와 내부 명세를 일치시킨다."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import StrMethodFormatter

    def read(name):
        with (output / "tables" / f"{name}.csv").open(encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream))

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11, "axes.spines.top": False,
                         "axes.spines.right": False, "savefig.dpi": 180})
    rows = read("popularity_bands")
    tracks = [float(r["track_share_pct"]) for r in rows]
    events = [float(r["event_share_pct"]) for r in rows]
    labels = ["1 listener", "2 listeners", "3+ listeners"]
    x = np.arange(3)
    fig, ax = plt.subplots(figsize=(10, 6.2))
    for offset, values, label, color in ((-.19, tracks, "Share of training tracks", "#246B91"),
                                        (.19, events, "Share of training listen events", "#DB7733")):
        bars = ax.bar(x + offset, values, .36, label=label, color=color)
        ax.bar_label(bars, labels=[f"{v:.2f}%" for v in values], padding=4, fontsize=11)
    ax.set(xticks=x, xticklabels=labels, ylim=(0, 100), ylabel="Share of each total (%)",
           xlabel="Distinct training listeners per track", title="Training popularity: tracks and listen events")
    ax.legend(loc="upper right", frameon=False)
    ax.set_axisbelow(True)
    ax.grid(axis="y", alpha=.2)
    fig.text(.5, .04, f"{VERSION} | Train: 2026-08-17 to 2026-09-27 UTC\n"
             f"Track denominator: {stats['items']:,} tracks | Event denominator: {stats['events']:,} events\n"
             "Repeated listens count as events. Bands describe EDA groups, not final evaluation thresholds.",
             ha="center", fontsize=9)
    fig.tight_layout(rect=(0, .16, 1, 1))
    fig.savefig(output / "figures" / "popularity-shares.png")
    plt.close(fig)
    rows = read("user_history_histogram")
    counts = [int(r["users"]) for r in rows]
    labels = [f"{int(r['lower_inclusive']):,}\u2013{int(r['upper_exclusive']) - 1:,}" for r in rows]
    fig, ax = plt.subplots(figsize=(11, 6.2))
    bars = ax.bar(labels, counts, color="#246B91", width=.8)
    ax.bar_label(bars, labels=[f"{v:,}" for v in counts], padding=4, fontsize=9)
    ax.set(ylabel="Users (count)", xlabel="Unique training tracks per user (tracks; unequal-width bins)",
           title="Distribution of unique training tracks per user", ylim=(0, max(counts) * 1.17))
    ax.tick_params(axis="x", rotation=35, labelsize=9)
    ax.yaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
    ax.set_axisbelow(True)
    ax.grid(axis="y", alpha=.2)
    fig.text(.5, .025, f"{VERSION} | Train: 2026-08-17 to 2026-09-27 UTC | Denominator: {stats['users']:,} users\n"
             "Each user contributes once. Bar height is user count, not probability density.", ha="center", fontsize=9)
    fig.tight_layout(rect=(0, .10, 1, 1))
    fig.savefig(output / "figures" / "user-training-tracks.png")
    plt.close(fig)
    return {"popularity-shares.png": {"table": "popularity_bands.csv", "version": VERSION,
                "track_share_pct": tracks, "event_share_pct": events,
                "track_denominator": stats["items"], "event_denominator": stats["events"], "y_unit": "percent"},
            "user-training-tracks.png": {"table": "user_history_histogram.csv", "version": VERSION,
                "users": counts, "bins": labels, "user_denominator": stats["users"], "y_unit": "users"}}


def analyze(package: Path, output: Path) -> dict:
    package, output = package.resolve(), output.resolve()
    require(not output.is_relative_to(package) and not package.is_relative_to(output), "출력과 입력 패키지 경로가 겹칩니다")
    check_output_inventory(output, complete=False)
    source_files = (*SOURCES, "config/listening-package-reference.json", "scripts/analyze_listening.py",
                    "scripts/verify_listening_analysis.py", "requirements.txt")
    sources_before = {name: fingerprint(ROOT / name) for name in source_files}
    manifest, data, checks = load_package(package)
    stats, tables, user_tracks, user_events = aggregate_train(data, manifest)
    evaluation, target_summary, excluded, target_bands = aggregate_evaluation(data, manifest, user_tracks)
    tables["dataset_summary"] = [stats]
    tables["user_summary"] = [distribution(user_tracks, "train", "unique_tracks"),
                              distribution(user_events, "train", "listen_events")]
    tables["user_history_histogram"] = histogram(user_tracks, "train")
    tables["target_summary"], tables["target_count_bands"] = target_summary, target_bands
    tables["evaluation_pair_accounting"] = excluded
    tables["evaluation_users"] = [
        {"period": p, "status": reason, "users": value,
         "cohort_denominator": evaluation[p]["cohort_users"],
         "cohort_share_pct": 100 * value / evaluation[p]["cohort_users"]}
        for p in PERIODS for reason, value in {
            **manifest["evaluation"]["periods"][p]["user_exclusions"],
            "eligible": evaluation[p]["eligible_users"]}.items()]
    reuse_reports(manifest, stats, tables, data)
    require(set(tables) == set(TABLE_NAMES), "생성할 집계표 목록이 예정된 11개와 다릅니다")
    require(sum(r["tracks"] for r in tables["popularity_bands"]) == stats["items"]
            and sum(r["events"] for r in tables["popularity_bands"]) == stats["events"]
            and sum(r["users"] for r in tables["user_history_histogram"]) == stats["users"], "구간 집계 보존 오류")
    write_verification_record(output, {"status": "not_run", "matches": None,
                                      "scope": "분석 산출물 갱신 후 별도 검증을 아직 실행하지 않음"})
    (output / "tables").mkdir(parents=True, exist_ok=True)
    (output / "figures").mkdir(exist_ok=True)
    for name, rows in tables.items():
        write_table(output / "tables" / f"{name}.csv", [{"dataset_version": VERSION, **row} for row in rows])
    figures = draw_figures(output, stats)
    after = {name: fingerprint(package / name) for name in checks["input_fingerprints"]}
    require(after == checks["input_fingerprints"], "실행 중 입력 패키지가 변경됐습니다")
    require(sources_before == {name: fingerprint(ROOT / name) for name in source_files}, "실행 중 근거 파일이 변경됐습니다")
    result = {"dataset_version": VERSION, "contract_version": manifest["contract_version"],
              "rules_version": manifest["rules_version"],
              "scope": "원본 대조 성공 패키지 고정 기준·CSV 전체 수치 열·기존 검증 표 대조; 원본 사건은 재처리하지 않음",
              "train": stats, "evaluation": evaluation,
              "checks": {**checks, "fixed_package_reference_match": True,
                         "package_unchanged": True, "source_reports_match": True,
                         "table_roundtrip": True, "figure_values_from_saved_tables": True},
              "source_fingerprints": sources_before, "figure_specifications": figures,
              "output_files": {name: fingerprint(output / name) for name in sorted(ARTIFACT_PATHS)}}
    result["table_schemas"] = {name: {"dataset_version": "string", **{
        key: "int64" if isinstance(value, int) else "float64" if isinstance(value, (float, np.floating)) else "string"
        for key, value in rows[0].items()}} for name, rows in tables.items()}
    (output / "analysis_manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    check_output_inventory(output, complete=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=Path("datasets/listenbrainz-preprocessing"), help="읽기 전용 공통 CSV 패키지")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports/eda", help="집계표·그림·내부 검사 기록 저장 위치")
    args = parser.parse_args()
    result = analyze(args.package, args.output_dir)
    print(json.dumps({"dataset_version": result["dataset_version"], "train": result["train"],
                      "evaluation": result["evaluation"], "checks_passed": True}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
