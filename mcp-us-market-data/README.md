# mcp-us-market-data — 미국주식 MCP 서버

미국장 후보 발굴부터 시세·수급·뉴스까지 담당합니다.
**키 없이도 동작하지만, 한국투자증권 키를 넣으면 시세가 실시간이 됩니다.**

## 도구

| 도구 | 역할 | 한국장 대응 |
|------|------|-------------|
| `search_serenity_tickers` | 세레니티(@aleabitoreddit) X 게시물에서 언급 티커 집계 | `search_youtube_keywords` |
| `get_us_market_movers` | 상승률·거래량·급등·시총 순위 (한투) | — |
| `get_us_institutional_flow` | 기관 보유(13F) + 공매도 + 상대강도 + 애널리스트 | `get_institutional_buying` |
| `get_us_price_and_chart` | 실시간 시세 + 이평/볼린저/ATR + 호가 + 분봉 | `get_current_price_and_chart` |
| `get_us_news_sentiment` | 영문 + 한글 뉴스 | `search_news_for_trend_keywords` |

---

## 데이터 소스 두 개를 섞어 씁니다

| 데이터 | 소스 | 이유 |
|---|---|---|
| 실시간 체결가·당일 봉 | **한투** | yfinance 일봉에는 당일 데이터가 아예 없음 |
| 프리·애프터 분봉 (ET 04:00~20:00) | **한투** | 미국 단타는 갭이 핵심인데 yfinance로는 안 보임 |
| 호가 10단계·잔량 | **한투** | yfinance에 없는 데이터 |
| 상승률·거래량·급등·시총 순위 | **한투** | 세레니티와 별개인 후보 발굴 채널 |
| 한글 뉴스 | **한투** | 재료 성격을 빠르게 훑기 좋음 |
| 이동평균·볼린저·ATR·상대강도 | yfinance | 1년치 장기 시계열이 필요 (한투는 1회 100건) |
| 13F 기관 보유·공매도·애널리스트 | yfinance | **한투 해외 API에 아예 없음** |
| 영문 뉴스 | yfinance | 종목 연관도(`is_primary_subject`) 판별 가능 |

한투 시세는 응답 `stat` 필드가 `"무료실시간"`으로 나옵니다. 15분 지연이 아닙니다.

**당일 봉 병합**: yfinance 1년치 일봉 뒤에 한투의 당일 봉을 이어 붙인 뒤 지표를 계산합니다.
이렇게 하지 않으면 이동평균과 상대강도가 하루 늦은 값이 됩니다.

**진행 중 세션의 거래량**: 장중에는 하루치 거래량이 아직 안 쌓였습니다.
그대로 20일 평균과 비교하면 항상 미달로 나오므로, `volume_ratio_vs_avg20`은
**직전 완결 세션 기준**으로 계산하고 진행 중 거래량은 `partial_session_volume`에 따로 담습니다.

키가 없으면 시세도 yfinance로 폴백하고, `get_us_market_movers`만 사용할 수 없습니다.
어느 소스를 썼는지는 응답의 `price_source`에 표시됩니다.

---

## 미국장은 "수급"이 다릅니다

한국은 매일 외국인·기관 순매수가 공시되지만, **미국은 그런 데이터가 존재하지 않습니다.**
그래서 `get_us_institutional_flow`는 네 가지를 묶어서 돌려주고, 각각 신선도가 다릅니다.

| 항목 | 내용 | 갱신 주기 | 단타 활용도 |
|------|------|-----------|-------------|
| `ownership` | 기관 보유비율, 주요 기관별 보유량·증감 (13F) | 분기 (최대 45일 지연) | 낮음 — 누가 들고 있나 확인용 |
| `short_interest` | 공매도 잔고, 유동주식 대비 %, Days to Cover | 월 2회 (1~2주 지연) | 중간 — 숏스퀴즈 가능성 |
| `relative_strength` | SPY/QQQ 대비 초과수익률 + 거래량 급증 배율 | **일별** | **높음 — 사실상 유일한 실시간 수급 대리지표** |
| `analyst` | 등급 분포, 최근 상향/하향, 목표주가 | 이벤트 발생 시 | 중간 — 재료성 |

