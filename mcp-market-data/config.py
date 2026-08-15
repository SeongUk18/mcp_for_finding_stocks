"""
Market Data MCP 서버 설정.
한국투자증권 API 키는 .env에서 로드.
"""
import os
from dotenv import load_dotenv

load_dotenv()

KIS_APPKEY = os.getenv("KIS_APPKEY", "")
KIS_APPSECRET = os.getenv("KIS_APPSECRET", "")
KIS_BASE_URL = "https://openapi.koreainvestment.com:9443"

# 한투는 토큰 발급을 분당 1회로 제한한다. 프로세스 재시작이나 서버 간 중복 발급으로
# 403이 나지 않도록 토큰을 파일에 캐시한다. 미국장 서버와 같은 경로를 공유한다.
KIS_TOKEN_CACHE = os.getenv(
    "KIS_TOKEN_CACHE",
    os.path.join(
        os.getenv("LOCALAPPDATA") or os.path.expanduser("~"),
        "kis-mcp",
        "token.json",
    ),
)
