"""
한국투자증권 해외주식 시세 API 래퍼.

yfinance가 못 주는 것을 담당한다:
  - 실시간 체결가 (응답 stat이 "무료실시간")
  - 프리마켓·애프터마켓 포함 분봉 (ET 04:00~20:00)
  - 호가 10단계와 잔량
  - 상승률·거래량·급등·시가총액 순위
  - 한글 해외 뉴스

키가 없으면 이 모듈은 통째로 비활성화되고 상위에서 yfinance로 폴백한다.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

from config import (
    KIS_APPKEY,
    KIS_APPSECRET,
    KIS_BASE_URL,
    KIS_EXCHANGES,
    KIS_TOKEN_CACHE,
    kis_enabled,
)

# 토큰 유효기간은 24시간이지만 만료 30분 전에 미리 갱신한다.
_REFRESH_MARGIN_SEC = 1800
# 티커가 어느 거래소 소속인지 매번 탐색하지 않도록 프로세스 메모리에 기억한다.
_EXCD_CACHE: dict[str, str] = {}


class KisUnavailable(RuntimeError):
    """한투 키가 없거나 API를 쓸 수 없는 상태."""


def _read_token_cache() -> dict[str, Any]:
    try:
        with open(KIS_TOKEN_CACHE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_token_cache(data: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(KIS_TOKEN_CACHE), exist_ok=True)
    with open(KIS_TOKEN_CACHE, "w", encoding="utf-8") as f:
        json.dump(data, f)


def get_token() -> str:
    """
    접근토큰. 파일 캐시를 먼저 본다.

    한투는 토큰 발급을 분당 1회로 제한해서, 프로세스가 뜰 때마다 새로 받으면
    403이 난다. 캐시를 파일에 두면 재시작과 서버 간 공유가 모두 해결된다.
    """
    if not kis_enabled():
        raise KisUnavailable("한투 API 키(KIS_APPKEY/KIS_APPSECRET)가 설정되지 않았습니다.")

    cached = _read_token_cache()
    now = time.time()
    if cached.get("token") and now < (cached.get("expires_at", 0) - _REFRESH_MARGIN_SEC):
        return cached["token"]

    try:
        with httpx.Client() as client:
            r = client.post(
                f"{KIS_BASE_URL}/oauth2/tokenP",
                json={
                    "grant_type": "client_credentials",
                    "appkey": KIS_APPKEY,
                    "appsecret": KIS_APPSECRET,
                },
                timeout=15,
            )
    except httpx.HTTPError as e:
        if cached.get("token"):
            return cached["token"]
        raise KisUnavailable(f"한투 토큰 발급 실패: {e}") from e

    if r.status_code != 200:
        # 분당 발급 제한(403)에 걸리면 만료가 임박했더라도 기존 토큰을 계속 쓴다.
        if cached.get("token") and now < cached.get("expires_at", 0):
            return cached["token"]
        raise KisUnavailable(
            f"한투 토큰 발급 실패 (HTTP {r.status_code}). "
            "분당 1회 발급 제한일 수 있으니 잠시 후 다시 시도하세요."
        )

    data = r.json()
    token = data.get("access_token")
    if not token:
        raise KisUnavailable("한투 토큰 발급 응답에 access_token이 없습니다.")
    expires_in = int(data.get("expires_in") or 86400)
    _write_token_cache({"token": token, "expires_at": now + expires_in})
    return token


def _get(tr_id: str, path: str, params: dict[str, str]) -> dict[str, Any]:
    token = get_token()
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": KIS_APPKEY,
        "appsecret": KIS_APPSECRET,
        "tr_id": tr_id,
    }
    with httpx.Client() as client:
        r = client.get(f"{KIS_BASE_URL}{path}", params=params, headers=headers, timeout=20)
        r.raise_for_status()
        data = r.json()
    if str(data.get("rt_cd")) != "0":
        raise RuntimeError(f"한투 API 오류 [{tr_id}] {data.get('msg_cd')}: {data.get('msg1')}")
    return data


def _f(value: Any) -> float | None:
    """한투는 모든 수치를 문자열로 준다. 빈 문자열은 None으로."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _i(value: Any) -> int | None:
    f = _f(value)
    return int(f) if f is not None else None


