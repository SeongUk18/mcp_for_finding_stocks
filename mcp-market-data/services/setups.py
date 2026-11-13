"""
셋업 스캔 — 백테스트로 표본 내·외 모두 기대값이 양수였던 진입 유형만 고정 종목군에서 찾는다.

근거 (2026-11 조사, 대형·유동성 상위 고정 종목군 약 4년, 최근 30%를 표본 외로 분리, 비용 차감):
  신호는 t일 종가로 계산하고 진입은 t+1일. 청산은 손절 1.5 ATR · 목표 3 ATR · 5거래일.
  - dip (상승 추세 중 급락 뒤 추가 눌림): 두 시장, 표본 내·외, 지수 국면 상·하 모두 플러스였던 유일한 유형
  - high_pullback (신고가권 정배열 종목의 음봉 눌림): 지수가 20일선 위일 때만 유효
  - breakout_pullback (52주 신고가 돌파 뒤 눌림): 국장에서만 확인, 지수가 20일선 위일 때만 유효
  같은 조사에서 당일 급등 추격·급등 다음 날 진입·상대강도 단독·거래량 빌드업은 우위가 없었다.

조건 수치는 조사 때 쓴 값 그대로다. 바꾸려면 다시 백테스트해서 표본 외까지 확인해야 한다.
이 파일은 mcp-market-data와 mcp-us-market-data에 같은 내용으로 들어 있다.
"""
from __future__ import annotations

from statistics import mean
from typing import Any

from services import backtest

STOP_ATR = 1.5
TARGET_ATR = 3.0
MAX_HOLD_DAYS = 5
MIN_BARS = 130

UNIVERSE = {
    "kr": """005930 000660 373220 207940 005380 000270 068270 105560 055550 035420 035720 012330 005490 051910 006400
028260 086790 066570 003550 034730 015760 032830 009150 033780 017670 030200 010130 011200 009540 329180 010140
042660 012450 047810 064350 079550 034020 003490 096770 010950 018260 316140 024110 138040 000810 090430 051900
036570 259960 251270 352820 011170 009830 000720 028050 267260 010120 298040 042700 247540 086520 196170 028300
066970 277810 403870 058470 145020 214150 035900 041510 263750 293490 112040 039030 240810 036930 000100 128940
302440 326030 006800 071050 016360 039490 161390 004020 180640 036460 294870""".split(),
    "us": """AAPL MSFT NVDA AMZN GOOGL META TSLA AVGO AMD NFLX CRM ORCL ADBE INTC QCOM MU AMAT LRCX KLAC TXN CSCO IBM NOW
PANW CRWD SNOW PLTR NET DDOG SHOP UBER ABNB COIN PYPL JPM BAC GS MS WFC C V MA AXP UNH LLY JNJ PFE MRK ABBV TMO ISRG
XOM CVX COP SLB CAT DE BA GE HON LMT RTX UPS FDX WMT COST HD LOW TGT NKE SBUX MCD DIS CMCSA T VZ KO PEP PG MRVL ANET
SMCI DELL ZS MDB TEAM ROKU DASH RBLX MRNA GILD AMGN REGN VRTX F GM""".split(),
}


def _c(feature: str, op: str, value: Any) -> dict[str, Any]:
    return {"feature": feature, "op": op, "value": value}


# entry_atr: None이면 다음 거래일 시가 진입, 숫자면 신호일 종가에서 그만큼(ATR 배수) 아래 지정가
SETUPS: dict[str, dict[str, dict[str, Any]]] = {
    "kr": {
        "dip": {
            "label": "상승 추세 중 급락 뒤 추가 눌림",
            "conditions": [_c("ret_5d", "<=", -8), _c("close_vs_ma60", ">=", 0)],
            "entry_atr": 0.5, "needs_regime_up": False,
            "backtest": {"trades": 391, "win_rate_pct": 55, "avg_return_pct": 0.66,
                         "in_sample_avg": 0.57, "out_of_sample_avg": 0.73, "baseline_avg": 0.14},
        },
        "high_pullback": {
            "label": "신고가권 정배열 종목의 음봉 눌림",
            "conditions": [_c("pct_from_52w_high", "between", [-8, -1]), _c("ma_aligned", "==", 1),
                           _c("ret_1d", "<", 0), _c("close_vs_ma20", ">", 0)],
            "entry_atr": None, "needs_regime_up": True,
            "backtest": {"trades": 2311, "win_rate_pct": 47, "avg_return_pct": 0.43,
                         "in_sample_avg": 0.43, "out_of_sample_avg": 0.45, "baseline_avg": 0.06,
                         "regime_up_avg": 0.88, "regime_down_avg": -0.78},
        },
        "breakout_pullback": {
            "label": "52주 신고가 돌파 뒤 눌림",
            "conditions": [_c("new_high", "==", 1), _c("volume_ratio_avg20", ">=", 1.5)],
            "entry_atr": 0.5, "needs_regime_up": True,
            "backtest": {"trades": 734, "win_rate_pct": 47, "avg_return_pct": 0.81,
                         "in_sample_avg": 0.66, "out_of_sample_avg": 1.05, "baseline_avg": 0.14,
                         "regime_up_avg": 1.19, "regime_down_avg": -0.61},
        },
    },
    "us": {
        "dip": {
            "label": "상승 추세 중 급락 뒤 추가 눌림",
            "conditions": [_c("ret_5d", "<=", -8), _c("close_vs_ma60", ">=", 0)],
            "entry_atr": 0.5, "needs_regime_up": False,
            "backtest": {"trades": 218, "win_rate_pct": 60, "avg_return_pct": 2.03,
                         "in_sample_avg": 1.18, "out_of_sample_avg": 2.99, "baseline_avg": 0.33},
        },
        "high_pullback": {
            "label": "신고가권 정배열 종목의 음봉 눌림 (깊은 지정가)",
            "conditions": [_c("pct_from_52w_high", "between", [-8, -1]), _c("ma_aligned", "==", 1),
                           _c("ret_1d", "<", 0), _c("close_vs_ma20", ">", 0)],
            "entry_atr": 1.0, "needs_regime_up": True,
            "backtest": {"trades": 1016, "win_rate_pct": 53, "avg_return_pct": 0.63,
                         "in_sample_avg": 0.66, "out_of_sample_avg": 0.56, "baseline_avg": 0.26,
                         "regime_up_avg": 0.79, "regime_down_avg": 0.07},
        },
    },
}


