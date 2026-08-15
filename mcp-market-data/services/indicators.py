"""
원시 기술 지표 수치만 계산. 해석·판단은 하지 않음.

입력 리스트는 과거→현재 오름차순. 가격은 원 단위라 정수로 반올림한다.
"""
import math
from typing import Any

RETURN_WINDOWS = {"5d": 5, "20d": 20, "60d": 60, "120d": 120}


def _r0(v: float | None) -> float | None:
    return round(v, 0) if v is not None else None


def _r2(v: float | None) -> float | None:
    return round(v, 2) if v is not None else None


def compute_ma(prices: list[float], period: int) -> float | None:
    """이동평균. 데이터 부족 시 None."""
    if not prices or len(prices) < period:
        return None
    return sum(prices[-period:]) / period


def compute_return_pct(prices: list[float], period: int) -> float | None:
    """N거래일 전 대비 수익률(%)."""
    if not prices or len(prices) < period + 1:
        return None
    base = prices[-(period + 1)]
    if not base:
        return None
    return (prices[-1] - base) / base * 100


def compute_atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float | None:
    """평균 실제 변동폭(ATR). 손절 폭 산정 근거."""
    n = min(len(highs), len(lows), len(closes))
    if n < period + 1:
        return None
    trs = []
    for k in range(n - period, n):
        prev_close = closes[k - 1]
        trs.append(max(highs[k] - lows[k], abs(highs[k] - prev_close), abs(lows[k] - prev_close)))
    return sum(trs) / len(trs) if trs else None


def compute_bollinger(prices: list[float], period: int = 20, mult: float = 2.0) -> dict[str, float | None]:
    if not prices or len(prices) < period:
        return {"bollinger_upper": None, "bollinger_mid": None, "bollinger_lower": None}
    recent = prices[-period:]
    mid = sum(recent) / period
    var = sum((x - mid) ** 2 for x in recent) / period
    std = math.sqrt(var) if var > 0 else 0.0
    return {
        "bollinger_upper": _r0(mid + mult * std),
        "bollinger_mid": _r0(mid),
        "bollinger_lower": _r0(mid - mult * std),
    }


def compute_relative_strength(
    closes: list[float],
    index_closes: dict[str, list[float]],
) -> dict[str, Any]:
    """
    지수(코스피/코스닥) 대비 초과수익률.

    수급 데이터가 있어도 '시장 전체가 오른 건지 이 종목이 강한 건지'는 구분되지 않는다.
    지수 대비 초과수익률이 양수면 시장보다 강했다는 뜻이다.
    """
    out: dict[str, Any] = {
        "stock_return_pct": {k: _r2(compute_return_pct(closes, n)) for k, n in RETURN_WINDOWS.items()},
        "vs_index": {},
    }
    for name, idx in index_closes.items():
        if not idx:
            continue
        excess = {}
        for label, n in RETURN_WINDOWS.items():
            s, b = compute_return_pct(closes, n), compute_return_pct(idx, n)
            excess[label] = _r2(s - b) if (s is not None and b is not None) else None
        out["vs_index"][name] = {
            "index_return_pct": {k: _r2(compute_return_pct(idx, n)) for k, n in RETURN_WINDOWS.items()},
            "excess_return_pct": excess,
        }
    return out


def analyze_technicals(
    current_price: float,
    high: float,
    low: float,
    prev_close: float,
    volume: int,
    prev_volume: int,
    daily_prices: list[float] | None = None,
    daily_highs: list[float] | None = None,
    daily_lows: list[float] | None = None,
    daily_volumes: list[int] | None = None,
    index_closes: dict[str, list[float]] | None = None,
) -> dict[str, Any]:
    """
    현재가·고저·거래량·일봉 시계열로 계산 가능한 수치만 반환.

    daily_highs/lows/volumes와 index_closes는 선택 인자다.
    넘기면 ATR·52주 고저·거래량 20일 평균·상대강도까지 계산한다.
    """
    out: dict[str, Any] = {}
    closes = daily_prices or []

    for period in (5, 20, 60, 120, 200):
        out[f"ma{period}"] = _r0(compute_ma(closes, period))

    out.update(compute_bollinger(closes))

    # 전일 대비 거래량 비율 (기존 동작 유지)
    out["volume_ratio"] = round(volume / prev_volume, 2) if prev_volume and prev_volume > 0 else None

    # 20일 평균 대비가 급증 판단에 더 안정적이다
    vols = daily_volumes or []
    if len(vols) >= 21:
        avg20 = sum(vols[-21:-1]) / 20
        out["volume_avg20"] = int(avg20)
        out["volume_ratio_vs_avg20"] = round(vols[-1] / avg20, 2) if avg20 > 0 else None
    else:
        out["volume_avg20"] = None
        out["volume_ratio_vs_avg20"] = None

    out["pullback_pct_vs_high"] = _r2((current_price - high) / high * 100) if high and high > 0 else None

    atr = compute_atr(daily_highs or [], daily_lows or [], closes, 14)
    out["atr14"] = _r0(atr)
    out["atr14_pct_of_price"] = _r2(atr / current_price * 100) if (atr and current_price) else None

    window = closes[-250:] if len(closes) >= 250 else closes
    if window and current_price:
        hi, lo = max(window), min(window)
        out["high_52w"] = _r0(hi)
        out["low_52w"] = _r0(lo)
        out["pct_from_52w_high"] = _r2((current_price - hi) / hi * 100) if hi else None
        out["pct_from_52w_low"] = _r2((current_price - lo) / lo * 100) if lo else None
    else:
        out["high_52w"] = out["low_52w"] = None
        out["pct_from_52w_high"] = out["pct_from_52w_low"] = None

    out["relative_strength"] = compute_relative_strength(closes, index_closes or {})
    out["data_points"] = len(closes)
    return out
