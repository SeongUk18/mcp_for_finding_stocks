"""
Trend Scraper MCP 서버 설정.
.env에서 키와 유튜브 채널 목록(JSON)을 읽는다.
"""
import json
import os
from typing import Any, Dict, List

from dotenv import load_dotenv

load_dotenv()

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")

NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "")

_raw_channels = os.getenv("YOUTUBE_CHANNELS", "[]")

try:
    parsed = json.loads(_raw_channels)
    if isinstance(parsed, list):
        YOUTUBE_CHANNELS: List[Dict[str, Any]] = parsed
    else:
        YOUTUBE_CHANNELS = []
except Exception:
    YOUTUBE_CHANNELS = []

YOUTUBE_CHANNEL_IDS: List[str] = [
    c.get("id", "")
    for c in YOUTUBE_CHANNELS
    if isinstance(c, dict) and c.get("id")
]

