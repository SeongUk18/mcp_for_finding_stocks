"""
종목·지수 일봉 기간 조회 + 로컬 캐시.

요청·파싱은 backtest_kr_rule이 쓰는 kis_api 일봉 함수와 같다(수정주가).
캐시는 .cache/daily/에 코드별 JSON으로 둔다. 수정주가라서 분할·병합이 생기면 과거 값이
통째로 바뀌므로, 날이 바뀐 뒤 처음 쓸 때는 최근 구간을 다시 받아 캐시와 대조하고
하나라도 다르면 캐시를 버리고 새로 받는다. 같은 날 같은 조회는 API를 부르지 않는다.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Any, Callable

from services import kis_api as kis

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(_HERE, ".cache", "daily")

# 시간외 단일가(16:00~18:00)까지 끝나야 당일 일봉이 확정된다
DAY_FINAL_HOUR = 18
# 장중에 받은 당일 봉을 다시 쓰는 시간
PARTIAL_TTL = timedelta(minutes=10)
# 분할·병합 감지용으로 다시 받아 대조하는 구간
REVALIDATE_DAYS = 14
# 상장일·휴장 때문에 요청 시작일과 첫 봉 사이가 이보다 벌어지면 이력 부족으로 본다
INSUFFICIENT_SLACK_DAYS = 7

Fetcher = Callable[[str, str, str, bool], list[dict[str, Any]]]

INDEX_ALIASES = {"KOSPI": kis.INDEX_KOSPI, "KOSDAQ": kis.INDEX_KOSDAQ}


def resolve_code(code: str) -> tuple[str, bool]:
    """'KOSPI'·'0001' 같은 지수 코드면 (업종코드, True), 종목이면 (코드, False)."""
    code = code.strip().upper()
    if code in INDEX_ALIASES:
        return INDEX_ALIASES[code], True
    return code, len(code) == 4 and code.isdigit()


def _path(code: str, index: bool) -> str:
    return os.path.join(CACHE_DIR, f"{'index' if index else 'stock'}_{code}.json")


def _load(code: str, index: bool) -> dict[str, Any] | None:
    try:
        with open(_path(code, index), encoding="utf-8") as fp:
            return json.load(fp)
    except (OSError, ValueError):
        return None


def _save(code: str, index: bool, cache: dict[str, Any]) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_path(code, index), "w", encoding="utf-8") as fp:
        json.dump(cache, fp, ensure_ascii=False)


def _ymd(d: datetime) -> str:
    return d.strftime("%Y%m%d")


def _shift(ymd: str, days: int) -> str:
    return _ymd(datetime.strptime(ymd, "%Y%m%d") + timedelta(days=days))


def _today_fresh(cache: dict[str, Any], now: datetime) -> bool:
    """캐시에 든 당일 봉을 그대로 써도 되는지."""
    fetched = datetime.fromisoformat(cache["fetched_at"])
    if fetched.date() != now.date():
        return False
    return fetched.hour >= DAY_FINAL_HOUR or now - fetched < PARTIAL_TTL


def get_history(
    code: str,
    start: str,
    end: str,
    fetch: Fetcher | None = None,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    start~end(YYYYMMDD) 일봉과 메타. fetch(code, start, end, index)는 테스트에서 바꿔 끼운다.
    """
    fetch = fetch or (lambda c, s, e, idx: kis.get_daily_bars_range(c, s, e, index=idx))
    now = now or datetime.now()
    today = _ymd(now)
    code, index = resolve_code(code)
    end = min(end, today)
    calls = 0

    cache = _load(code, index)
    fresh = bool(cache) and datetime.fromisoformat(cache["fetched_at"]).date() == now.date()
    covered = bool(cache) and cache["covered_from"] <= start and cache["covered_to"] >= end

    if not (fresh and covered and (end < today or _today_fresh(cache, now))):
        bars: dict[str, dict[str, Any]] = {b["date"]: b for b in cache["bars"]} if cache else {}
        if cache:
            # 마지막 캐시 구간부터 오늘까지 다시 받아 겹치는 날을 대조한다
            tail_start = max(cache["covered_from"], _shift(cache["covered_to"], -REVALIDATE_DAYS))
            tail = fetch(code, tail_start, today, index)
            calls += 1
            if any(b["date"] in bars and bars[b["date"]]["close"] != b["close"] for b in tail):
                bars, cache = {}, None
            else:
                bars.update({b["date"]: b for b in tail})
        if cache is None:
            bars = {b["date"]: b for b in fetch(code, start, today, index)}
            calls += 1
            covered_from = start
        elif start < cache["covered_from"]:
            bars.update({b["date"]: b for b in fetch(code, start, _shift(cache["covered_from"], -1), index)})
            calls += 1
            covered_from = start
        else:
            covered_from = cache["covered_from"]
        cache = {
            "fetched_at": now.isoformat(timespec="seconds"),
            "covered_from": covered_from,
            "covered_to": today,
            "bars": [bars[k] for k in sorted(bars)],
        }
        _save(code, index, cache)

    rows = [b for b in cache["bars"] if start <= b["date"] <= end]
    first = rows[0]["date"] if rows else None
    meta = {
        "code": code,
        "is_index": index,
        "start": start,
        "end": end,
        "rows": len(rows),
        "first_date": first,
        "last_date": rows[-1]["date"] if rows else None,
        "insufficient_history": first is None or first > _shift(start, INSUFFICIENT_SLACK_DAYS),
        "from_cache": calls == 0,
        "fetch_calls": calls,
    }
    return rows, meta
