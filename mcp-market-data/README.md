# MCP Market Data (Python)

한국투자증권 Open API로 시세·수급을 조회하는 MCP 서버입니다.

## Tools

| 도구 | 역할 |
|------|------|
| **get_kr_market_movers** | 거래량·등락률·체결강도·VI 순위 (후보 발굴) |
| **get_current_price_and_chart** | 현재가 + 이평/볼린저/ATR/52주/코스피 대비 상대강도 + 호가 |
| **get_institutional_buying** | 수급 종합 (확정치·가집계·창구·외국계·프로그램·공매도·융자) |
| **get_kr_investor_ranking** | 시장 전체 외국인·기관 순매수·순매도 상위 30종목 (장중 가집계) |
| **scan_kr_setups** | 백테스트로 검증된 셋업(급락 반등·신고가권 눌림·신고가 돌파 눌림)에 해당하는 종목 스캔 |
| **get_kr_news_and_opinion** | 한투 속보 + 증권사 투자의견·목표주가 + 실적 컨센서스 |
| **get_kr_disclosures** | DART 공시(성격 분류) + 임원·주요주주 소유보고 + 5% 대량보유 |
| **get_kr_index_snapshot** | 코스피·코스닥 지수 등락률 + 상승·하락·보합 종목 수 |
| **get_kr_daily_history** | 종목·지수 과거 일봉 기간 조회 (수정주가, `.cache/daily/` 로컬 캐시) |
| **backtest_kr_rule** | 가격·거래량 규칙 백테스트 (다음 날 시가 진입, ATR 손절, 표본 내/외, baseline 비교) |

`get_current_price_and_chart`는 코스피·코스닥에 더해 **소속 업종 지수 대비** 상대강도(`vs_index.SECTOR`)와
**시장경보**(`current.market_warning`: 투자주의/경고/위험·단기과열·거래정지·VI)를 함께 줍니다.
업종 코드는 한투가 배포하는 종목 마스터(`kospi/kosdaq_code.mst`, 키 불필요)에서 읽고 하루 캐시합니다.

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

한투 일별 투자자 API는 당일 행을 장 종료 후에야 채웁니다. 그 전에는 금액 API가 당일 행을 0으로 주고,
수량 API가 당일 시세를 전 거래일 날짜로 붙여 주는 일이 있습니다. 그래서 행 순서가 아니라
`stck_bsop_date`로만 날짜를 붙이고, 당일 0값 행은 `unconfirmed_dates`로 빼서 합계에서 제외합니다.
일봉과 대조해 다른 날짜 값이 붙은 행은 `daily_detail.excluded_rows`로 빼고 `errors.date_check`에 남깁니다.
`as_of_date`(마지막 확정일)와 `today_included`로 오늘 수급이 들어 있는지 확인하세요.

### 분봉

`get_current_price_and_chart(chart_type="5min")`처럼 분봉을 고르면 하루치 1분봉을 시각을 되감으며 모아
(당일 30건, 과거 날짜 120건씩) 겹치는 봉을 지우고 N분봉으로 묶습니다. `date=YYYYMMDD`로 과거 날짜도 됩니다.
분봉이 없거나 실패하면 `errors.minute_chart`에 남기고 **일봉으로 대체하지 않습니다**.

창구 분석이 핵심입니다. JP모간·골드만·UBS 같은 외국계 창구가 매수 상위에 있으면
외국인 자금 유입으로 해석하는 게 일반적입니다.

### 순위 조회 시 주의

`exclude_noise=True`(기본)를 끄면 거래량 상위가 **KODEX 인버스 같은 ETF로 도배**됩니다.
한투의 대상제외 마스크(10자리)로 ETF·ETN·우선주·관리종목·스팩을 걸러냅니다.
투자주의·경고·위험 종목은 일부러 **포함**합니다. 급등주는 투자주의로 지정되는 일이 흔해서
빼면 단타 후보가 통째로 사라집니다. 경보 여부는 현재가 조회의 `market_warning`으로 확인합니다.

### 공시 (DART)

`get_kr_disclosures`는 [OpenDART](https://opendart.fss.or.kr) 키(무료)가 필요합니다. `.env`에 `DART_API_KEY`를 넣으세요.
보고서 제목으로 `risk`·`dilution`·`contract`·`earnings`·`shareholder_return`·`ownership`·`corporate_action`을
붙이지만 이건 공시 성격일 뿐 호재/악재 판단이 아닙니다. 종목코드 없이 부르면 시장 전체 최근 공시를 줍니다.

### 백테스트

`backtest_kr_rule`은 조건을 모두 만족한 날(t일 종가 기준)의 **다음 날 시가**에 진입하고,
ATR 배수 손절·목표 또는 보유일 만료로 청산합니다. 같은 날 손절가와 목표가를 모두 건드리면 손절로 칩니다.
최근 30%를 표본 외로 떼어 보고, 조건 없이 매일 샀을 때(`baseline_every_day`)와 비교합니다.
수급(외인·기관) 이력은 한투 API로 수년치를 받을 수 없어 조건으로 쓸 수 없습니다.

등락률 순위는 한투가 돌려주는 순서가 등락률과 일치하지 않아
(상승 조회인데 8%가 1위, 30%가 2위로 오는 식) 서버에서 다시 정렬합니다.

## 설정

1. **`.env`** 파일에 API 키 입력 (이미 있음, 값만 채우면 됨)
   - `KIS_APPKEY`, `KIS_APPSECRET`: [한국투자증권 API 포털](https://apiportal.koreainvestment.com)에서 앱 등록 후 발급
   - `DART_API_KEY`: [OpenDART](https://opendart.fss.or.kr)에서 무료 발급 (공시 도구용, 없으면 그 도구만 꺼짐)
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