# ---------------------------------------------------------------- 시세

def resolve_exchange(symbol: str) -> str:
    """
    티커가 속한 거래소 코드를 찾는다.

    한투는 EXCD를 명시해야 하는데 티커만 알고 있는 경우가 많다.
    나스닥 → 뉴욕 → 아멕스 순으로 조회해서 체결가가 잡히는 거래소를 채택한다.
    """
    symbol = symbol.upper()
    if symbol in _EXCD_CACHE:
        return _EXCD_CACHE[symbol]

    last_error: Exception | None = None
    for excd in KIS_EXCHANGES:
        try:
            res = _get(
                "HHDFS00000300",
                "/uapi/overseas-price/v1/quotations/price",
                {"AUTH": "", "EXCD": excd, "SYMB": symbol},
            )
        except Exception as e:
            last_error = e
            continue
        out = res.get("output") or {}
        if _f(out.get("last")):
            _EXCD_CACHE[symbol] = excd
            return excd
    if last_error:
        raise last_error
    raise RuntimeError(f"{symbol}: 한투에서 조회되는 거래소를 찾지 못했습니다 (NAS/NYS/AMS).")


def get_quote(symbol: str, excd: str | None = None) -> dict[str, Any]:
    """현재가상세. 실시간 체결가 + 52주 고저 + PER/PBR/EPS 등."""
    excd = excd or resolve_exchange(symbol)
    out = _get(
        "HHDFS76200200",
        "/uapi/overseas-price/v1/quotations/price-detail",
        {"AUTH": "", "EXCD": excd, "SYMB": symbol.upper()},
    ).get("output") or {}

    last = _f(out.get("last"))
    base = _f(out.get("base"))
    change = (last - base) if (last is not None and base) else None
    return {
        "price": last,
        "prev_close": base,
        "change": round(change, 2) if change is not None else None,
        "change_pct": f"{change / base * 100:+.2f}%" if (change is not None and base) else None,
        "open": _f(out.get("open")),
        "day_high": _f(out.get("high")),
        "day_low": _f(out.get("low")),
        "volume": _i(out.get("tvol")),
        "prev_volume": _i(out.get("pvol")),
        "amount": _f(out.get("tamt")),
        "currency": out.get("curr") or "USD",
        "high_52w": _f(out.get("h52p")),
        "high_52w_date": out.get("h52d") or None,
        "low_52w": _f(out.get("l52p")),
        "low_52w_date": out.get("l52d") or None,
        "per": _f(out.get("perx")),
        "pbr": _f(out.get("pbrx")),
        "eps": _f(out.get("epsx")),
        "bps": _f(out.get("bpsx")),
        "shares_outstanding": _i(out.get("shar")),
        "market_cap": _f(out.get("tomv")),
        "industry": out.get("e_icod") or None,
        "tick_size": _f(out.get("e_hogau")),
        "tradable": out.get("e_ordyn") or None,
        "exchange_code": excd,
        "usdkrw": _f(out.get("t_rate")),
    }


def get_daily(symbol: str, excd: str | None = None, gubn: str = "0") -> list[dict[str, Any]]:
    """
    기간별시세. gubn 0:일 1:주 2:월. 한 번에 최근 100건.
    과거→현재 오름차순으로 뒤집어 반환한다.
    """
    excd = excd or resolve_exchange(symbol)
    rows = _get(
        "HHDFS76240000",
        "/uapi/overseas-price/v1/quotations/dailyprice",
        {"AUTH": "", "EXCD": excd, "SYMB": symbol.upper(),
         "GUBN": gubn, "BYMD": "", "MODP": "1"},
    ).get("output2") or []

    bars = [
        {
            "date": r.get("xymd"),
            "open": _f(r.get("open")),
            "high": _f(r.get("high")),
            "low": _f(r.get("low")),
            "close": _f(r.get("clos")),
            "volume": _i(r.get("tvol")) or 0,
        }
        for r in rows
        if isinstance(r, dict) and _f(r.get("clos"))
    ]
    bars.sort(key=lambda b: b["date"] or "")
    return bars


