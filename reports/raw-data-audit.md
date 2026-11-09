# 원본 데이터 점검 결과

이 문서는 `scripts/audit_raw_data.py`의 실제 실행 결과다. 정규화·중복 제거 결과는 임시 진단 집계이며 최종 전처리 전달본은 아직 생성하지 않았다. 모든 날짜는 UTC다.

## 검증 범위와 근거

Parquet 전체를 읽어 스키마·통계·해시를 검사했다. 함께 제공된 여섯 파일의 해시를 실행 전후 대조했다. 이름 정규화는 NFC와 고정된 유니코드 공백 29종의 축약이다. 결측·빈 이름·범위 밖 행을 진단 집계에서 분리하고 동일 사용자·시각·정규화 튜플은 한 건으로 집계했다. 원본은 수정하지 않았다.

부속 README·source_manifest·기존 검증 JSON의 추출 방법 및 공식 덤프 규모는 **기존 설명에 기록된 사실**이다. 원본 TAR, 20,284명 전체 후보군, 표본의 상위 5,000명 선정 순위는 다시 검증하지 않았다. 로컬 CSV와 현재 학습 이력의 일치 여부만 직접 검증했다. TIMESTAMP에는 시간대가 내장되어 있지 않아 UTC 해석은 부속 자료의 명세를 따른다.

## 실행 환경

| 항목 | 값 |
| --- | --- |
| "started_note" | "finished_utc is report generation time" |
| "finished_utc" | "2026-11-07T07:53:33.304587+00:00" |
| "python" | "3.13.7" |
| "duckdb" | "1.4.4" |
| "platform" | "Darwin arm64" |
| "memory_limit" | "3GB" |
| "threads" | 2 |
| "sample_size" | 5 |
| "elapsed_seconds" | 36.43 |
| "peak_rss_bytes" | 2622668800 |
| "normalization" | "NFC + fixed 29 whitespace codepoints -&gt; ASCII space + strip" |
| "python_unicode_version" | "15.1.0" |
| "whitespace_codepoints" | ["U+0009", "U+000A", "U+000B", "U+000C", "U+000D", "U+001C", "U+001D", "U+001E", "U+001F", "U+0020", "U+0085", "U+00A0", "U+1680", "U+2000", "U+2001", "U+2002", "U+2003", "U+2004", "U+2005", "U+2006", "U+2007", "U+2008", "U+2009", "U+200A", "U+2028", "U+2029", "U+202F", "U+205F", "U+3000"] |
| "final_preprocessing_files_created" | False |

## 입력 파일 해시와 불변성

| file | bytes | sha256 | unchanged |
| --- | --- | --- | --- |
| "listens_8weeks_5000users.parquet" | 144828609 | "4beecfca926a472081b70bcc50c2a20193756a90f9e8dc84336076e86b72e3c3" | True |
| "README.txt" | 5183 | "fd8104ccfd16875bac897640464d7927ffce8c2057016a53cd6b9bf5625d5e7e" | True |
| "selected_users.csv" | 76347 | "6ca4013d636ac7c16aa52ce6fa1e9fa670df1a4aca8eda64ad9f30f45cbe94a5" | True |
| "source_manifest.json" | 306507 | "7e0af4eb5328ab661e66efc7bf9378d74cb8c8e1a377d3792ac93889765ec526" | True |
| "verified_8week_summary.json" | 7606 | "ac897b3ee55bfd6aa2145dfd16c3eb7705604f6cbaa73646cafdc33d980a445f" | True |
| "COPYING" | 6390 | "75f3c90d6fa833817f19d019b35807687c3ed1c0b858b5f274625e96dda24bea" | True |

## 컬럼과 실제 자료형

| column_name | column_type | null | key | default | extra |
| --- | --- | --- | --- | --- | --- |
| "user_id" | "BIGINT" | "YES" | null | null | null |
| "listened_at" | "TIMESTAMP" | "YES" | null | null | null |
| "artist_name" | "VARCHAR" | "YES" | null | null | null |
| "recording_name" | "VARCHAR" | "YES" | null | null | null |

## 원본 전체 통계

| 항목 | 값 |
| --- | --- |
| "rows" | 9350628 |
| "users" | 5000 |
| "dates" | 56 |
| "min_time" | "2026-08-17 00:00:00" |
| "max_time" | "2026-10-11 23:59:59" |
| "outside_window" | 0 |

## 결측 수

| 항목 | 값 |
| --- | --- |
| "user_id" | 0 |
| "listened_at" | 0 |
| "artist_name" | 0 |
| "recording_name" | 0 |