def add_extra_features(bars: list[dict[str, Any]], feats: list[dict[str, Any]]) -> None:
    """backtest 엔진에 없는 피처. new_high = 오늘 종가가 직전 250일 종가 최고 이상."""
    closes = [float(b["close"]) for b in bars]
    for k, f in enumerate(feats):
        f["new_high"] = 1 if k >= 120 and closes[k] >= max(closes[max(0, k - 249):k]) else 0


def regime_of(bench_closes_by_date: dict[str, float]) -> dict[str, Any]:
    """지수 종가가 자기 20일선 위인지."""
    dates = sorted(bench_closes_by_date)
    if len(dates) < 20:
        return {"up": None}
    closes = [bench_closes_by_date[d] for d in dates]
    ma20 = mean(closes[-20:])
    return {"date": dates[-1], "close": round(closes[-1], 2), "ma20": round(ma20, 2),
            "vs_ma20_pct": round((closes[-1] / ma20 - 1) * 100, 2), "up": closes[-1] > ma20}


def _match(f: dict[str, Any], conditions: list[dict[str, Any]]) -> bool:
    return backtest._match(f, conditions)


def scan(
    market: str,
    data: dict[str, list[dict[str, Any]]],
    bench_by_ticker: dict[str, dict[str, float]],
    regime_by_ticker: dict[str, dict[str, Any]],
    names: dict[str, str] | None = None,
    price_digits: int = 0,
) -> list[dict[str, Any]]:
    """종목별 최신 봉에서 셋업 조건을 확인하고, 진입·손절·목표 계획과 채점 원장용 시나리오를 붙인다."""
    names = names or {}
    out: list[dict[str, Any]] = []

    def px(v: float) -> float | int:
        return int(round(v)) if price_digits == 0 else round(v, price_digits)

    for ticker, bars in data.items():
        bars = [b for b in bars if b.get("close")]
        if len(bars) < MIN_BARS:
            continue
        feats = backtest.compute_features(bars, bench_by_ticker.get(ticker))
        add_extra_features(bars, feats)
        f, last = feats[-1], bars[-1]
        atr = f.get("atr")
        if not atr:
            continue
        regime = regime_by_ticker.get(ticker) or {}
        for key, spec in SETUPS[market].items():
            if not _match(f, spec["conditions"]):
                continue
            close = float(last["close"])
            if spec["entry_atr"] is None:
                entry = {"type": "open", "desc": "다음 거래일 시가"}
                scenario = {"id": key, "type": "open"}
            else:
                price = px(close - spec["entry_atr"] * atr)
                entry = {"type": "limit", "price": price,
                         "desc": f"다음 거래일 지정가 (신호일 종가 - ATR {spec['entry_atr']}배). 닿지 않으면 미진입"}
                scenario = {"id": key, "type": "limit", "price": price}
            scenario.update({"stop_offset": px(STOP_ATR * atr), "t1_offset": px(TARGET_ATR * atr),
                             "max_hold_days": MAX_HOLD_DAYS})
            regime_ok = (not spec["needs_regime_up"]) or bool(regime.get("up"))
            out.append({
                "ticker": ticker,
                "name": names.get(ticker),
                "setup": key,
                "setup_label": spec["label"],
                "signal_date": last["date"],
                "regime_ok": regime_ok,
                "close": px(close),
                "atr14": px(atr),
                "entry": entry,
                "stop": f"진입가 - {px(STOP_ATR * atr)} (ATR {STOP_ATR}배)",
                "target": f"진입가 + {px(TARGET_ATR * atr)} (ATR {TARGET_ATR}배)",
                "max_hold_days": MAX_HOLD_DAYS,
                "ledger_scenario": scenario,
                "features": {k: (round(f[k], 2) if isinstance(f.get(k), float) else f.get(k)) for k in (
                    "ret_1d", "ret_5d", "ret_20d", "rs_20d", "volume_ratio_avg20", "close_vs_ma5", "close_vs_ma20",
                    "close_vs_ma60", "ma_aligned", "pct_from_52w_high", "atr_pct", "rsi14")},
                "trade_value": f.get("trade_value"),
            })
    out.sort(key=lambda m: (not m["regime_ok"], -(m["trade_value"] or 0)))
    return out


def describe(market: str) -> dict[str, Any]:
    return {key: {"label": s["label"],
                  "entry": "다음 거래일 시가" if s["entry_atr"] is None else f"신호일 종가 - ATR {s['entry_atr']}배 지정가",
                  "needs_regime_up": s["needs_regime_up"], "backtest": s["backtest"]}
            for key, s in SETUPS[market].items()}
