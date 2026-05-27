# aws-usage-viewer
- AWS 비용 시각화 통합

## 구성요소 
- clickhouse 
- metabase

## clickhouse setup
- clickhouse를 별도로 설치합니다. (이미 있다면 지나가도 좋습니다.)

```bash
curl https://clickhouse.com/cli | sh
clickhousectl local install stable
clickhousectl local server start
clickhousectl local client
```
[참조](https://clickhouse.com/docs/ko/install/quick-install)