## 빈 문자열과 이름 정규화 영향

| 항목 | 값 |
| --- | --- |
| "empty_artist" | 0 |
| "empty_recording" | 0 |
| "whitespace_artist" | 0 |
| "whitespace_recording" | 0 |
| "changed_artist_rows" | 1939 |
| "changed_recording_rows" | 10760 |
| "nfc_changed_rows" | 1249 |
| "any_name_changed_rows" | 12438 |

## 선정 사용자 CSV 대조

| 항목 | 값 |
| --- | --- |
| "rows" | 5000 |
| "users" | 5000 |
| "indices" | 5000 |
| "min_index" | 1 |
| "max_index" | 5000 |
| "min_claimed_train_items" | 10 |
| "missing_rows" | 0 |
| "raw_users_not_in_cohort" | 0 |
| "cohort_users_without_raw_rows" | 0 |
| "train_item_count_mismatches" | 8 |
| "no_train_users" | 0 |
| "min_current_train_items" | 10 |
| "within_cohort_hash_rank_mismatches" | 0 |

## 원본 기간별 규모

| split | events | users |
| --- | --- | --- |
| "test" | 1153857 | 4114 |
| "train" | 7018319 | 5000 |
| "validation" | 1178452 | 4205 |

## 원문 키 중복 후보

| 항목 | 값 |
| --- | --- |
| "unique_events" | 8813073 |
| "excess_duplicate_rows" | 537555 |

## 원문 이름 튜플 수

| 항목 | 값 |
| --- | --- |
| "items" | 1856579 |

## 정규화 후 중복 후보 진단

| 항목 | 값 |
| --- | --- |
| "events" | 8813053 |
| "users" | 5000 |
| "excess_duplicate_rows" | 537575 |
| "duplicate_groups" | 530904 |
| "max_multiplicity" | 5 |
| "invalid_or_outside_rows" | 0 |

## 정규화 후 전체 이름 튜플 수

| 항목 | 값 |
| --- | --- |
| "items" | 1856311 |

## 진단 집계의 기간별 규모

| split | events | users | items |
| --- | --- | --- | --- |
| "test" | 1084955 | 4114 | 418776 |
| "train" | 6623584 | 5000 | 1525005 |
| "validation" | 1104514 | 4205 | 418374 |

## 진단 집계의 학습 카탈로그

| 항목 | 값 |
| --- | --- |
| "items" | 1525005 |
| "single_listener_items" | 1204318 |
| "one_two_listener_items" | 1363187 |
| "max_listeners" | 330 |
| "user_item_pairs" | 2739292 |
| "users" | 5000 |
| "events" | 6623584 |

## 과거 규칙 재현과 차이 확인

| 항목 | 값 |
| --- | --- |
| "legacy_events" | 8813057 |
| "legacy_items" | 1856351 |
| "legacy_train_events" | 6623588 |
| "legacy_train_items" | 1525029 |
| "legacy_train_pairs" | 2739300 |
| "legacy_cohort_item_count_mismatches" | 0 |
| "current_minus_legacy_events" | -4 |
| "current_minus_legacy_items" | -40 |

## 다른 시각의 반복 청취

| 항목 | 값 |
| --- | --- |
| "repeated_train_user_item_pairs" | 794607 |
| "train_repeat_events_beyond_first" | 3884292 |
| "max_train_pair_listens" | 14113 |

## 청취량 분포

| 항목 | 값 |
| --- | --- |
| "min_active_day_events" | 1 |
| "median_active_day_events" | 24.0 |
| "p99_active_day_events" | 529.0 |
| "max_active_day_events" | 2406 |
| "active_user_days" | 165880 |
| "min_user_events" | 10 |
| "median_user_events" | 743.5 |
| "p95_user_events" | 5641.4000000000015 |
| "max_user_events" | 80707 |

## 비음악 콘텐츠 의심 문자열 신호

| 항목 | 값 |
| --- | --- |
| "pattern" | "podcast&#124;audiobook&#124;audio book&#124;chapter [0-9]&#124;episode [0-9]&#124;팟캐스트&#124;오디오북" |
| "events" | 4783 |
| "users" | 470 |
| "items" | 2729 |

## 평가 데이터 제외 규모 진단

성능 지표를 계산한 것이 아니다. 신규 곡과 학습 기청취곡은 서로 겹치지 않는다. 각 범주의 사용자 수는 서로 겹칠 수 있으므로 합산하지 않는다. no_target_users는 비활동 사용자를 포함한 원래 표본 기준이며 후보 부족과는 별도 조건이다.

