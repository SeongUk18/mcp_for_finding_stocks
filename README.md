# 급등주 예측 에이전트 — MCP 서버 (Python)

한국·미국 주식 추천 데이터를 제공하는 MCP 서버 세 개입니다.

| 서버 | 시장 | 역할 | 도구 |
|------|------|------|------|
| **mcp-trend-scraper** | 🇰🇷 | 소셜/재료 수집 | `search_youtube_keywords`, `search_news_for_trend_keywords` |
| **mcp-market-data** | 🇰🇷 | 퀀트/차트/수급 | `get_kr_market_movers`, `get_current_price_and_chart`, `get_institutional_buying`, `get_kr_news_and_opinion` |
| **mcp-us-market-data** | 🇺🇸 | 미국장 전 과정 | `search_serenity_tickers`, `get_us_market_movers`, `get_us_price_and_chart`, `get_us_institutional_flow`, `get_us_news_sentiment` |

미국장 서버는 **키 없이도 동작**하지만(yfinance + 공개 아카이브),
한투 키가 있으면 **무료 실시간 시세·프리마켓 분봉·호가·순위·한글 뉴스**까지 씁니다.
키는 `mcp-market-data/.env`에 있으면 자동으로 읽어옵니다.
자세한 내용은 [mcp-us-market-data/README.md](mcp-us-market-data/README.md)를 보세요.

---

## 사전 준비

1. **Python 3.10+** 설치 ([python.org](https://www.python.org/downloads/))
2. 각 서버 폴더에 **`.env`** 파일이 있음 → API 키만 채우면 됨 (미국장 서버는 불필요)
3. **Trend Scraper**: `config.py`의 `YOUTUBE_CHANNELS`에 실제 유튜브 채널 ID 입력 시 검색 결과 나옴

---

## 프로젝트 구조

```
mcp_for_finding_stocks/
├── mcp-trend-scraper/       # 유튜브·뉴스
│   ├── .env                 # API 키 (YOUTUBE, NAVER)
│   ├── config.py
│   ├── main.py
│   ├── services/
│   └── requirements.txt
├── mcp-market-data/         # 한투 시세·수급
│   ├── .env                 # API 키 (KIS_APPKEY, KIS_APPSECRET)
│   ├── config.py
│   ├── main.py
│   ├── services/
│   └── requirements.txt
├── mcp-us-market-data/      # 미국주식 (키 불필요)
│   ├── config.py
│   ├── main.py
│   ├── services/            # yf_service, serenity_service, indicators
│   ├── .cache/              # 세레니티 아카이브 캐시 (자동 생성)
│   └── requirements.txt
├── .mcp.json                # Claude Code용 서버 등록
└── README.md
```

---

## 설치 및 실행

각 서버마다 **가상환경** 사용을 권장합니다.

### mcp-market-data

```powershell
cd c:\Users\adult\Desktop\mcp_for_finding_stocks\mcp-market-data
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py
```

### mcp-trend-scraper

```powershell
cd c:\Users\adult\Desktop\mcp_for_finding_stocks\mcp-trend-scraper
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py
```

### mcp-us-market-data

```powershell
cd c:\Users\adult\Desktop\mcp_for_finding_stocks\mcp-us-market-data
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py
```

---

## Claude Desktop MCP 연결 (Windows)

가상환경의 Python을 쓰려면 **command**에 `.venv\Scripts\python.exe` 경로를 넣습니다.

**Claude Desktop**: `%APPDATA%\Claude\claude_desktop_config.json`

가상환경은 **서버별로 따로** 있습니다. `command`에는 각 서버 폴더 안의
`.venv\Scripts\python.exe` 경로를 넣어야 합니다.

```json
{
  "mcpServers": {
    "trend-scraper": {
      "command": "c:/Users/{사용자}/Desktop/mcp_for_finding_stocks/mcp-trend-scraper/.venv/Scripts/python.exe",
      "args": ["c:/Users/{사용자}/Desktop/mcp_for_finding_stocks/mcp-trend-scraper/main.py"],
      "cwd": "c:/Users/{사용자}/Desktop/mcp_for_finding_stocks/mcp-trend-scraper"
    },
    "market-data": {
      "command": "c:/Users/{사용자}/Desktop/mcp_for_finding_stocks/mcp-market-data/.venv/Scripts/python.exe",
      "args": ["c:/Users/{사용자}/Desktop/mcp_for_finding_stocks/mcp-market-data/main.py"],
      "cwd": "c:/Users/{사용자}/Desktop/mcp_for_finding_stocks/mcp-market-data"
    },
    "us-market-data": {
      "command": "c:/Users/{사용자}/Desktop/mcp_for_finding_stocks/mcp-us-market-data/.venv/Scripts/python.exe",
      "args": ["c:/Users/{사용자}/Desktop/mcp_for_finding_stocks/mcp-us-market-data/main.py"],
      "cwd": "c:/Users/{사용자}/Desktop/mcp_for_finding_stocks/mcp-us-market-data"
    }
  }
}
```

- `.env`에 이미 API 키를 넣었다면 위 설정만으로 동작합니다.
- 키를 MCP 설정에서만 넣으려면 각 서버에 `"env": { "KIS_APPKEY": "...", ... }` 형태로 추가하면 됩니다.
- **Claude Code**에서는 저장소 루트의 `.mcp.json`이 세 서버를 자동으로 등록합니다.

---

## API 발급

| 서버 | 필요한 키 | 발급처 |
|------|-----------|--------|
| trend-scraper | YOUTUBE_API_KEY, NAVER_CLIENT_ID, NAVER_CLIENT_SECRET | [Google Cloud](https://console.cloud.google.com), [Naver Developers](https://developers.naver.com) |
| market-data | KIS_APPKEY, KIS_APPSECRET | [한국투자증권 API 포털](https://apiportal.koreainvestment.com) |
| **us-market-data** | (선택) KIS_APPKEY, KIS_APPSECRET | 없어도 동작하지만 넣으면 실시간 시세·호가·순위가 열립니다. market-data와 같은 키를 씁니다 |
