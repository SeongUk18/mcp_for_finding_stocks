# mcp-us-market-data — 미국주식 MCP 서버

미국장 후보 발굴부터 시세·수급·뉴스까지 담당합니다. **API 키가 필요 없습니다.**

## 도구

| 도구 | 역할 | 한국장 대응 |
|------|------|-------------|
| `search_serenity_tickers` | 세레니티(@aleabitoreddit) X 게시물에서 언급 티커 집계 | `search_youtube_tickers` |
| `get_us_institutional_flow` | 기관 보유(13F) + 공매도 + 상대강도 + 애널리스트 | `get_institutional_buying` |
| `get_us_price_and_chart` | 시세 + 이평/볼린저/ATR + 상대강도 | `get_current_price_and_chart` |
| `get_us_news_sentiment` | 종목 연관 최신 뉴스 | `search_news_sentiment` |

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

`.env`는 없어도 동작합니다. 기본 동작을 바꾸려면 `.env.example`을 참고하세요.

---

## 데이터 한계 (반드시 인지할 것)

- **yfinance는 비공식 라이브러리입니다.** Yahoo가 응답 구조를 바꾸면 일부 필드가 `null`이 될 수 있습니다.
  각 조회는 개별적으로 감싸져 있어서 하나가 실패해도 나머지는 나오고, 실패 사유는 `errors`에 남습니다.
- 시세는 실시간이 아니라 **15분 지연**일 수 있습니다.
- `Ticker.get_news()`가 종목과 무관한 일반 시장 피드를 반환하는 문제가 있어
  `yf.Search`를 우선 사용합니다. 응답의 `is_primary_subject`가 `true`인 기사가 해당 종목이 주제인 기사입니다.
- 상대강도는 지수 대비 강도일 뿐이며, 실제 기관 매수를 증명하지 않습니다.