### validation

| 항목 | 값 |
| --- | --- |
| "events" | 1104514 |
| "active_users" | 4205 |
| "user_item_pairs" | 596553 |
| "new_item" | {"events": 228684, "users": 3792, "items": 168926, "pairs": 174308} |
| "seen_in_train" | {"events": 617108, "users": 3813, "items": 164659, "pairs": 228922} |
| "eligible_users" | 3922 |
| "target_pairs" | 193323 |
| "no_train_users" | 0 |
| "no_target_users" | 1078 |
| "insufficient_candidate_users" | 0 |
| "min_candidate_count" | 1513129 |

### test

| 항목 | 값 |
| --- | --- |
| "events" | 1084955 |
| "active_users" | 4114 |
| "user_item_pairs" | 594266 |
| "new_item" | {"events": 255471, "users": 3764, "items": 180246, "pairs": 187636} |
| "seen_in_train" | {"events": 550599, "users": 3639, "items": 148304, "pairs": 205427} |
| "eligible_users" | 3852 |
| "target_pairs" | 201203 |
| "no_train_users" | 0 |
| "no_target_users" | 1148 |
| "insufficient_candidate_users" | 0 |
| "min_candidate_count" | 1513129 |

## 제한된 표본 점검

각 범주에서 최대 설정 sample_size개를 빈도 내림차순과 명시적 동률 순서로 선정했다. 무작위 대표 표본이 아니다. audit_user_index는 기존 CSV의 1부터 시작하는 표시 ID이며 공통 user_idx와 다르다. 제목 문자열은 JSON 이스케이프로 공백·제어문자를 표시한다.

### names

| raw_artist | raw_recording | normalized_artist | normalized_recording | raw_events |
| --- | --- | --- | --- | --- |
| "Živé vysílání" | "Jazz\nNapříč jazzovou současností i minulostí" | "Živé vysílání" | "Jazz Napříč jazzovou současností i minulostí" | 507 |
| "Živé vysílání" | "Jazz\nModern Mainstream: Napříč jazzovou současností i minulostí" | "Živé vysílání" | "Jazz Modern Mainstream: Napříč jazzovou současností i minulostí" | 504 |
| "野崎りこん" | "marble　feat.FUNKY鬚HANK" | "野崎りこん" | "marble feat.FUNKY鬚HANK" | 111 |
| "Živé vysílání" | "Jazz\nFokus: Jazzrock a funk: Jazzrocková fúze, groove a funk" | "Živé vysílání" | "Jazz Fokus: Jazzrock a funk: Jazzrocková fúze, groove a funk" | 106 |
| "Živé vysílání" | "Jazz\nJazzrocková fúze, groove a funk" | "Živé vysílání" | "Jazz Jazzrocková fúze, groove a funk" | 104 |

### duplicates

| audit_user_index | listened_at | artist_name | recording_name | multiplicity |
| --- | --- | --- | --- | --- |
| 2345 | "2026-09-04 13:08:39" | "Chat Pile" | "October All the Time" | 5 |
| 788 | "2026-08-18 16:52:09" | "Chris Webby" | "Rookie of the Year" | 4 |
| 788 | "2026-09-02 15:48:10" | "ITZY" | "Wind Ride" | 4 |
| 1421 | "2026-09-27 03:16:37" | "Sunny Day Real Estate" | "In Circles" | 4 |
| 1421 | "2026-09-29 00:17:34" | "Tweaker feat. Will Oldham" | "Ruby" | 4 |

### repeats

| audit_user_index | artist_name | recording_name | listens |
| --- | --- | --- | --- |
| 2459 | "BTS" | "SWIM" | 14113 |
| 1507 | "BTS" | "SWIM" | 9940 |
| 3348 | "Riley Green feat. Ella Langley" | "Don’t Mind If I Do" | 8736 |
| 3785 | "BTS" | "SWIM" | 8010 |
| 4854 | "Eminem" | "Rock Bottom" | 7775 |

### high_activity

| audit_user_index | day | events | items | timestamps |
| --- | --- | --- | --- | --- |
| 2459 | "2026-08-17" | 2406 | 100 | 2347 |
| 2459 | "2026-09-19" | 2136 | 52 | 2094 |
| 2459 | "2026-09-27" | 2116 | 75 | 2095 |
| 2459 | "2026-08-18" | 2110 | 131 | 2085 |
| 2459 | "2026-09-21" | 2010 | 73 | 1977 |

