"""
원시 기술 지표 수치만 계산. 해석·판단은 하지 않음.
"""
import math
from typing import Any


def compute_ma(prices: list[float], period: int) -> float | None:
    """이동평균. 데이터 부족 시 None."""
    if not prices or len(prices) < period:
        return None
    return sum(prices[-period:]) / period


def analyze_technicals(
    current_price: float,
    high: float,
    low: float,
    prev_close: float,
    volume: int,
    prev_volume: int,
    daily_prices: list[float] | None = None,
) -> dict[str, Any]:
    """
    현재가·고저·거래량·일봉 종가로 계산 가능한 수치만 반환.
    """
    out: dict[str, Any] = {}
    if daily_prices:
        ma5 = compute_ma(daily_prices, 5)
        ma20 = compute_ma(daily_prices, 20)
        ma60 = compute_ma(daily_prices, 60)
        out["ma5"] = round(ma5, 0) if ma5 is not None else None
        out["ma20"] = round(ma20, 0) if ma20 is not None else None
        out["ma60"] = round(ma60, 0) if ma60 is not None else None
    else:
        out["ma5"] = out["ma20"] = out["ma60"] = None

    if prev_volume and prev_volume > 0:
        out["volume_ratio"] = round(volume / prev_volume, 2)
    else:
        out["volume_ratio"] = None

    if high and high > 0:
        out["pullback_pct_vs_high"] = round((current_price - high) / high * 100, 2)
    else:
        out["pullback_pct_vs_high"] = None

    if daily_prices and len(daily_prices) >= 20:
        recent = daily_prices[-20:]
        mid = sum(recent) / 20
        var = sum((x - mid) ** 2 for x in recent) / 20
        std = math.sqrt(var) if var else 0
        out["bollinger_upper"] = round(mid + 2 * std, 0)
        out["bollinger_mid"] = round(mid, 0)
        out["bollinger_lower"] = round(mid - 2 * std, 0)
    else:
        out["bollinger_upper"] = out["bollinger_mid"] = out["bollinger_lower"] = None

    return out
