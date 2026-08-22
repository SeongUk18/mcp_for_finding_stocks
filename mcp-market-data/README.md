# MCP Market Data (Python)

한국투자증권 Open API로 시세·수급을 조회하는 MCP 서버입니다.

## Tools

| 도구 | 역할 |
|------|------|
| **get_kr_market_movers** | 거래량·등락률·체결강도·VI 순위 (후보 발굴) |
| **get_current_price_and_chart** | 현재가 + 이평/볼린저/ATR/52주/코스피 대비 상대강도 + 호가 |
| **get_institutional_buying** | 수급 종합 (확정치·가집계·창구·외국계·프로그램·공매도·융자) |
| **get_kr_news_and_opinion** | 한투 속보 + 증권사 투자의견·목표주가 + 실적 컨센서스 |

### 수급 데이터의 성격이 서로 다릅니다

`get_institutional_buying`은 성격이 다른 7가지를 한 번에 돌려줍니다. 섞어서 읽으면 안 됩니다.

| 항목 | 내용 | 신선도 |
|---|---|---|
| `daily_confirmed` | 일별 확정 순매수 (금액, **원** 단위) | 장 마감 후 확정 |
| `daily_detail` | 일별 순매수 (수량). 외국인 **등록/미등록** 분리 | 장 마감 후 확정 |
| `intraday_estimate` | 장중 외인·기관 추정 가집계 | **추정치. 확정 아님** |
| `brokers` | 증권사 창구별 매수·매도 상위 5 | 장중 실시간 |
| `foreign_brokers` | 외국계 창구 매매 추이 | 장중 실시간 |
| `program` | 프로그램매매 순매수 (시간대별) | 장중 실시간 |
| `short_sale` / `loan` | 공매도 비중 / 신용융자 잔고 | 전 거래일까지 |

창구 분석이 핵심입니다. JP모간·골드만·UBS 같은 외국계 창구가 매수 상위에 있으면
외국인 자금 유입으로 해석하는 게 일반적입니다.

### 순위 조회 시 주의

`exclude_noise=True`(기본)를 끄면 거래량 상위가 **KODEX 인버스 같은 ETF로 도배**됩니다.
한투의 대상제외 마스크(10자리)로 ETF·ETN·우선주·관리종목·스팩을 걸러냅니다.

등락률 순위는 한투가 돌려주는 순서가 등락률과 일치하지 않아
(상승 조회인데 8%가 1위, 30%가 2위로 오는 식) 서버에서 다시 정렬합니다.

## 설정

1. **`.env`** 파일에 API 키 입력 (이미 있음, 값만 채우면 됨)
   - `KIS_APPKEY`, `KIS_APPSECRET`: [한국투자증권 API 포털](https://apiportal.koreainvestment.com)에서 앱 등록 후 발급
2. 현재 구현은 **조회 전용**(현재가·차트·수급)이라 계좌번호는 사용하지 않음

## 설치 및 실행 (가상환경 권장)

```powershell
cd c:\Users\adult\Desktop\mcp_for_finding_stocks\mcp-market-data
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py
```

시스템 Python 사용 시:

```powershell
pip install -r requirements.txt
python main.py
```

MCP 클라이언트(Cursor/Claude Desktop)에서는 `command`에 위 `.venv\Scripts\python.exe` 경로를 넣고, `args`에 `main.py` 절대 경로를 넣으면 됩니다.

## 한투 API 사용 시 주의

- **토큰 발급은 분당 1회 제한**입니다. `%LOCALAPPDATA%\kis-mcp\token.json`에 캐시해서
  프로세스 재시작과 미국장 서버 간에 공유합니다. 이걸 안 하면 403이 납니다.
- 일봉 API는 한 번에 최대 100건, 지수 일봉은 50건입니다.
  MA200·60일 상대강도를 계산하려면 구간을 뒤로 밀며 페이징해야 합니다 (`get_daily_bars`).
- 투자자 매매 대금은 **천원 단위**로 옵니다. 서버가 ×1000 해서 원으로 반환합니다.
- 신용융자 잔고(`daily-loan-trans`)는 `MRKT_DIV_CLS_CODE`가 **3(종목)** 이어야 합니다.
  1이나 2를 넣으면 종목코드를 줘도 시장 전체 집계가 돌아옵니다.
- VI 발동 종목은 **장중에만** 값이 있습니다.

**참고**: 터미널에서 `python main.py`로 직접 실행하면 서버가 stdio 입력을 기다립니다. **엔터를 누르거나 키보드 입력을 하면** `Invalid JSON` / `validation error for JSONRPCMessage` 에러가 납니다. 서버는 Cursor나 Claude가 **자동으로 실행**할 때만 정상 사용됩니다. 직접 실행해서 동작 확인만 할 때는 아무 키도 누르지 말고 Ctrl+C로 종료하세요.