### content_signal

| artist_name | recording_name | events |
| --- | --- | --- |
| "C8 Podcast" | "NESSIE JUDGE DIPAKSA MASUK GEREJA SETAN, HAMPIR MATI KARENA REM BLONG?! &#124; BICARA SARA WIJAYANTO" | 108 |
| "J. K. Rowling" | "Chapter 16 - The Goblet of Fire" | 53 |
| "Nongagos; bayman917; surreal" | "Flower Man With Lyrics &#124; Deltarune Chapter 5" | 41 |
| "Glass Animals" | "Season 2 Episode 3" | 37 |
| "Toby Fox" | "Chapter 5 Logo" | 36 |

### case_variants

| folded_artist | folded_recording | variants | first_example | last_example |
| --- | --- | --- | --- | --- |
| "lady gaga" | "rain on me (with ariana grande)" | 5 | "Lady GaGa / Rain On Me (With Ariana Grande)" | "Lady Gaga / Rain on Me (with Ariana Grande)" |
| "mass of the fermenting dregs" | "dareka ga kizu wo ou sono shunkan wo" | 5 | "MASS OF THE FERMENTING DREGS / Dareka Ga Kizu Wo Ou Sono Shunkan Wo" | "Mass of the Fermenting Dregs / Dareka ga kizu wo ou sono shunkan wo" |
| "egoist" | "namae no nai kaibutsu" | 4 | "EGOIST / Namae No Nai Kaibutsu" | "Egoist / Namae no nai Kaibutsu" |
| "maretu" | "coin locker baby" | 4 | "MARETU / Coin Locker Baby" | "maretu / Coin Locker Baby" |
| "maretu" | "mind brand" | 4 | "MARETU / Mind Brand" | "maretu / Mind brand" |

### legacy_rule_differences

| raw_artist | raw_recording | legacy_artist | legacy_recording | current_artist | current_recording | events |
| --- | --- | --- | --- | --- | --- | --- |
| "野崎りこん" | "marble　feat.FUNKY鬚HANK" | "野崎りこん" | "marble　feat.FUNKY鬚HANK" | "野崎りこん" | "marble feat.FUNKY鬚HANK" | 111 |
| "Koko" | "No.048　幸祜 -KOKO- 「Phantom」" | "Koko" | "No.048　幸祜 -KOKO- 「Phantom」" | "Koko" | "No.048 幸祜 -KOKO- 「Phantom」" | 93 |
| "Anri" | "悲しみがとまらない　I CAN'T STOP THE LONELINESS" | "Anri" | "悲しみがとまらない　I CAN'T STOP THE LONELINESS" | "Anri" | "悲しみがとまらない I CAN'T STOP THE LONELINESS" | 68 |
| "PhilosophicalRacoon" | "Fields of â  â  â  and â â  â â " | "PhilosophicalRacoon" | "Fields of â  â  â  and â â  â â " | "PhilosophicalRacoon" | "Fields of â  â  â  and â â  â â " | 39 |
| "soowamisu" | "FULL . ◠ . TEAM . ◠ . SWEEP ♫ • ｡ﾟ　You're So Obvious - G6 Anthem Remix" | "soowamisu" | "FULL . ◠ . TEAM . ◠ . SWEEP ♫ • ｡ﾟ　You're So Obvious - G6 Anthem Remix" | "soowamisu" | "FULL . ◠ . TEAM . ◠ . SWEEP ♫ • ｡ﾟ You're So Obvious - G6 Anthem Remix" | 25 |

### cohort_count_differences

| audit_user_index | csv_items | current_items | difference |
| --- | --- | --- | --- |
| 245 | 1091 | 1090 | -1 |
| 788 | 3258 | 3257 | -1 |
| 952 | 1152 | 1151 | -1 |
| 2353 | 276 | 275 | -1 |
| 2457 | 1220 | 1219 | -1 |

## 해석과 한계

과거 규칙은 `NFC(regexp_replace(trim(s), '\s+', ' ', 'g'))`이며 로컬 기존 분석 스크립트에서 확인했다. 이를 같은 파일에 재적용한 수치와 현재 규칙을 위에서 대조한다. RE2의 `\s`가 정리하지 않는 유니코드 공백과 trim 적용 순서의 차이가 있으므로 CSV의 train_unique_tracks를 새 규칙의 기대값으로 강제하지 않는다. 사용자 선정 순서는 유지한다.

