"""
일봉 규칙 백테스트 엔진. 해석·판단은 하지 않고 통계만 낸다.

주간 튜닝은 실제 픽 몇 개로 규칙을 검증하는데, 표본이 작아 운에 좌우된다.
이 엔진은 같은 규칙을 수년치·수십 종목에 돌려 승률·손익비·낙폭을 본다.

설계 원칙 (vectorbt·freqtrade에서 가져온 것):
- 신호는 t일 종가로 계산하고 진입은 t+1일 시가. 당일 종가로 사는 미래 참조를 막는다.
- 손절은 신호일 ATR의 배수. 같은 날 손절가와 목표가를 둘 다 건드리면 손절로 본다(보수적).
- 시가가 손절가 아래로 갭하락하면 시가에 청산한다.
- 표본 내/표본 외(최근 구간)를 나눠 보고한다. 표본 외 성과가 무너지면 과최적화다.
- 연속 손실 후 쉬기(freqtrade의 Protections)를 옵션으로 둔다.
- 조건 없이 매일 같은 방식으로 샀을 때(baseline)와 비교해야 규칙의 기여를 알 수 있다.

입력 bars는 과거→현재 오름차순 dict 리스트: date, open, high, low, close, volume.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from statistics import median
from typing import Any

FEATURES = {
    "ret_1d": "전일 대비 수익률(%)",
    "ret_5d": "5거래일 수익률(%)",
    "ret_20d": "20거래일 수익률(%)",
    "gap_pct": "시가 갭(%) — 신호일 시가 / 전일 종가",
    "body_pct": "신호일 몸통(%) — 종가 / 시가",
    "close_position": "신호일 종가 위치 0~1 — (종가-저가)/(고가-저가), 1이면 고가 마감",
    "volume_ratio_avg20": "거래량 / 직전 20일 평균",
    "trade_value": "거래대금 (종가 x 거래량)",
    "close_vs_ma5": "종가의 MA5 대비 괴리(%)",
    "close_vs_ma20": "종가의 MA20 대비 괴리(%)",
    "close_vs_ma60": "종가의 MA60 대비 괴리(%)",
    "ma_aligned": "MA5>MA20>MA60 정배열이면 1, 아니면 0",
    "pct_from_52w_high": "250일 최고 종가 대비(%) — 0에 가까울수록 신고가",
    "atr_pct": "ATR14 / 종가(%)",
    "rsi14": "RSI(14)",
    "rs_20d": "벤치마크 대비 20일 초과수익률(%p)",
    "rs_5d": "벤치마크 대비 5일 초과수익률(%p)",
}

OPS = {">", ">=", "<", "<=", "==", "between"}


def _r2(v: float | None) -> float | None:
    return round(v, 2) if v is not None else None


def _pct(a: float | None, b: float | None) -> float | None:
    if a is None or not b:
        return None
    return (a - b) / b * 100


def _sma(values: list[float], end: int, period: int) -> float | None:
    if end + 1 < period:
        return None
    window = values[end + 1 - period:end + 1]
    return sum(window) / period


def _atr_series(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    trs: list[float] = []
    for k in range(len(closes)):
        if k == 0:
            trs.append(highs[0] - lows[0])
        else:
            prev = closes[k - 1]
            trs.append(max(highs[k] - lows[k], abs(highs[k] - prev), abs(lows[k] - prev)))
        if k >= period:
            out[k] = sum(trs[k + 1 - period:k + 1]) / period
    return out


def _rsi_series(closes: list[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = losses = 0.0
    for k in range(1, period + 1):
        d = closes[k] - closes[k - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    avg_g, avg_l = gains / period, losses / period
    for k in range(period, len(closes)):
        if k > period:
            d = closes[k] - closes[k - 1]
            avg_g = (avg_g * (period - 1) + max(d, 0)) / period
            avg_l = (avg_l * (period - 1) + max(-d, 0)) / period
        out[k] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    return out


def compute_features(bars: list[dict[str, Any]], bench_closes_by_date: dict[str, float] | None = None) -> list[dict[str, Any]]:
    """bars 각 날짜의 피처. 해당 날짜 종가까지의 정보만 쓴다."""
    o = [float(b["open"] or b["close"]) for b in bars]
    h = [float(b["high"] or b["close"]) for b in bars]
    lo = [float(b["low"] or b["close"]) for b in bars]
    c = [float(b["close"]) for b in bars]
    v = [float(b.get("volume") or 0) for b in bars]
    atr = _atr_series(h, lo, c)
    rsi = _rsi_series(c)
    bench = bench_closes_by_date or {}
    bench_c = [bench.get(b["date"]) for b in bars]

    feats: list[dict[str, Any]] = []
    for k in range(len(bars)):
        f: dict[str, Any] = {"date": bars[k]["date"], "atr": atr[k]}
        f["ret_1d"] = _pct(c[k], c[k - 1]) if k >= 1 else None
        f["ret_5d"] = _pct(c[k], c[k - 5]) if k >= 5 else None
        f["ret_20d"] = _pct(c[k], c[k - 20]) if k >= 20 else None
        f["gap_pct"] = _pct(o[k], c[k - 1]) if k >= 1 else None
        f["body_pct"] = _pct(c[k], o[k])
        rng = h[k] - lo[k]
        f["close_position"] = (c[k] - lo[k]) / rng if rng > 0 else 0.5
        avg20 = sum(v[k - 20:k]) / 20 if k >= 20 else None
        f["volume_ratio_avg20"] = v[k] / avg20 if avg20 else None
        f["trade_value"] = c[k] * v[k]
        ma5, ma20, ma60 = _sma(c, k, 5), _sma(c, k, 20), _sma(c, k, 60)
        f["close_vs_ma5"] = _pct(c[k], ma5)
        f["close_vs_ma20"] = _pct(c[k], ma20)
        f["close_vs_ma60"] = _pct(c[k], ma60)
        f["ma_aligned"] = (1 if ma5 > ma20 > ma60 else 0) if None not in (ma5, ma20, ma60) else None
        window = c[max(0, k - 249):k + 1]
        f["pct_from_52w_high"] = _pct(c[k], max(window)) if k >= 59 else None
        f["atr_pct"] = atr[k] / c[k] * 100 if atr[k] and c[k] else None
        f["rsi14"] = rsi[k]
        for label, n in (("rs_20d", 20), ("rs_5d", 5)):
            if k >= n and bench_c[k] and bench_c[k - n]:
                f[label] = _pct(c[k], c[k - n]) - _pct(bench_c[k], bench_c[k - n])
            else:
                f[label] = None
        feats.append(f)
    return feats


def validate_conditions(conditions: list[dict[str, Any]]) -> str | None:
    for cond in conditions:
        feat, op = cond.get("feature"), cond.get("op")
        if feat not in FEATURES:
            return f"지원하지 않는 feature: {feat} (가능: {', '.join(FEATURES)})"
        if op not in OPS:
            return f"지원하지 않는 op: {op} (가능: {', '.join(sorted(OPS))})"
        val = cond.get("value")
        if op == "between":
            if not (isinstance(val, (list, tuple)) and len(val) == 2):
                return f"between은 value가 [하한, 상한]이어야 합니다: {cond}"
        elif not isinstance(val, (int, float)):
            return f"value는 숫자여야 합니다: {cond}"
    return None


def _match(f: dict[str, Any], conditions: list[dict[str, Any]]) -> bool:
    for cond in conditions:
        x = f.get(cond["feature"])
        if x is None:
            return False
        op, val = cond["op"], cond["value"]
        if op == ">" and not x > val:
            return False
        if op == ">=" and not x >= val:
            return False
        if op == "<" and not x < val:
            return False
        if op == "<=" and not x <= val:
            return False
        if op == "==" and not x == val:
            return False
        if op == "between" and not (val[0] <= x <= val[1]):
            return False
    return True


def simulate_ticker(
    ticker: str,
    bars: list[dict[str, Any]],
    feats: list[dict[str, Any]],
    conditions: list[dict[str, Any]],
    stop_atr_mult: float,
    target_atr_mult: float | None,
    max_hold_days: int,
    cost_pct: float,
    max_entry_gap_pct: float | None,
    start_index: int = 60,
) -> list[dict[str, Any]]:
    """한 종목에 규칙을 적용. 포지션은 하나씩만 (청산 전 새 신호는 무시)."""
    trades: list[dict[str, Any]] = []
    k = start_index
    n = len(bars)
    while k < n - 1:
        f = feats[k]
        if not f["atr"] or not _match(f, conditions):
            k += 1
            continue
        entry_i = k + 1
        entry = float(bars[entry_i]["open"] or bars[entry_i]["close"])
        signal_close = float(bars[k]["close"])
        gap = _pct(entry, signal_close)
        if max_entry_gap_pct is not None and gap is not None and gap > max_entry_gap_pct:
            k += 1
            continue
        stop = entry - stop_atr_mult * f["atr"]
        target = entry + target_atr_mult * f["atr"] if target_atr_mult else None

        exit_i, exit_px, reason = None, None, None
        for d in range(entry_i, min(n, entry_i + max_hold_days)):
            b = bars[d]
            op_ = float(b["open"] or b["close"])
            hi, low_ = float(b["high"] or b["close"]), float(b["low"] or b["close"])
            if d > entry_i and op_ <= stop:
                exit_i, exit_px, reason = d, op_, "stop_gap"
                break
            if low_ <= stop:
                exit_i, exit_px, reason = d, stop, "stop"
                break
            if target is not None:
                if d > entry_i and op_ >= target:
                    exit_i, exit_px, reason = d, op_, "target_gap"
                    break
                if hi >= target:
                    exit_i, exit_px, reason = d, target, "target"
                    break
        if exit_i is None:
            last = min(n - 1, entry_i + max_hold_days - 1)
            if entry_i + max_hold_days - 1 > n - 1:
                # 데이터 끝까지 보유 기간이 안 끝난 거래는 결과가 미확정이라 뺀다
                break
            exit_i, exit_px, reason = last, float(bars[last]["close"]), "time"

        ret = (exit_px - entry) / entry * 100 - cost_pct
        trades.append({
            "ticker": ticker,
            "signal_date": bars[k]["date"],
            "entry_date": bars[entry_i]["date"],
            "exit_date": bars[exit_i]["date"],
            "entry": entry,
            "exit": exit_px,
            "entry_gap_pct": _r2(gap),
            "return_pct": ret,
            "hold_days": exit_i - entry_i + 1,
            "exit_reason": reason,
        })
        k = exit_i + 1
    return trades


def _parse_date(s: str) -> datetime:
    s = s.replace("-", "")
    return datetime.strptime(s[:8], "%Y%m%d")


def apply_cooldown(trades: list[dict[str, Any]], losses_in_row: int, cooldown_days: int) -> tuple[list[dict[str, Any]], int]:
    """
    연속 손실 후 쉬기 (freqtrade Protections의 StoplossGuard 개념).
    청산일 순으로 losses_in_row번 연속 손실이 나면 그 뒤 cooldown_days(달력일) 동안 신규 진입을 건너뛴다.
    """
    if not losses_in_row or not cooldown_days:
        return trades, 0
    kept: list[dict[str, Any]] = []
    skipped = 0
    blocked_until: datetime | None = None
    for t in sorted(trades, key=lambda x: x["entry_date"]):
        entry = _parse_date(t["entry_date"])
        if blocked_until and entry <= blocked_until:
            skipped += 1
            continue
        kept.append(t)
        closed = sorted((x for x in kept if _parse_date(x["exit_date"]) <= entry), key=lambda x: x["exit_date"])
        recent = closed[-losses_in_row:]
        if len(recent) == losses_in_row and all(x["return_pct"] <= 0 for x in recent):
            blocked_until = _parse_date(recent[-1]["exit_date"]) + timedelta(days=cooldown_days)
    return kept, skipped


def summarize(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {"trades": 0}
    rets = [t["return_pct"] for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    gross_win, gross_loss = sum(wins), -sum(losses)

    # 거래마다 같은 금액을 건다고 보고 청산일 순으로 수익률을 더한 곡선의 최대 낙폭.
    # 여러 종목 거래가 겹치므로 복리로 이어 붙이면 숫자가 의미 없이 커진다.
    total, peak, mdd = 0.0, 0.0, 0.0
    for t in sorted(trades, key=lambda x: x["exit_date"]):
        total += t["return_pct"]
        peak = max(peak, total)
        mdd = min(mdd, total - peak)

    reasons: dict[str, int] = {}
    for t in trades:
        reasons[t["exit_reason"]] = reasons.get(t["exit_reason"], 0) + 1

    return {
        "trades": len(trades),
        "tickers_with_trades": len({t["ticker"] for t in trades}),
        "win_rate_pct": _r2(len(wins) / len(rets) * 100),
        "avg_return_pct": _r2(sum(rets) / len(rets)),
        "median_return_pct": _r2(median(rets)),
        "avg_win_pct": _r2(sum(wins) / len(wins)) if wins else None,
        "avg_loss_pct": _r2(sum(losses) / len(losses)) if losses else None,
        "profit_factor": _r2(gross_win / gross_loss) if gross_loss > 0 else None,
        "sum_return_pct": _r2(total),
        "max_drawdown_sum_pct": _r2(mdd),
        "avg_hold_days": _r2(sum(t["hold_days"] for t in trades) / len(trades)),
        "exit_reasons": reasons,
    }


def run(
    data: dict[str, list[dict[str, Any]]],
    bench_by_ticker: dict[str, dict[str, float]],
    conditions: list[dict[str, Any]],
    stop_atr_mult: float = 1.5,
    target_atr_mult: float | None = 3.0,
    max_hold_days: int = 5,
    cost_pct: float = 0.25,
    max_entry_gap_pct: float | None = None,
    oos_fraction: float = 0.3,
    cooldown_after_losses: int = 0,
    cooldown_days: int = 0,
    include_baseline: bool = True,
    price_digits: int = 0,
) -> dict[str, Any]:
    """종목별 bars를 받아 규칙 성과·표본 내/외·baseline을 계산한다."""
    all_trades: list[dict[str, Any]] = []
    baseline: list[dict[str, Any]] = []
    signal_days = 0
    dates: list[str] = []
    for ticker, bars in data.items():
        if len(bars) < 80:
            continue
        feats = compute_features(bars, bench_by_ticker.get(ticker))
        dates.extend(b["date"] for b in bars[60:])
        signal_days += sum(1 for f in feats[60:] if f["atr"] and _match(f, conditions))
        all_trades += simulate_ticker(ticker, bars, feats, conditions, stop_atr_mult, target_atr_mult,
                                      max_hold_days, cost_pct, max_entry_gap_pct)
        if include_baseline:
            baseline += simulate_ticker(ticker, bars, feats, [], stop_atr_mult, target_atr_mult,
                                        max_hold_days, cost_pct, None)

    all_trades, skipped = apply_cooldown(all_trades, cooldown_after_losses, cooldown_days)

    if not dates:
        return {"error": "백테스트할 데이터가 부족합니다 (종목당 최소 80거래일 필요)."}
    unique_dates = sorted(set(dates))
    cutoff = unique_dates[int(len(unique_dates) * (1 - oos_fraction))] if 0 < oos_fraction < 1 else None
    in_sample = [t for t in all_trades if cutoff is None or t["signal_date"] < cutoff]
    out_sample = [t for t in all_trades if cutoff is not None and t["signal_date"] >= cutoff]

    warnings = []
    if len(all_trades) < 30:
        warnings.append(f"거래 {len(all_trades)}건 — 30건 미만이라 통계적으로 신뢰하기 어렵습니다.")
    if out_sample and in_sample:
        ins, outs = summarize(in_sample), summarize(out_sample)
        if (ins.get("avg_return_pct") or 0) > 0 and (outs.get("avg_return_pct") or 0) <= 0:
            warnings.append("표본 내에서는 수익이지만 표본 외(최근 구간)에서 손실입니다 — 과최적화 의심.")
    if len(out_sample) < 10:
        warnings.append(f"표본 외 거래가 {len(out_sample)}건뿐이라 최근 구간 검증력이 약합니다.")

    def _fmt(t: dict[str, Any]) -> dict[str, Any]:
        return {**t, "entry": round(t["entry"], price_digits), "exit": round(t["exit"], price_digits),
                "return_pct": _r2(t["return_pct"])}

    result: dict[str, Any] = {
        "period": {"from": unique_dates[0], "to": unique_dates[-1], "oos_from": cutoff},
        "signal_days": signal_days,
        "all": summarize(all_trades),
        "in_sample": summarize(in_sample),
        "out_of_sample": summarize(out_sample),
        "cooldown_skipped_trades": skipped,
        "recent_trades": [_fmt(t) for t in sorted(all_trades, key=lambda x: x["entry_date"])[-15:]],
        "warnings": warnings,
        "stats_note": ("sum_return_pct·max_drawdown_sum_pct는 거래마다 같은 금액을 걸었을 때 "
                       "수익률(%)을 단순 합산한 값입니다 (복리 아님, 동시 보유 무시)."),
    }
    if include_baseline:
        result["baseline_every_day"] = summarize(baseline)
        result["baseline_note"] = ("조건 없이 매일 같은 손절·목표·보유일로 샀을 때입니다. "
                                   "규칙의 avg_return_pct가 이보다 높아야 조건이 기여한 것입니다.")
    return result
