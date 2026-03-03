# 급등주 예측 에이전트 — MCP 서버 (Python)

한국 주식 단타용 데이터를 제공하는 MCP 서버 두 개입니다.

| 서버 | 역할 | 도구 |
|------|------|------|
| **mcp-trend-scraper** | 소셜/재료 수집 | `search_youtube_tickers`, `search_news_sentiment` |
| **mcp-market-data** | 퀀트/차트 분석 | `get_current_price_and_chart`, `get_institutional_buying` |

---

## 사전 준비

1. **Python 3.10+** 설치 ([python.org](https://www.python.org/downloads/))
2. 각 서버 폴더에 **`.env`** 파일이 있음 → API 키만 채우면 됨
3. **Trend Scraper**: `config.py`의 `YOUTUBE_CHANNELS`에 실제 유튜브 채널 ID 입력 시 검색 결과 나옴

---

## 프로젝트 구조

```
finding_stocks/
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
└── README.md
```

---

## 설치 및 실행

각 서버마다 **가상환경** 사용을 권장합니다.

### mcp-market-data

```powershell
cd c:\Users\adult\Desktop\finding_stocks\mcp-market-data
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py
```

### mcp-trend-scraper

```powershell
cd c:\Users\adult\Desktop\finding_stocks\mcp-trend-scraper
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py
```

---

## Claude Desktop MCP 연결 (Windows)

가상환경의 Python을 쓰려면 **command**에 `.venv\Scripts\python.exe` 경로를 넣습니다.

**Claude Desktop**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "trend-scraper": {
      "command": "c:/Users/{사용자}/Desktop/mcp_for_finding_stocks/.venv/Scripts/python.exe",
      "args": ["c:/Users/{사용자}/Desktop/mcp_for_finding_stocks/main.py"],
      "cwd": "c:/Users/{사용자}/Desktop/mcp_for_finding_stocks/mcp-trend-scraper"
    },
    "market-data": {
      "command": "c:/Users/{사용자}/Desktop/mcp_for_finding_stocks/.venv/Scripts/python.exe",
      "args": ["c:/Users/{사용자}/Desktop/mcp_for_finding_stocks/mcp-market-data/main.py"],
      "cwd": "c:/Users/{사용자}/Desktop/mcp_for_finding_stocks/mcp-market-data"
    }
  }
}
```

- `.env`에 이미 API 키를 넣었다면 위 설정만으로 동작합니다.
- 키를 MCP 설정에서만 넣으려면 각 서버에 `"env": { "KIS_APPKEY": "...", ... }` 형태로 추가하면 됩니다.

---

## API 발급

| 서버 | 필요한 키 | 발급처 |
|------|-----------|--------|
| trend-scraper | YOUTUBE_API_KEY, NAVER_CLIENT_ID, NAVER_CLIENT_SECRET | [Google Cloud](https://console.cloud.google.com), [Naver Developers](https://developers.naver.com) |
| market-data | KIS_APPKEY, KIS_APPSECRET | [한국투자증권 API 포털](https://apiportal.koreainvestment.com) |
