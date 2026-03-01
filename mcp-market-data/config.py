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
