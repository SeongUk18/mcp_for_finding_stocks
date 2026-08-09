"""
yfinance 래퍼.

yfinance는 비공식 라이브러리라 필드가 버전·종목마다 빠질 수 있다.
따라서 모든 조회는 개별적으로 감싸고, 실패하면 None과 사유를 남겨
"데이터 없음"과 "데이터 0"이 구분되게 한다.
"""
from __future__ import annotations

import time
from typing import Any

import pandas as pd
import yfinance as yf

from config import BENCHMARKS

# 벤치마크는 여러 종목 분석에서 반복 호출되므로 프로세스 메모리에 잠깐 캐시한다.
_BENCH_CACHE: dict[str, tuple[float, list[float]]] = {}
_BENCH_TTL_SEC = 600


def _clean(value: Any) -> Any:
    """pandas/numpy 스칼라를 JSON 직렬화 가능한 값으로 변환."""
    if value is None:
        return None
    if isinstance(value, (pd.Timestamp,)):
        return value.strftime("%Y-%m-%d")
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except (AttributeError, ValueError):
            pass
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


def _df_records(df: Any, limit: int | None = None) -> list[dict[str, Any]]:
    """DataFrame을 딕셔너리 리스트로. 비어 있으면 빈 리스트."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return []
    frame = df.head(limit) if limit else df
    records: list[dict[str, Any]] = []
    for idx, row in frame.iterrows():
        rec: dict[str, Any] = {}
        if not isinstance(idx, int):
            rec["index"] = _clean(idx)
        for col in frame.columns:
            rec[str(col)] = _clean(row[col])
        records.append(rec)
    return records


def _safe(fn, default=None):
    """yfinance 호출 하나가 실패해도 전체 응답이 죽지 않게 감싼다."""
    try:
        return fn(), None
    except Exception as e:  # yfinance는 예외 타입을 보장하지 않는다
        return default, f"{type(e).__name__}: {e}"


def get_history(symbol: str, period: str = "1y", interval: str = "1d") -> dict[str, Any]:
    """
    일봉/분봉 시계열. 과거→현재 오름차순 리스트로 반환.
    auto_adjust=False로 실제 체결가 기준 값을 쓴다.
    """
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=period, interval=interval, auto_adjust=False)
    if df is None or df.empty:
        raise RuntimeError(f"{symbol}: yfinance가 시세 데이터를 반환하지 않음 (티커 확인 필요)")
    df = df.dropna(subset=["Close"])
    fmt = "%Y-%m-%d" if interval.endswith("d") or interval.endswith("wk") or interval.endswith("mo") else "%Y-%m-%d %H:%M"
    return {
        "dates": [d.strftime(fmt) for d in df.index],
        "open": [float(v) for v in df["Open"]],
        "high": [float(v) for v in df["High"]],
        "low": [float(v) for v in df["Low"]],
        "close": [float(v) for v in df["Close"]],
        "volume": [int(v) if pd.notna(v) else 0 for v in df["Volume"]],
    }


def get_benchmark_closes(names: list[str] | None = None, period: str = "1y") -> dict[str, list[float]]:
    """벤치마크 종가. 10분 캐시. 실패한 벤치마크는 결과에서 빠진다."""
    out: dict[str, list[float]] = {}
    now = time.time()
    for name in (names or BENCHMARKS):
        cached = _BENCH_CACHE.get(name)
        if cached and (now - cached[0]) < _BENCH_TTL_SEC:
            out[name] = cached[1]
            continue
        try:
            closes = get_history(name, period=period)["close"]
        except Exception:
            continue
        _BENCH_CACHE[name] = (now, closes)
        out[name] = closes
    return out


def get_quote(symbol: str) -> dict[str, Any]:
    """현재가·전일종가·당일 고저·거래량. fast_info 우선, 실패 시 info로 보완."""
    ticker = yf.Ticker(symbol)
    quote: dict[str, Any] = {}
    fast, fast_err = _safe(lambda: dict(ticker.fast_info), {})
    fast = fast or {}

    def pick(*keys: str) -> Any:
        for k in keys:
            v = fast.get(k)
            if v is not None:
                return _clean(v)
        return None

    quote["price"] = pick("last_price", "lastPrice")
    quote["prev_close"] = pick("previous_close", "previousClose")
    quote["open"] = pick("open")
    quote["day_high"] = pick("day_high", "dayHigh")
    quote["day_low"] = pick("day_low", "dayLow")
    quote["volume"] = pick("last_volume", "lastVolume")
    quote["market_cap"] = pick("market_cap", "marketCap")
    quote["currency"] = pick("currency") or "USD"

    if quote["price"] and quote["prev_close"]:
        change = quote["price"] - quote["prev_close"]
        quote["change"] = round(change, 2)
        quote["change_pct"] = f"{change / quote['prev_close'] * 100:+.2f}%"
    else:
        quote["change"] = None
        quote["change_pct"] = None

    if fast_err:
        quote["_warning"] = fast_err
    return quote


def get_info(symbol: str) -> tuple[dict[str, Any], str | None]:
    """.info 전체. 무겁고 실패도 잦아 별도로 감싼다."""
    return _safe(lambda: dict(yf.Ticker(symbol).info), {})


def get_ownership(symbol: str) -> dict[str, Any]:
    """
    기관 보유 현황 (13F 기반).

    미국은 한국처럼 일별 외국인·기관 순매수 공시가 없다.
    대신 분기마다 제출되는 13F로 '큰손이 담고 있는지'를 확인한다.
    분기 단위라 최대 45일 지연될 수 있다는 점을 반드시 감안해야 한다.
    """
    ticker = yf.Ticker(symbol)
    major, major_err = _safe(lambda: ticker.get_major_holders())
    inst, inst_err = _safe(lambda: ticker.get_institutional_holders())
    funds, funds_err = _safe(lambda: ticker.get_mutualfund_holders())

    summary: dict[str, Any] = {}
    if isinstance(major, pd.DataFrame) and not major.empty:
        # 최신 yfinance는 index=지표명, 단일 'Value' 컬럼 형태.
        col = major.columns[0]
        for idx, row in major.iterrows():
            summary[str(idx)] = _clean(row[col])

    return {
        "summary": summary or None,
        "institutional_holders": _df_records(inst, limit=15),
        "mutualfund_holders": _df_records(funds, limit=10),
        "note": "13F 기반 분기 데이터. 보고 기준일(Date Reported) 확인 필수. 최대 45일 지연.",
        "errors": {k: v for k, v in
                   {"major_holders": major_err, "institutional_holders": inst_err,
                    "mutualfund_holders": funds_err}.items() if v} or None,
    }


def get_short_interest(symbol: str, info: dict[str, Any] | None = None) -> dict[str, Any]:
    """공매도 잔고·Days to Cover. 거래소 보고라 월 2회 갱신이며 지연이 크다."""
    if info is None:
        info, _ = get_info(symbol)
    info = info or {}
    prior = info.get("sharesShortPriorMonth")
    current = info.get("sharesShort")
    change_pct = None
    if current and prior:
        change_pct = round((current - prior) / prior * 100, 2)
    return {
        "shares_short": _clean(current),
        "shares_short_prior_month": _clean(prior),
        "shares_short_change_pct": change_pct,
        "short_percent_of_float": _clean(info.get("shortPercentOfFloat")),
        "days_to_cover": _clean(info.get("shortRatio")),
        "float_shares": _clean(info.get("floatShares")),
        "shares_outstanding": _clean(info.get("sharesOutstanding")),
        "date_short_interest": _epoch_to_date(info.get("dateShortInterest")),
        "note": "거래소 공매도 잔고는 월 2회 보고. 실시간이 아니며 1~2주 지연될 수 있음.",
    }


def get_analyst_view(symbol: str, info: dict[str, Any] | None = None) -> dict[str, Any]:
    """애널리스트 등급 분포·최근 등급 변경·목표주가."""
    ticker = yf.Ticker(symbol)
    recs, recs_err = _safe(lambda: ticker.get_recommendations())
    grades, grades_err = _safe(lambda: ticker.get_upgrades_downgrades())
    targets, targets_err = _safe(lambda: ticker.get_analyst_price_targets())

    if not isinstance(targets, dict) or not targets:
        if info is None:
            info, _ = get_info(symbol)
        info = info or {}
        targets = {
            "current": _clean(info.get("currentPrice")),
            "low": _clean(info.get("targetLowPrice")),
            "high": _clean(info.get("targetHighPrice")),
            "mean": _clean(info.get("targetMeanPrice")),
            "median": _clean(info.get("targetMedianPrice")),
        }
    else:
        targets = {k: _clean(v) for k, v in targets.items()}

    # upgrades_downgrades는 최신순 정렬이 보장되지 않으므로 날짜로 다시 정렬한다.
    if isinstance(grades, pd.DataFrame) and not grades.empty:
        try:
            grades = grades.sort_index(ascending=False)
        except TypeError:
            pass

    return {
        "recommendation_trend": _df_records(recs, limit=4),
        "recent_grade_changes": _df_records(grades, limit=10),
        "price_targets": targets,
        "errors": {k: v for k, v in
                   {"recommendations": recs_err, "upgrades_downgrades": grades_err,
                    "price_targets": targets_err}.items() if v} or None,
    }


def get_news(symbol: str, limit: int = 15) -> list[dict[str, Any]]:
    """
    최신 뉴스 헤드라인.

    Ticker.get_news()는 종목과 무관한 일반 시장 피드를 돌려주는 경우가 있어
    종목 연관성이 확실한 Search API를 우선 사용한다.
    Search 결과에는 relatedTickers가 있어 해당 종목이 기사 주제인지 판별할 수 있다.
    호재/악재 판단은 하지 않고 원문 메타데이터만 넘긴다.
    """
    symbol_upper = symbol.upper()
    raw, err = _safe(lambda: yf.Search(symbol, news_count=limit).news, [])

    out: list[dict[str, Any]] = []
    for item in (raw or [])[:limit]:
        if not isinstance(item, dict):
            continue
        related = item.get("relatedTickers") or []
        out.append(
            {
                "title": item.get("title"),
                "publisher": item.get("publisher"),
                "published_utc": _epoch_to_utc(item.get("providerPublishTime")),
                "url": item.get("link"),
                "related_tickers": related,
                # 기사가 이 종목을 직접 다루는지, 곁다리로 언급만 하는지 구분한다.
                "is_primary_subject": bool(related) and related[0].upper() == symbol_upper,
            }
        )
    if out:
        return out

    # Search가 비면 Ticker.get_news로 폴백 (신·구 응답 구조 모두 처리)
    ticker = yf.Ticker(symbol)
    fallback, fb_err = _safe(lambda: ticker.get_news(count=limit), [])
    if not fallback:
        detail = err or fb_err
        return [{"error": detail}] if detail else []

    for item in (fallback or [])[:limit]:
        if not isinstance(item, dict):
            continue
        content = item.get("content") if isinstance(item.get("content"), dict) else item
        provider = content.get("provider")
        if isinstance(provider, dict):
            provider = provider.get("displayName")
        url = content.get("canonicalUrl") or content.get("clickThroughUrl") or content.get("link")
        if isinstance(url, dict):
            url = url.get("url")
        published = content.get("pubDate") or content.get("displayTime") or content.get("providerPublishTime")
        if isinstance(published, (int, float)):
            published = pd.Timestamp(published, unit="s", tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
        out.append(
            {
                "title": content.get("title"),
                "summary": content.get("summary") or content.get("description"),
                "publisher": provider or content.get("publisher"),
                "published_utc": published,
                "url": url,
                "related_tickers": item.get("relatedTickers"),
            }
        )
    return out


def get_profile(symbol: str, info: dict[str, Any] | None = None) -> dict[str, Any]:
    """종목 기본 정보. 티커가 실제로 존재하는지 확인하는 용도도 겸한다."""
    if info is None:
        info, _ = get_info(symbol)
    info = info or {}
    return {
        "symbol": symbol,
        "name": info.get("longName") or info.get("shortName"),
        "exchange": info.get("fullExchangeName") or info.get("exchange"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "market_cap": _clean(info.get("marketCap")),
        "avg_volume_10d": _clean(info.get("averageDailyVolume10Day")),
        "beta": _clean(info.get("beta")),
        "trailing_pe": _clean(info.get("trailingPE")),
        "forward_pe": _clean(info.get("forwardPE")),
        "earnings_date": _epoch_to_date(info.get("earningsTimestamp")),
    }


def _epoch_to_date(value: Any) -> str | None:
    """에포크 초를 날짜 문자열로. 실적 발표일은 진입 타이밍에 직결되므로 읽기 쉽게 변환."""
    return _epoch_fmt(value, "%Y-%m-%d")


def _epoch_to_utc(value: Any) -> str | None:
    """에포크 초를 UTC 타임스탬프 문자열로."""
    return _epoch_fmt(value, "%Y-%m-%dT%H:%M:%SZ")


def _epoch_fmt(value: Any, fmt: str) -> str | None:
    if isinstance(value, str):
        return value
    if not isinstance(value, (int, float)) or value <= 0:
        return _clean(value)
    try:
        return pd.Timestamp(int(value), unit="s", tz="UTC").strftime(fmt)
    except (ValueError, OverflowError, OSError):
        return None
