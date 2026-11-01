"""
국내 종목 마스터 (한투가 매일 배포하는 kospi/kosdaq_code.mst + 업종코드 idxcode.mst).

종목코드 → 종목명·시장·업종 지수코드를 찾는 데 쓴다. 키가 필요 없는 공개 파일이다.
업종 지수코드는 한투 지수 일봉 API에 그대로 넣을 수 있다 (예: 0013 전기·전자).

파일은 고정폭 cp949 텍스트다. 뒤쪽 고정 길이(코스피 228자, 코스닥 222자)가
속성 필드이고 앞쪽이 단축코드(9)·표준코드(12)·한글명이다. 한투 샘플 코드와 같은 분할이다.
"""
from __future__ import annotations

import io
import json
import os
import time
import zipfile
from typing import Any

import httpx

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_PATH = os.path.join(_HERE, ".cache", "krx_master.json")
CACHE_TTL_SEC = 24 * 3600

_BASE = "https://new.real.download.dws.co.kr/common/master"
_MARKETS = {
    "KOSPI": (f"{_BASE}/kospi_code.mst.zip", 228),
    "KOSDAQ": (f"{_BASE}/kosdaq_code.mst.zip", 222),
}
_IDX_URL = f"{_BASE}/idxcode.mst.zip"

# 증권그룹구분코드. ST 주권, FS 외국주권, DR 예탁증서만 개별 종목으로 본다.
STOCK_GROUPS = {"ST", "FS", "DR"}

_memory: dict[str, Any] | None = None


def _download_text(url: str) -> str:
    r = httpx.get(url, timeout=30)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        return z.read(z.namelist()[0]).decode("cp949", errors="replace")


def _parse_market(text: str, tail_len: int, market: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        if len(line) <= tail_len:
            continue
        head, tail = line[:-tail_len], line[-tail_len:]
        code, name = head[0:9].strip(), head[21:].strip()
        if not code or not name:
            continue
        out[code] = {
            "code": code,
            "name": name,
            "market": market,
            "group": tail[1:3],
            # 업종 대·중·소분류 지수코드. 0000이면 해당 분류 없음.
            "sector_codes": [c for c in (tail[4:8], tail[8:12], tail[12:16]) if c.strip("0 ")],
        }
    return out


def _parse_sectors(text: str) -> dict[str, str]:
    """idxcode.mst: 첫 글자 시장구분(0 코스피, 1 코스닥) + 지수코드 4자리 + 이름."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        if len(line) < 6:
            continue
        out[line[1:5]] = line[5:].strip()
    return out


def _fetch() -> dict[str, Any]:
    stocks: dict[str, dict[str, Any]] = {}
    for market, (url, tail_len) in _MARKETS.items():
        stocks.update(_parse_market(_download_text(url), tail_len, market))
    return {
        "fetched_at": time.time(),
        "stocks": stocks,
        "sectors": _parse_sectors(_download_text(_IDX_URL)),
    }


def load(force_refresh: bool = False) -> dict[str, Any]:
    """마스터를 메모리 → 파일 캐시 → 다운로드 순으로 가져온다. 다운로드 실패 시 낡은 캐시라도 쓴다."""
    global _memory
    now = time.time()
    if not force_refresh and _memory and now - _memory["fetched_at"] < CACHE_TTL_SEC:
        return _memory

    cached = None
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, encoding="utf-8") as fp:
                cached = json.load(fp)
        except (OSError, ValueError):
            cached = None
    if not force_refresh and cached and now - cached.get("fetched_at", 0) < CACHE_TTL_SEC:
        _memory = cached
        return cached

    try:
        data = _fetch()
    except Exception:
        if cached:
            _memory = cached
            return cached
        raise
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False)
    _memory = data
    return data


def lookup(ticker_code: str) -> dict[str, Any] | None:
    """종목 정보 + 가장 세분화된 업종 지수코드와 이름."""
    data = load()
    info = data["stocks"].get(ticker_code)
    if not info:
        return None
    sectors = data["sectors"]
    chain = [{"index_code": c, "name": sectors.get(c)} for c in info["sector_codes"]]
    return {
        "code": info["code"],
        "name": info["name"],
        "market": info["market"],
        "sector_chain": chain,
        "sector": chain[-1] if chain else None,
    }
