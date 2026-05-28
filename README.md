# datahouse

AWS Cost and Usage Report 2.0 데이터를 로컬 ClickHouse에 적재하는 CLI.

## 요구사항

- Python 3.11+
- `uv` (의존성 관리)
- 실행 중인 로컬 ClickHouse (Docker, native, Cloud 무관)
- AWS 프로필 (`~/.aws/credentials`)

## 설치

```bash
uv sync
```

## 설정

```bash
cp config.example.toml config.toml
# config.toml 편집: aws.profile, cur.bucket_name 등
```

ClickHouse 비밀번호는 환경변수 `DATAHOUSE_CH_PASSWORD`로 덮어쓸 수 있다.

## 사용

### 1. AWS 리소스 프로비저닝 (계정당 한 번)

```bash
uv run datahouse setup
```

S3 버킷과 CUR 2.0 export를 생성한다. 멱등.

AWS가 첫 데이터를 S3에 떨구는 데 **최대 24시간** 소요.

### 2. 데이터 동기

```bash
uv run datahouse sync
```

- 변경된 billing period만 처리
- 멱등: 새 데이터 없으면 아무 동작 없음
- 실패한 period는 다음 실행에 재시도

특정 period만 강제 재로드:
```bash
uv run datahouse sync --only-period 2026-04
```

### 3. 상태 확인

```bash
uv run datahouse status
```

### 4. 테이블만 먼저 생성 (디버깅용)

```bash
uv run datahouse init-schema
```

## 로컬 ClickHouse 실행 예시

Docker로 간단하게:

```bash
docker run -d --name datahouse-ch \
  -p 8123:8123 -p 9000:9000 \
  -v $PWD/ch-data:/var/lib/clickhouse \
  clickhouse/clickhouse-server:latest
```

## Metabase 시각화

ClickHouse는 이미 위에서 띄웠다고 가정. Metabase만 compose로 띄움:

```bash
docker compose up -d
```

`http://localhost:3000`에서 첫 접속 시 관리자 계정 생성. 설정 파일은 `./metabase-data/`에 영속됨 (gitignore).

### Metabase에 ClickHouse 연결

1. 초기 셋업에서 "I'll add my data later" 선택
2. Admin → Databases → Add database
3. 다음과 같이 입력:
   - **Database type**: ClickHouse
   - **Host**: `host.docker.internal` (Metabase 컨테이너 → 호스트의 ClickHouse)
   - **Port**: `8123`
   - **Database name**: `aws_billing`
   - **Username**: `default`, **Password**: 비움

> Metabase v0.49+는 ClickHouse 드라이버를 번들로 포함. 별도 설치 불필요.

### 추천 쿼리 (Metabase Native SQL에 그대로 붙여넣기)

**1. 이번 달 총 비용**

```sql
SELECT sum(line_item_unblended_cost) AS total_cost
FROM aws_billing.cur_line_items
WHERE _billing_period = formatDateTime(now(), '%Y-%m')
  AND line_item_line_item_type != 'Tax';
```

**2. 서비스별 비용 Top 10 (이번 달)**

```sql
SELECT line_item_product_code AS service,
       sum(line_item_unblended_cost) AS cost
FROM aws_billing.cur_line_items
WHERE _billing_period = formatDateTime(now(), '%Y-%m')
GROUP BY service
ORDER BY cost DESC
LIMIT 10;
```

**3. 일별 비용 추이**

```sql
SELECT toDate(line_item_usage_start_date) AS day,
       sum(line_item_unblended_cost) AS cost
FROM aws_billing.cur_line_items
WHERE _billing_period = formatDateTime(now(), '%Y-%m')
GROUP BY day
ORDER BY day;
```

**4. 계정별 비용 (Organizations 환경)**

```sql
SELECT line_item_usage_account_id AS account,
       sum(line_item_unblended_cost) AS cost
FROM aws_billing.cur_line_items
WHERE _billing_period = formatDateTime(now(), '%Y-%m')
GROUP BY account
ORDER BY cost DESC;
```

**5. 리소스별 비용 Top 20**

```sql
SELECT line_item_resource_id AS resource,
       line_item_product_code AS service,
       sum(line_item_unblended_cost) AS cost
FROM aws_billing.cur_line_items
WHERE _billing_period = formatDateTime(now(), '%Y-%m')
  AND line_item_resource_id != ''
GROUP BY resource, service
ORDER BY cost DESC
LIMIT 20;
```

**6. 태그(`user_Project`)별 비용 — Map 컬럼 예시**

```sql
SELECT resource_tags['user_Project'] AS project,
       sum(line_item_unblended_cost) AS cost
FROM aws_billing.cur_line_items
WHERE _billing_period = formatDateTime(now(), '%Y-%m')
  AND resource_tags['user_Project'] != ''
GROUP BY project
ORDER BY cost DESC;
```

> Map 컬럼(`resource_tags`, `cost_category`, `product`, `discount`)은
> Metabase의 GUI Question Builder로는 다루기 어색해서 SQL Question 권장.

## 테스트

```bash
uv run pytest
```

## 동작 원리

- `setup`: S3 버킷 생성 + 정책 부착 + `bcm-data-exports create-export` 호출. 멱등.
- `sync`:
  1. S3에서 manifest 목록 조회
  2. 각 manifest의 ETag와 로컬 `.datahouse-state.json` 비교
  3. 변경된 billing period에 대해:
     - `ALTER TABLE ... DROP PARTITION '<period>'`
     - `INSERT INTO ... SELECT * FROM s3('<url>', '<key>', '<secret>', 'Parquet')`
     - count 확인 후 state 업데이트
- ClickHouse 엔진은 `MergeTree`, 파티션 키는 `_billing_period`. ReplacingMergeTree를 안 쓰는 이유는 CUR이 row 삭제도 발생시키기 때문 — 자세한 건 [spec](docs/superpowers/specs/2026-05-27-aws-cur-clickhouse-design.md) 참조.

## v1 범위 밖

- 자동 스케줄링 (cron 사용)
- 14일 이전 백필 (AWS Support 티켓 필요)
- ClickHouse 자동 프로비저닝
- Cost Explorer fallback
