# listenbrainz-preprocessing 공통 학습·평가 CSV

이 폴더를 Item-Based CF와 Implicit MF의 공통 입력으로 지정한다. 같은 패키지의 CSV 7개를 함께 사용한다. 저장소 루트에서 `unzip datasets/listenbrainz-preprocessing.zip -d datasets`를 실행하면 `datasets/listenbrainz-preprocessing/`에 바로 풀린다.

| 파일 | 용도 |
| --- | --- |
| train_interactions.csv | `user_idx` 행 × `item_idx` 열에 `listen_count`를 넣어 학습 행렬 구성 |
| users.csv, items.csv | 0 기반 공통 인덱스와 곡명·아티스트명·고유 train 청취자 수 조회 |
| validation_users.csv, validation_targets.csv | 검증 대상 사용자와 정답으로 설정 선택 |
| test_users.csv, test_targets.csv | 설정을 고정한 뒤 테스트 대상 사용자와 정답으로 최종 평가 |

후보는 전체 train 카탈로그에서 해당 사용자의 train 청취곡을 뺀 집합이다. test에도 같은 train 이력과 카탈로그를 적용한다. CSV는 헤더가 있는 UTF-8 BOM이며 이름에 쉼표·따옴표·개행이 있을 수 있다. 문자열의 `NA`, `NULL`은 원문 그대로 읽는다.

패키지 폴더에서 아래 코드로 공통 `item_idx`를 곡명과 아티스트명에 연결할 수 있다. `item_idx = 0`을 모델이 반환한 인덱스로 바꾼다.

```python
import csv

item_idx = 0
with open("items.csv", encoding="utf-8-sig", newline="") as stream:
    item = next(row for row in csv.DictReader(stream) if int(row["item_idx"]) == item_idx)
print(item_idx, item["recording_name"], item["artist_name"], item["train_listener_count"])
```

`dataset_manifest.json`은 파일 무결성·스키마·UTC 시간 경계와 검증 집계를 기록한다. 데이터 출처는 MetaBrainz Foundation / ListenBrainz이며 이용 조건은 `COPYING`에 있다. 전체 행렬·평가 로딩 예제는 저장소의 `docs/data-preprocessing-handoff.md`, CSV 7개 점검 명령은 루트 `README.md`에 있다.
