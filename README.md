# curhouse

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

`http://localhost:3000` 접속. 첫 실행이면 관리자 계정만 잡혀있고 비어있는 상태. 설정은 `./metabase-data/`에 영속 (gitignore).

### 데이터소스 + 대시보드 자동 프로비저닝

```bash
MB_ADMIN_EMAIL=you@example.com \
MB_ADMIN_PASSWORD='YourStrong#Pass1' \
python3 scripts/provision_metabase.py
```

스크립트가 멱등(idempotent)하게 수행하는 작업:

1. 첫 실행이면 `/api/setup`으로 어드민 계정 생성, 아니면 로그인
2. ClickHouse 데이터소스 `datahouse-clickhouse` 등록/갱신
   - host=`host.docker.internal`, port=`8123`, db=`aws_billing` (env로 오버라이드 가능)
3. Native SQL Question 7개 upsert (이름으로 매칭)
4. 대시보드 `AWS 비용 대시보드` upsert
   - 상단 필터 (모두 옵셔널, 디폴트 없음, AND 결합):
     - `단일 날짜` — 캘린더에서 하루 선택 (`line_item_usage_start_date` 기준)
     - `기간 (범위)` — 캘린더에서 start~end 선택 (같은 컬럼)
     - `월` — `_billing_period` (`YYYY-MM`) 드롭다운
     - `서비스` — `line_item_product_code` 드롭다운
   - 모든 카드에 네 필터 매핑

   > 세 날짜 필터는 같은 시간축을 다른 입력 방식으로 표현. 동시에 두 개 켜면 AND라 의도와 다르게 좁아질 수 있어서, 보통은 **하나만** 사용.
5. 필터 값 캐시 재스캔 (`POST /api/database/{id}/rescan_values`)

OSS Metabase는 공식 serialization이 막혀있어서 이 스크립트가 git에 올라가는 "단일 진실원"이에요. GUI에서 카드를 추가/수정해도 다음 스크립트 실행 시 덮어쓰여집니다.

#### 환경변수

| 변수 | 기본값 | 비고 |
|---|---|---|
| `MB_URL` | `http://localhost:3000` | |
| `MB_ADMIN_EMAIL` | (필수) | |
| `MB_ADMIN_PASSWORD` | (필수) | 첫 실행 시 Metabase 정책상 강비번 필요 |
| `CH_HOST` | `host.docker.internal` | Linux도 `extra_hosts` 매핑으로 동작 |
| `CH_PORT` | `8123` | |
| `CH_DB` | `aws_billing` | |
| `CH_USER` | `default` | |
| `CH_PASSWORD` | (빈값) | |

> Metabase v0.49+는 ClickHouse 드라이버를 번들로 포함. 별도 설치 불필요.

### 필터값 수동 갱신

`datahouse sync`로 새 billing period가 들어왔는데 대시보드 드롭다운에 안 보이면:

```bash
python3 scripts/provision_metabase.py  # rescan_values 포함
```

또는 단독으로:

```bash
curl -X POST $MB_URL/api/database/<db_id>/rescan_values \
  -H "X-Metabase-Session: <session>"
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
