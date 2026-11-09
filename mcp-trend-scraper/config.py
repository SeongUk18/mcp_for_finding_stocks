"""
Trend Scraper MCP 서버 설정.
.env에서 네이버 검색 API 키를 읽는다.
"""
import os

from dotenv import load_dotenv

load_dotenv()

NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "")
