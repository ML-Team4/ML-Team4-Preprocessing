# ListenBrainz 배포 검증

모델 입력 ZIP과 주별 원본을 같은 저장소 다운로드본에 제공한다. 모델 담당자는 ZIP을 풀어 같은 CSV 7개를 읽고, 재현 담당자는 동봉 원본으로 별도 출력 폴더에 같은 CSV를 생성한다. [README](../README.md) · [인수인계](../docs/data-preprocessing-handoff.md) · [Issue #15](https://github.com/ML-Team4/ML-Team4-Preprocessing/issues/15)

## 원본과 주별 CSV 대조

기존 Parquet과 부속 자료를 포함한 원본 파일 6개는 변환 전에 기존 크기·SHA256 기준을 통과했다. 동일한 네 자료형으로 원본과 압축 CSV를 읽고 주별 경계·행 수·고유 사용자 수·시각 범위를 검사했다. `EXCEPT ALL`을 두 방향으로 적용해 네 필드가 같은 행의 출현 횟수까지 대조했다. 전체 9,350,628행·5,000명, 실제 첫 시각 2026-08-17 00:00:00 UTC, 마지막 시각 2026-10-11 23:59:59 UTC가 일치했다. 누락 0행, 추가 0행이다. 원문 키 기준 중복 초과 537,555행과 이름 표기를 주별 CSV에 그대로 보존했다.

| UTC 주 시작일 | 원본 및 CSV 행 수 |
| --- | ---: |
| 2026-08-17 | 1,179,959 |
| 2026-08-24 | 1,130,268 |
| 2026-08-31 | 1,165,576 |
| 2026-09-07 | 1,167,527 |
| 2026-09-14 | 1,177,299 |
| 2026-09-21 | 1,197,690 |
| 2026-09-28 | 1,178,452 |
| 2026-10-05 | 1,153,857 |
| 합계 | 9,350,628 |

주별 경계는 월요일 00:00:00 UTC를 포함하며 다음 월요일 00:00:00 UTC는 포함하지 않는다. [배포 manifest](../datasets/raw/distribution_manifest.json)에 주별 사용자 수와 실제 시각 범위, [CSV 고정 기준](../config/raw-csv-reference.toml)에 배포 파일 무결성을 기록한다. `source_manifest.json`·`verified_8week_summary.json`·`README.txt`는 변환 전 원본의 출처·선정·검증 자료로 바이트를 보존했다. 선정 사용자 CSV와 CC0 이용 조건도 그대로 동봉한다.

## 전처리 패키지와 재현 대조

기존 패키지를 Parquet 원본에서 독립 검증해 CSV 7개 전체의 누락·초과가 0임을 다시 확인했다. 배포본은 폴더명과 README를 갱신했다. manifest는 배포명·README 지문·재현 명령·배포 계보를 갱신했고 CSV의 행·컬럼·공통 인덱스·순서·인코딩과 COPYING을 보존했다. [기존 패키지 기준](../config/verified-package-origin.json)과 [배포 패키지 기준](../config/listening-package-reference.json)을 분리한다.

| 전체 대조 | 비교 파일 | 불일치 파일 |
| --- | ---: | ---: |
| 기존 원본 검증 CSV ↔ 배포 ZIP의 CSV | 7 | 0 |
| 주별 원본 CSV에서 새로 생성한 CSV ↔ 배포 ZIP의 CSV | 7 | 0 |

재현본과 배포본에 각각 원본 기반 독립 검사를 실행해 학습·평가 행의 누락과 초과가 모두 0임을 확인했다. 전처리에서 정규화 키 기준 중복 초과 537,575행을 제외해 8,813,053사건을 보존한다. 결측·기간 밖·빈 이름 제외는 각 0행이다. 학습은 5,000명·1,525,005곡·2,739,292개 사용자·곡 쌍·청취 6,623,584건이다. validation은 3,922명·193,323 정답 쌍, test는 3,852명·201,203 정답 쌍이다. [정제·평가 검증](csv-dataset-validation.md)

## 다운로드본과 게시 대상

Git 게시 후보를 별도 폴더에 풀어 Git 메타데이터·개인 경로 설정·기존 Parquet·기존 패키지 없이 README를 따라 실행했다. ZIP은 `datasets/listenbrainz-preprocessing/` 한 겹에 풀리고 CSV 7개의 공통 ID, 학습 행렬, 고유 청취자 수, 실제 곡명과 아티스트명, 평가 사용자와 정답 연결을 확인했다. 압축된 주별 CSV에서 원본 점검·정제·시간 분할·새 패키지 ZIP 생성·독립 검증·배포본 대조를 실행했다.

회귀 테스트 44개와 의존성 검사를 통과했다. EDA의 집계 CSV 11개·그림 2개·manifest 독립 재현 불일치는 0건이다. 원본 배포 파일과 전처리 ZIP은 일반 Git 업로드 한도 이내다. 게시 후보의 실제 Git blob에 LFS 포인터·해제 사본·기존 Parquet·개인 경로 설정이 포함되지 않는지 확인했다. [GitHub 파일 크기 기준](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github#file-size-limits)
