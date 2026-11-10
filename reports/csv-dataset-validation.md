# CSV 데이터셋 생성과 검증

모델 담당자가 파일을 직접 확인하고 CSV로 불러올 수 있도록 전달 계약을 2.0.0으로 변경했다. 새 데이터 버전은 listenbrainz-8week-v2이며 저장소 옆 공유 데이터셋 폴더에 생성했다. 생성기는 원본 8주에 공통 정제·시간 분할을 적용해 CSV를 직접 저장한다.

## 변경한 내용과 이유

- 학습 청취 횟수, 사용자·곡 매핑, 검증·테스트 정답과 평가 사용자 목록을 CSV 7개로 제공한다. 사용자가 찾기 쉬운 폴더 경로를 README와 후속 작업 프롬프트에 안내한다.
- 헤더와 UTF-8 BOM을 쓰고 정수 자료형을 명시한다. CSV의 쉼표·따옴표·개행, 한국어·악센트, 실제 NA·NULL 문자열을 보존한다.
- 모델이 사용할 SciPy CSR은 학습 CSV에서 메모리에 구성한다. 패키지 README에 CSV 로딩, 곡명·가수명 조회, 평가 정답 읽기 예제를 포함했다.
- 각 CSV의 모든 행을 생성 관계 및 원본에서 독립적으로 계산한 기대값과 대조한다. 무결성·자료형·집계 기록은 manifest에 보관한다.

## 실측 결과

| 파일 | CSV 행 수 | 원본 기준 누락·초과 |
| --- | ---: | ---: |
| train_interactions.csv | 2,739,292 | 0 |
| items.csv | 1,525,005 | 0 |
| users.csv | 5,000 | 0 |
| validation_targets.csv | 193,323 | 0 |
| test_targets.csv | 201,203 | 0 |
| validation_users.csv | 3,922 | 0 |
| test_users.csv | 3,852 | 0 |

학습 CSV는 고유 사용자·곡 쌍 2,739,292행이며 listen_count 합계는 6,623,584건이다. 검증·테스트 정답은 각 기간의 평가 대상 사용자에 대한 고유 정답 쌍이다. 데이터의 행 단위와 제외 규칙은 [공통 계약](../docs/data-contract.md)을 따른다.

CSV 7개를 기존 검증된 v1 파일의 모든 값·순서와도 대조해 불일치 0건을 확인했다. 패키지 README의 예제를 실제 파일에서 실행해 학습 행렬 크기 (5000, 1525005), 비제로 2,739,292개와 곡명·가수명 조회를 확인했다.

회귀 테스트 26개가 통과했다. CSV 특수문자·개행·결측값으로 오인하기 쉬운 이름·큰 정수 ID·빈 데이터·BOM 누락, 시간 분할·미래 정보 누수, 잘못된 좌표·횟수·metadata, 실패 후 재시도와 동시 생성을 검사했다. 기존 계약의 저장 파일을 다시 검증하는 경로도 확인했다.

## 직접 확인하는 방법

저장소 루트에서 다음 명령으로 현재 CSV 전달본을 독립 검증한다. matches가 true이고 모든 누락·초과가 0이면 통과다.

```bash
.venv/bin/python scripts/validate_dataset.py --config config/paths.local.toml \
  --package "../공유 데이터셋/listenbrainz-8week-v2"
```

CSV 전달본의 README를 열고 Python 예제를 같은 폴더에서 실행하면 파일 무결성, 학습 행렬과 곡 이름을 확인할 수 있다. [소비자 로딩 예제](../docs/dataset-package-readme.md)

큰 학습 CSV와 곡 CSV는 Excel 한 시트의 행 한도를 넘으므로 전체 행을 사용하는 분석은 CSV 파서로 수행한다.

CSV 전달 형식 변경은 [Issue #11](https://github.com/ML-Team4/ML-Team4-Preprocessing/issues/11)에 기록했다.
