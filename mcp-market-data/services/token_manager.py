"""
한국투자증권 OAuth2 토큰 관리.
만료 1시간 전 자동 재발급, 메모리 캐싱.
"""
import time
from typing import Any

import httpx

from config import KIS_BASE_URL, KIS_APPKEY, KIS_APPSECRET

_cached: dict[str, Any] = {"token": None, "expires_at": 0}
# 유효기간 24시간, 1시간 전 갱신
REFRESH_BEFORE_MS = 3600 * 1000
EXPIRE_MS = 24 * 3600 * 1000


def get_access_token() -> str:
    global _cached
    now_ms = int(time.time() * 1000)
    if _cached["token"] and now_ms < (_cached["expires_at"] - REFRESH_BEFORE_MS):
        return _cached["token"]

    url = f"{KIS_BASE_URL}/oauth2/tokenP"
    payload = {
        "grant_type": "client_credentials",
        "appkey": KIS_APPKEY,
        "appsecret": KIS_APPSECRET,
    }
    with httpx.Client() as client:
        r = client.post(url, json=payload, timeout=10)
        r.raise_for_status()
        data = r.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError("한국투자증권 토큰 발급 실패: access_token 없음")
    _cached["token"] = token
    _cached["expires_at"] = now_ms + EXPIRE_MS
    return token
