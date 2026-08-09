"""
미국주식 원시 기술 지표 계산. 해석·판단은 하지 않음.

입력 리스트는 모두 과거→현재 오름차순이어야 한다.
가격은 달러라 소수 2자리로 반올림한다.
"""
from __future__ import annotations

import math
from typing import Any

RETURN_WINDOWS = {"5d": 5, "20d": 20, "60d": 60, "120d": 120}


def _r2(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None


def compute_ma(prices: list[float], period: int) -> float | None:
    """이동평균. 데이터 부족 시 None."""
    if not prices or len(prices) < period:
        return None
    return sum(prices[-period:]) / period


def compute_return_pct(prices: list[float], period: int) -> float | None:
    """N거래일 전 대비 수익률(%). 기준가가 0이면 None."""
    if not prices or len(prices) < period + 1:
        return None
    base = prices[-(period + 1)]
    if not base:
        return None
    return (prices[-1] - base) / base * 100


def compute_atr(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> float | None:
    """평균 실제 변동폭(ATR). 손절 폭 산정 근거로 쓴다."""
    n = min(len(highs), len(lows), len(closes))
    if n < period + 1:
        return None
    trs: list[float] = []
    for i in range(n - period, n):
        prev_close = closes[i - 1]
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - prev_close),
            abs(lows[i] - prev_close),
        )
        trs.append(tr)
    return sum(trs) / len(trs) if trs else None


def compute_bollinger(prices: list[float], period: int = 20, mult: float = 2.0) -> dict[str, float | None]:
    """볼린저 밴드. 데이터 부족 시 전부 None."""
    if not prices or len(prices) < period:
        return {"bollinger_upper": None, "bollinger_mid": None, "bollinger_lower": None}
    recent = prices[-period:]
    mid = sum(recent) / period
    var = sum((x - mid) ** 2 for x in recent) / period
    std = math.sqrt(var) if var > 0 else 0.0
    return {
        "bollinger_upper": _r2(mid + mult * std),
        "bollinger_mid": _r2(mid),
        "bollinger_lower": _r2(mid - mult * std),
    }


def compute_relative_strength(
    closes: list[float],
    benchmark_closes: dict[str, list[float]],
) -> dict[str, Any]:
    """
    벤치마크(SPY/QQQ) 대비 초과수익률.

    한국 시장의 외인·기관 순매수처럼 '큰손이 담고 있나'를 직접 볼 수 없는 대신,
    지수 대비 얼마나 강한지로 자금 유입 강도를 간접 추정한다.
    양수면 지수보다 강하다는 뜻.
    """
    stock_returns = {
        label: _r2(compute_return_pct(closes, n)) for label, n in RETURN_WINDOWS.items()
    }
    out: dict[str, Any] = {"stock_return_pct": stock_returns, "vs_benchmark": {}}

    for name, bench in benchmark_closes.items():
        if not bench:
            continue
        excess: dict[str, float | None] = {}
        for label, n in RETURN_WINDOWS.items():
            s = compute_return_pct(closes, n)
            b = compute_return_pct(bench, n)
            excess[label] = _r2(s - b) if (s is not None and b is not None) else None
        out["vs_benchmark"][name] = {
            "benchmark_return_pct": {
                label: _r2(compute_return_pct(bench, n)) for label, n in RETURN_WINDOWS.items()
            },
            "excess_return_pct": excess,
        }
    return out


def analyze_technicals(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    volumes: list[int],
    benchmark_closes: dict[str, list[float]] | None = None,
) -> dict[str, Any]:
    """일봉 시계열에서 계산 가능한 원시 수치만 반환."""
    out: dict[str, Any] = {}
    current = closes[-1] if closes else None

    for period in (5, 20, 60, 200):
        out[f"ma{period}"] = _r2(compute_ma(closes, period))

    out.update(compute_bollinger(closes))
    out["atr14"] = _r2(compute_atr(highs, lows, closes, 14))
    if out["atr14"] and current:
        out["atr14_pct_of_price"] = _r2(out["atr14"] / current * 100)
    else:
        out["atr14_pct_of_price"] = None

    # 거래량은 전일 대비보다 20일 평균 대비가 급등 판단에 안정적이다.
    if len(volumes) >= 21:
        avg20 = sum(volumes[-21:-1]) / 20
        out["volume_avg20"] = int(avg20)
        out["volume_ratio_vs_avg20"] = round(volumes[-1] / avg20, 2) if avg20 > 0 else None
    else:
        out["volume_avg20"] = None
        out["volume_ratio_vs_avg20"] = None
    out["volume_ratio_vs_prev_day"] = (
        round(volumes[-1] / volumes[-2], 2) if len(volumes) >= 2 and volumes[-2] > 0 else None
    )

    window = closes[-252:] if len(closes) >= 252 else closes
    if window and current:
        hi52, lo52 = max(window), min(window)
        out["high_52w"] = _r2(hi52)
        out["low_52w"] = _r2(lo52)
        out["pct_from_52w_high"] = _r2((current - hi52) / hi52 * 100) if hi52 else None
        out["pct_from_52w_low"] = _r2((current - lo52) / lo52 * 100) if lo52 else None
    else:
        out["high_52w"] = out["low_52w"] = None
        out["pct_from_52w_high"] = out["pct_from_52w_low"] = None

    out["relative_strength"] = compute_relative_strength(closes, benchmark_closes or {})
    return out