def get_minute_bars(
    symbol: str,
    excd: str | None = None,
    nmin: str = "5",
    count: int = 120,
) -> dict[str, Any]:
    """
    분봉. 한투는 프리마켓·애프터마켓(ET 04:00~20:00)을 모두 포함해서 준다.
    미국 단타에서 갭과 시간외 흐름을 보는 데 필요한 데이터다.
    """
    excd = excd or resolve_exchange(symbol)
    res = _get(
        "HHDFS76950200",
        "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice",
        {"AUTH": "", "EXCD": excd, "SYMB": symbol.upper(), "NMIN": str(nmin),
         "PINC": "1", "NEXT": "", "NREC": str(min(count, 120)), "FILL": "", "KEYB": ""},
    )
    meta = res.get("output1") or {}
    rows = res.get("output2") or []
    bars = [
        {
            "date": r.get("xymd"),
            "time_local": r.get("xhms"),   # 현지(ET) 시각
            "time_kst": r.get("khms"),     # 한국 시각
            "open": _f(r.get("open")),
            "high": _f(r.get("high")),
            "low": _f(r.get("low")),
            "close": _f(r.get("last")),
            "volume": _i(r.get("evol")) or 0,
        }
        for r in rows
        if isinstance(r, dict) and _f(r.get("last"))
    ]
    bars.sort(key=lambda b: (b["date"] or "", b["time_local"] or ""))
    return {
        "interval_min": int(nmin),
        "session_start_local": meta.get("stim"),
        "session_end_local": meta.get("etim"),
        "bars": bars,
        "note": "현지시각 기준 프리마켓·정규장·애프터마켓이 모두 포함됩니다.",
    }


def get_orderbook(symbol: str, excd: str | None = None, depth: int = 5) -> dict[str, Any]:
    """호가와 잔량. yfinance로는 아예 얻을 수 없는 데이터."""
    excd = excd or resolve_exchange(symbol)
    res = _get(
        "HHDFS76200100",
        "/uapi/overseas-price/v1/quotations/inquire-asking-price",
        {"AUTH": "", "EXCD": excd, "SYMB": symbol.upper()},
    )
    head = res.get("output1") or {}
    book = res.get("output2") or {}

    levels = []
    for i in range(1, min(depth, 10) + 1):
        bid, ask = _f(book.get(f"pbid{i}")), _f(book.get(f"pask{i}"))
        if bid is None and ask is None:
            continue
        levels.append({
            "level": i,
            "bid": bid, "bid_size": _i(book.get(f"vbid{i}")),
            "ask": ask, "ask_size": _i(book.get(f"vask{i}")),
        })

    best_bid = levels[0]["bid"] if levels else None
    best_ask = levels[0]["ask"] if levels else None
    spread = (best_ask - best_bid) if (best_bid and best_ask) else None
    return {
        "quote_time_local": head.get("dhms"),
        "quote_date": head.get("dymd"),
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": round(spread, 4) if spread is not None else None,
        "spread_pct": round(spread / best_ask * 100, 3) if (spread and best_ask) else None,
        "total_bid_size": _i(head.get("bvol")),
        "total_ask_size": _i(head.get("avol")),
        "levels": levels,
    }


# ---------------------------------------------------------------- 순위

_RANK_SPECS = {
    "surge": ("HHDFS76290000", "/uapi/overseas-stock/v1/ranking/updown-rate", "상승률"),
    "plunge": ("HHDFS76290000", "/uapi/overseas-stock/v1/ranking/updown-rate", "하락률"),
    "volume": ("HHDFS76310010", "/uapi/overseas-stock/v1/ranking/trade-vol", "거래량"),
    "fluct_up": ("HHDFS76260000", "/uapi/overseas-stock/v1/ranking/price-fluct", "가격급등"),
    "fluct_down": ("HHDFS76260000", "/uapi/overseas-stock/v1/ranking/price-fluct", "가격급락"),
    "market_cap": ("HHDFS76350100", "/uapi/overseas-stock/v1/ranking/market-cap", "시가총액"),
}