동일 사용자·동일 시각·동일 정규화 튜플의 초과 행만 중복 제거 대상으로 삼는다. 다른 시각의 반복은 실제 선호 신호일 수 있어 보존한다. 대소문자 차이나 라이브·리믹스·리마스터 표기는 의미가 다를 수 있어 자동 병합하지 않는다.

과도한 청취량과 podcast·audiobook 등의 문자열은 검토 신호일 뿐이다. 네 컬럼만으로 재생 길이, 실제 사람의 청취, 음악 여부를 판정할 수 없으므로 해당 신호로 자동 삭제하지 않는다. 동명 곡의 오병합과 표기 차이의 과분리는 남는다.

여기서 재산출된 수치가 과거 검증 수치와 같더라도 최종 전달본 통계는 아니다. 최종 해시·ID·sparse matrix·제외 사유별 산출물 검증은 패키지 생성 시 수행한다. source_manifest의 공식 TAR 전체 해시를 검증한 것이 아니다.

## 진단 일별 규모

| date | events | users |
| --- | --- | --- |
| "2026-08-17" | 156791 | 2869 |
| "2026-08-18" | 162533 | 2920 |
| "2026-08-19" | 162881 | 2949 |
| "2026-08-20" | 161678 | 2942 |
| "2026-08-21" | 166887 | 2941 |
| "2026-08-22" | 154121 | 2803 |
| "2026-08-23" | 149578 | 2706 |
| "2026-08-24" | 157037 | 2876 |
| "2026-08-25" | 146865 | 2896 |
| "2026-08-26" | 155740 | 2928 |
| "2026-08-27" | 158171 | 2900 |
| "2026-08-28" | 159925 | 2911 |
| "2026-08-29" | 145036 | 2777 |
| "2026-08-30" | 145267 | 2706 |
| "2026-08-31" | 156516 | 2917 |
| "2026-09-01" | 160390 | 2979 |
| "2026-09-02" | 159337 | 2959 |
| "2026-09-03" | 160054 | 2995 |
| "2026-09-04" | 166490 | 3021 |
| "2026-09-05" | 151234 | 2832 |
| "2026-09-06" | 150206 | 2820 |
| "2026-09-07" | 159408 | 2941 |
| "2026-09-08" | 160040 | 3055 |
| "2026-09-09" | 164725 | 3098 |
| "2026-09-10" | 163997 | 3082 |
| "2026-09-11" | 163797 | 3085 |
| "2026-09-12" | 150272 | 2883 |
| "2026-09-13" | 142245 | 2761 |
| "2026-09-14" | 160290 | 3012 |
| "2026-09-15" | 161098 | 3070 |
| "2026-09-16" | 164751 | 3081 |
| "2026-09-17" | 160282 | 3075 |
| "2026-09-18" | 163129 | 3133 |
| "2026-09-19" | 152451 | 2927 |
| "2026-09-20" | 145532 | 2812 |
| "2026-09-21" | 165717 | 3077 |
| "2026-09-22" | 162607 | 3101 |
| "2026-09-23" | 163231 | 3137 |
| "2026-09-24" | 165074 | 3142 |
| "2026-09-25" | 164128 | 3165 |
| "2026-09-26" | 152962 | 2955 |
| "2026-09-27" | 151111 | 2896 |
| "2026-09-28" | 162556 | 3066 |
| "2026-09-29" | 163351 | 3123 |
| "2026-09-30" | 162005 | 3047 |
| "2026-10-01" | 162181 | 3056 |
| "2026-10-02" | 162807 | 3101 |
| "2026-10-03" | 148219 | 2907 |
| "2026-10-04" | 143395 | 2806 |
| "2026-10-05" | 159795 | 3064 |
| "2026-10-06" | 158947 | 3079 |
| "2026-10-07" | 162062 | 3017 |
| "2026-10-08" | 165465 | 3051 |
| "2026-10-09" | 159949 | 2983 |
| "2026-10-10" | 147775 | 2808 |
| "2026-10-11" | 130962 | 2637 |

## 코드 추적

| 항목 | 값 |
| --- | --- |
| "head" | null |
| "branch" | "work/data-audit" |
| "status" | "?? .gitignore\n?? config/\n?? reports/\n?? requirements.txt\n?? scripts/\n?? tests/" |
| "audit_script_sha256" | "929c5f0ed6ad9474ec21eaea0f03cc002be53fde11665480daf1087f8b0dfa40" |

규칙과 전달 규격은 `docs/preprocessing-plan.md`, `docs/data-contract.md`를 따른다. 원본 사용자 식별자는 보고서에 싣지 않았다.
