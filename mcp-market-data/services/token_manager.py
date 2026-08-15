"""
한국투자증권 OAuth2 토큰 관리.

한투는 접근토큰 발급을 분당 1회로 제한한다. 메모리에만 캐시하면
프로세스가 재시작될 때마다, 그리고 같은 키를 쓰는 서버가 둘 이상이면
403이 발생한다. 그래서 파일에도 캐시해서 프로세스·서버 간에 공유한다.
"""
import json
import os
import time
from typing import Any

import httpx

from config import KIS_BASE_URL, KIS_APPKEY, KIS_APPSECRET, KIS_TOKEN_CACHE

_cached: dict[str, Any] = {"token": None, "expires_at": 0}
# 유효기간 24시간. 만료 30분 전에 미리 갱신한다.
REFRESH_MARGIN_SEC = 1800


def _read_file_cache() -> dict[str, Any]:
    try:
        with open(KIS_TOKEN_CACHE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_file_cache(token: str, expires_at: float) -> None:
    try:
        os.makedirs(os.path.dirname(KIS_TOKEN_CACHE), exist_ok=True)
        with open(KIS_TOKEN_CACHE, "w", encoding="utf-8") as f:
            json.dump({"token": token, "expires_at": expires_at}, f)
    except OSError:
        # 파일 캐시는 최적화일 뿐이므로 실패해도 메모리 캐시로 계속 동작한다.
        pass


def _valid(entry: dict[str, Any], now: float, margin: float = REFRESH_MARGIN_SEC) -> bool:
    return bool(entry.get("token")) and now < (entry.get("expires_at", 0) - margin)


def get_access_token() -> str:
    global _cached
    now = time.time()

    if _valid(_cached, now):
        return _cached["token"]

    file_cache = _read_file_cache()
    if _valid(file_cache, now):
        _cached = dict(file_cache)
        return _cached["token"]

    try:
        with httpx.Client() as client:
            r = client.post(
                f"{KIS_BASE_URL}/oauth2/tokenP",
                json={
                    "grant_type": "client_credentials",
                    "appkey": KIS_APPKEY,
                    "appsecret": KIS_APPSECRET,
                },
                timeout=10,
            )
    except httpx.HTTPError as e:
        # 네트워크가 잠깐 끊긴 경우, 만료 전이면 기존 토큰으로 버틴다.
        for entry in (_cached, file_cache):
            if _valid(entry, now, margin=0):
                return entry["token"]
        raise RuntimeError(f"한국투자증권 토큰 발급 실패: {e}") from e

    if r.status_code != 200:
        # 분당 발급 제한(403)에 걸렸으면 만료 직전이라도 기존 토큰을 그대로 쓴다.
        for entry in (_cached, file_cache):
            if _valid(entry, now, margin=0):
                return entry["token"]
        raise RuntimeError(
            f"한국투자증권 토큰 발급 실패 (HTTP {r.status_code}). "
            "토큰 발급은 분당 1회로 제한되니 잠시 후 다시 시도하세요."
        )

    data = r.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError("한국투자증권 토큰 발급 실패: access_token 없음")

    expires_at = now + int(data.get("expires_in") or 86400)
    _cached = {"token": token, "expires_at": expires_at}
    _write_file_cache(token, expires_at)
    return token
