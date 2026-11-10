# 입력 검증·정제·UTC 분할 실행 기록

최종 전달 패키지 생성 전의 전체 입력 검증·임시 정제 실행이다. 원본 사용자 ID·개인별 기록·개인 경로는 포함하지 않는다.

## 입력 스키마·선정 사용자

```json
{
  "schema": [
    {
      "column": "user_id",
      "type": "BIGINT"
    },
    {
      "column": "listened_at",
      "type": "TIMESTAMP"
    },
    {
      "column": "artist_name",
      "type": "VARCHAR"
    },
    {
      "column": "recording_name",
      "type": "VARCHAR"
    }
  ],
  "cohort": {
    "rows": 5000,
    "users": 5000,
    "indices": 5000,
    "min_index": 1,
    "max_index": 5000,
    "missing_fields": 0,
    "raw_users_not_in_cohort": 0,
    "cohort_users_without_raw_rows": 0
  },
  "raw_rows": 9350628
}
```

## 정제 제외와 분할 집계

```json
{
  "raw_rows": 9350628,
  "exclusions": {
    "missing_required": 0,
    "outside_window": 0,
    "empty_name": 0,
    "duplicate_excess": 537575
  },
  "clean_events": 8813053,
  "accounting_matches": true,
  "partitions": [
    {
      "split": "train",
      "start_inclusive_utc": "2026-08-17 00:00:00",
      "end_exclusive_utc": "2026-09-28 00:00:00",
      "events": 6623584,
      "users": 5000,
      "items": 1525005
    },
    {
      "split": "validation",
      "start_inclusive_utc": "2026-09-28 00:00:00",
      "end_exclusive_utc": "2026-10-05 00:00:00",
      "events": 1104514,
      "users": 4205,
      "items": 418374
    },
    {
      "split": "test",
      "start_inclusive_utc": "2026-10-05 00:00:00",
      "end_exclusive_utc": "2026-10-12 00:00:00",
      "events": 1084955,
      "users": 4114,
      "items": 418776
    }
  ],
  "train": {
    "user_item_pairs": 2739292,
    "users": 5000,
    "events": 6623584,
    "items": 1525005
  },
  "cohort_users_without_train": 0
}
```

## 학습 기간 품질 진단

```json
{
  "period": "train only",
  "name_signals": {
    "affected_events": 455,
    "replacement_events": 18,
    "zero_width_or_bom_events": 221,
    "nonwhitespace_control_events": 216
  },
  "timestamps": {
    "multitrack_timestamps": 256142,
    "multitrack_events": 523096,
    "timestamps_ge10": 94,
    "events_ge10": 2146,
    "timestamps_ge100": 2,
    "max_tracks": 572
  },
  "activity": {
    "active_days_over720": 744,
    "active_days_over1440": 39,
    "max_day_events": 2406
  },
  "profiles": {
    "pairs_with_at_least10_shared_items": 366446,
    "max_jaccard": 1.0,
    "identical_itemset_pairs": 1
  },
  "flagged_users": {
    "abnormal_name": 112,
    "multitrack": 1459,
    "burst_ge10": 42,
    "day_over1440": 6,
    "identical_itemset": 2
  },
  "events_removed_by_flags": 0,
  "accounts_merged": 0
}
```

6개 입력의 실행 전후 바이트 수·SHA256 일치를 확인했다. 품질 플래그는 삭제 조건이 아니며 각 범주는 겹칠 수 있다. 진단의 개인별 플래그는 임시 DB 안에만 있고 종료 시 삭제한다.

중복 대표 원문은 원문 쌍의 UTF-8 바이트 순으로 선택한다. 표시 이름은 정제된 train 사건의 원문 쌍 빈도, 같은 바이트 동률 순서로 결정한다. 이 보고서의 검증 범위는 입력 파일과 정제·시간 분할 집계다.
