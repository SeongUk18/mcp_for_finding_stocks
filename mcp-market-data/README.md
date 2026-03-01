# MCP Market Data (Python)

한국투자증권 Open API로 시세·수급을 조회하는 MCP 서버입니다.

## Tools

- **get_current_price_and_chart**: 현재가 + 기술적 분석(이평선, 거래량, 눌림목, 볼린저 등)
- **get_institutional_buying**: 외국인/기관/개인 수급 (최근 N일). 금액은 **원** 단위로 반환 (API는 천원 단위로 오므로 ×1000 적용).

## 설정

1. **`.env`** 파일에 API 키 입력 (이미 있음, 값만 채우면 됨)
   - `KIS_APPKEY`, `KIS_APPSECRET`: [한국투자증권 API 포털](https://apiportal.koreainvestment.com)에서 앱 등록 후 발급
2. 현재 구현은 **조회 전용**(현재가·차트·수급)이라 계좌번호는 사용하지 않음

## 설치 및 실행 (가상환경 권장)

```powershell
cd c:\Users\adult\Desktop\finding_stocks\mcp-market-data
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

**참고**: 터미널에서 `python main.py`로 직접 실행하면 서버가 stdio 입력을 기다립니다. **엔터를 누르거나 키보드 입력을 하면** `Invalid JSON` / `validation error for JSONRPCMessage` 에러가 납니다. 서버는 Cursor나 Claude가 **자동으로 실행**할 때만 정상 사용됩니다. 직접 실행해서 동작 확인만 할 때는 아무 키도 누르지 말고 Ctrl+C로 종료하세요.