def get_ranking(
    category: str,
    excd: str = "NAS",
    limit: int = 30,
    vol_rang: str = "0",
    nday: str = "0",
) -> dict[str, Any]:
    """
    순위 조회. category: surge / plunge / volume / fluct_up / fluct_down / market_cap

    vol_rang 0:전체 1:1000주이상 2:10만주이상 3:100만주이상 4:1000만주이상
    nday     0:당일 1:2일 2:3일 3:5일 4:10일 5:20일 6:30일 7:60일 8:120일 9:1년
    """
    if category not in _RANK_SPECS:
        raise ValueError(f"지원하지 않는 category: {category} (가능: {', '.join(_RANK_SPECS)})")
    tr_id, path, label = _RANK_SPECS[category]

    params: dict[str, str] = {"AUTH": "", "EXCD": excd, "VOL_RANG": vol_rang, "KEYB": ""}
    if category in ("surge", "plunge"):
        params.update({"NDAY": nday, "GUBN": "1" if category == "surge" else "0"})
    elif category == "volume":
        params.update({"NDAY": nday, "PRC1": "", "PRC2": ""})
    elif category in ("fluct_up", "fluct_down"):
        params.update({"GUBN": "1" if category == "fluct_up" else "0", "MINX": "0"})
    elif category == "market_cap":
        params.update({"CURR_GB": "0"})

    res = _get(tr_id, path, params)
    meta = res.get("output1") or {}
    rows = res.get("output2") or []

    items = []
    for r in rows[:limit]:
        if not isinstance(r, dict):
            continue
        items.append({
            "rank": _i(r.get("rank")),
            "ticker": r.get("symb"),
            "name_en": r.get("ename") or r.get("enam"),
            "name_ko": r.get("name") or r.get("knam"),
            "price": _f(r.get("last")),
            "change_pct": _f(r.get("rate")),
            "volume": _i(r.get("tvol")),
            "amount": _f(r.get("tamt")),
            "market_cap": _f(r.get("tomv")),
            "exchange": r.get("excd"),
        })
    return {
        "category": category,
        "category_label": label,
        "exchange": excd,
        "data_status": meta.get("stat"),
        "total_matched": _i(meta.get("trec")),
        "returned": len(items),
        "items": items,
    }


# ---------------------------------------------------------------- 뉴스

def get_news(symbol: str = "", limit: int = 20) -> list[dict[str, Any]]:
    """
    해외 시장 관련 한글 뉴스 제목.
    yfinance가 주는 영문 기사와 성격이 달라서 같이 보면 재료 파악이 빨라진다.
    """
    res = _get(
        "FHKST01011801",
        "/uapi/overseas-price/v1/quotations/brknews-title",
        {"FID_NEWS_OFER_ENTP_CODE": "", "FID_COND_SCR_DIV_CODE": "11801",
         "FID_COND_MRKT_CLS_CODE": "", "FID_INPUT_ISCD": symbol.upper() if symbol else "",
         "FID_TITL_CNTT": "", "FID_INPUT_DATE_1": "", "FID_INPUT_HOUR_1": "",
         "FID_RANK_SORT_CLS_CODE": "", "FID_INPUT_SRNO": ""},
    )
    rows = res.get("output") or []
    out = []
    for r in rows[:limit]:
        if not isinstance(r, dict):
            continue
        date, tm = r.get("data_dt") or "", r.get("data_tm") or ""
        tickers = [r.get(f"iscd{i}") for i in range(1, 11)]
        out.append({
            "title": r.get("hts_pbnt_titl_cntt"),
            "source": r.get("dorg"),
            "published_kst": f"{date[:4]}-{date[4:6]}-{date[6:8]} {tm[:2]}:{tm[2:4]}" if len(date) == 8 else None,
            "related_tickers": [t for t in tickers if t and t.strip()],
        })
    return out