**핵심**: 1·2번은 지연 데이터라 "오늘 누가 샀는지"를 절대 알려주지 않습니다.
한국의 "외인 3일 연속 순매수" 같은 판단을 미국에서 하려면 **3번(상대강도 + 거래량)** 을 봐야 합니다.

---

## 세레니티(Serenity) 데이터 출처

[@aleabitoreddit](https://x.com/aleabitoreddit)은 AI 하드웨어·반도체 공급망 리서치로 알려진 X 계정입니다.

X API는 2023년부터 유료(검색 가능한 Basic 기준 월 $200)라 쓰지 않습니다.
대신 [yan-labs/serenity-aleabitoreddit](https://github.com/yan-labs/serenity-aleabitoreddit) 저장소가
자동 갱신하는 **공개 게시물 아카이브(CSV)** 를 받아서 로컬에 캐시합니다.

- 캐시 위치: `.cache/serenity_tweets.csv` (약 4.5MB)
- 기본 TTL 1시간. TTL이 지나면 ETag 조건부 요청으로 변경분만 확인 (304면 재다운로드 없음)
- 응답의 `archive.newest_post_utc`로 **데이터가 얼마나 최신인지 반드시 확인**하세요
- 아카이브는 주기적 동기화라 최근 몇 시간 게시물은 빠져 있을 수 있습니다

이 도구는 감성 점수를 매기지 않습니다. 원문 발췌를 그대로 넘기니 직접 읽고 판단하세요.

---

## 설치

```powershell
cd c:\Users\adult\Desktop\mcp_for_finding_stocks\mcp-us-market-data
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py
```

`.env` 없이도 동작합니다. 한투 키는 `mcp-market-data/.env`에 있으면 자동으로 읽어오므로
따로 복사할 필요가 없습니다. 자세한 옵션은 `.env.example`을 보세요.

서버가 뜰 때 stderr에 시세 소스가 표시됩니다:
`us-market-data MCP: stdio 대기 중. 시세 소스: 한투 실시간 + yfinance`

---

## 한투 API 사용 시 주의

- **토큰 발급은 분당 1회 제한**입니다. 토큰을 `%LOCALAPPDATA%\kis-mcp\token.json`에 캐시해서
  프로세스 재시작과 한국장/미국장 서버 간에 공유합니다. 이걸 안 하면 403이 납니다.
- 거래소 코드(`EXCD`)를 명시해야 하는데 티커만 아는 경우가 많아,
  나스닥 → 뉴욕 → 아멕스 순으로 조회해서 체결가가 잡히는 거래소를 자동 채택합니다.
- 순위 API의 급등 상위에는 저가주·소형주가 많이 섞입니다.
  `min_volume`(0~4)으로 반드시 걸러서 보세요.

## 데이터 한계 (반드시 인지할 것)

- **yfinance는 비공식 라이브러리입니다.** Yahoo가 응답 구조를 바꾸면 일부 필드가 `null`이 될 수 있습니다.
  각 조회는 개별적으로 감싸져 있어서 하나가 실패해도 나머지는 나오고, 실패 사유는 `errors`에 남습니다.
- 한투 키가 없으면 시세가 yfinance 폴백이라 **당일 데이터가 없습니다.** `price_source`를 확인하세요.
- `Ticker.get_news()`가 종목과 무관한 일반 시장 피드를 반환하는 문제가 있어
  `yf.Search`를 우선 사용합니다. 응답의 `is_primary_subject`가 `true`인 기사가 해당 종목이 주제인 기사입니다.
- 한투 한글 뉴스는 티커 필터가 잘 안 걸리는 편입니다. 필터 결과가 비면
  해외 시장 전체 속보를 반환하고 `korean_news_note`로 알려줍니다.
- 상대강도는 지수 대비 강도일 뿐이며, 실제 기관 매수를 증명하지 않습니다.
- 13F·공매도·애널리스트는 한투에 없어 yfinance 전용이며, 지연 데이터입니다.
