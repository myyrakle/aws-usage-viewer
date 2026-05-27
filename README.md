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

쿼리 예시:

```bash
docker exec -it datahouse-ch clickhouse-client --query "
  SELECT line_item_product_code, sum(line_item_unblended_cost) AS cost
  FROM aws_billing.cur_line_items
  WHERE _billing_period = '2026-04'
  GROUP BY line_item_product_code
  ORDER BY cost DESC
  LIMIT 10
"
```

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
