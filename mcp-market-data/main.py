"""
Market Data MCP 서버 (퀀트/차트 분석).
Tools: get_current_price_and_chart, get_institutional_buying
"""
import json
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from services.kis_api import get_price, get_minute_chart, get_investor_trend, get_daily_chart
from services.indicators import analyze_technicals

mcp = FastMCP("market-data", json_response=True)


def _parse_price_response(res: dict[str, Any], ticker_code: str) -> dict[str, Any]:
    """한투 현재가 응답에서 숫자 필드 추출."""
    out: dict[str, Any] = {
        "price": 0,
        "change_pct": "0%",
        "volume": 0,
        "volume_ratio": None,
        "high": 0,
        "low": 0,
        "open": 0,
        "prev_close": 0,
    }
    data = res or {}
    output = data.get("output") or data.get("output1") or data.get("output2") or data

    if isinstance(output, list) and output:
        o = output[0]
    elif isinstance(output, dict):
        o = output
    else:
        o = {}

    if isinstance(o, dict) and o:
        try:
            out["price"] = int(o.get("stck_prpr") or o.get("stckPrpr") or 0)
        except (TypeError, ValueError):
            out["price"] = 0
        try:
            out["prev_close"] = int(o.get("prdy_clpr") or o.get("prdyClpr") or out["price"])
        except (TypeError, ValueError):
            out["prev_close"] = out["price"]
        try:
            out["volume"] = int(o.get("acml_vol") or o.get("acmlVol") or 0)
        except (TypeError, ValueError):
            out["volume"] = 0
        try:
            out["high"] = int(o.get("stck_hgpr") or o.get("stckHgpr") or 0)
        except (TypeError, ValueError):
            out["high"] = 0
        try:
            out["low"] = int(o.get("stck_lwpr") or o.get("stckLwpr") or 0)
        except (TypeError, ValueError):
            out["low"] = 0
        try:
            out["open"] = int(o.get("stck_oprc") or o.get("stckOprc") or 0)
        except (TypeError, ValueError):
            out["open"] = 0

        prdy_ctrt = o.get("prdy_ctrt") or o.get("prdyCtrt") or "0"
        if isinstance(prdy_ctrt, (int, float)):
            out["change_pct"] = f"{prdy_ctrt:+.1f}%"
        else:
            try:
                num = float(str(prdy_ctrt))
                out["change_pct"] = f"{num:+.1f}%"
            except (TypeError, ValueError):
                out["change_pct"] = "0%"

    return out


def _parse_daily_chart(res: dict[str, Any]) -> tuple[list[float], int]:
    """일봉 응답에서 종가 리스트와 전일 거래량 추출. (종가 리스트, 전일 acml_vol)."""
    body = res.get("body") or res
    for key in ("output2", "output1", "output"):
        arr = body.get(key)
        if not isinstance(arr, list):
            continue
        prices: list[float] = []
        volumes: list[int] = []
        for row in arr:
            if not isinstance(row, dict):
                continue
            cl = row.get("stck_clpr") or row.get("stckClpr")
            if cl is not None:
                try:
                    prices.append(float(cl))
                except (TypeError, ValueError):
                    pass
            vol = row.get("acml_vol") or row.get("acmlVol")
            if vol is not None:
                try:
                    volumes.append(int(vol))
                except (TypeError, ValueError):
                    pass
        if prices:
            prev_vol = volumes[-2] if len(volumes) >= 2 else 0
            return (prices, prev_vol)
    return ([], 0)


def _parse_minute_chart(res: dict[str, Any]) -> list[dict[str, Any]]:
    """분봉 응답에서 시간·가격 리스트 추출."""
    body = res.get("body") or res
    for key in ("output2", "output1", "output"):
        arr = body.get(key)
        if not isinstance(arr, list):
            continue
        out: list[dict[str, Any]] = []
        for row in arr:
            if not isinstance(row, dict):
                continue
            time_val = row.get("stck_cntg_hour") or row.get("stckCntgHour") or row.get("cntg_hour") or ""
            prc = row.get("stck_prpr") or row.get("stckPrpr") or row.get("cntg_prpr")
            if prc is not None:
                try:
                    out.append({"time": time_val, "price": int(prc)})
                except (TypeError, ValueError):
                    pass
        if out:
            return out
    return []


def _parse_investor(res: dict[str, Any]) -> list[dict[str, Any]]:
    """투자자별 매매 동향 일별 리스트."""
    body = res.get("body") or res
    output = body.get("output") or body.get("output1") or body.get("output2")
    if isinstance(output, list):
        return output
    if isinstance(output, dict):
        return [output]
    return []


def _row_int(row: dict[str, Any], *keys: str) -> int:
    """지정한 키 중 존재하는 첫 값을 정수로 반환."""
    for k in keys:
        v = row.get(k)
        if v is None:
            continue
        try:
            return int(v)
        except (TypeError, ValueError):
            pass
    return 0


@mcp.tool()
def get_current_price_and_chart(
    ticker_code: str,
    chart_type: str = "day",
) -> str:
    """
    특정 종목의 현재가·고저·거래량·일봉 및 이평/볼린저 등 원시 수치를 반환합니다.
    ticker_code: 종목코드 6자리 (예: 005930)
    chart_type: 1min, 5min, 15min, 60min, day 중 하나
    """
    ticker_code = ticker_code.strip()
    try:
        price_res = get_price(ticker_code)
    except Exception as e:
        return json.dumps({"error": str(e), "ticker_code": ticker_code}, ensure_ascii=False, indent=2)

    parsed = _parse_price_response(price_res, ticker_code)
    current = parsed["price"]
    high = parsed["high"] or current
    low = parsed["low"] or current
    volume = parsed["volume"]
    prev_close = parsed["prev_close"] or current

    daily_prices: list[float] = []
    prev_volume = 0
    try:
        daily_res = get_daily_chart(ticker_code)
        daily_prices, prev_volume = _parse_daily_chart(daily_res)
        if daily_prices and len(daily_prices) > 1:
            daily_prices = list(reversed(daily_prices))
    except Exception:
        pass

    minute_candles: list[dict[str, Any]] = []
    if chart_type and chart_type.lower() != "day":
        try:
            minute_res = get_minute_chart(ticker_code)
            minute_candles = _parse_minute_chart(minute_res)
        except Exception:
            pass

    technicals = analyze_technicals(
        current_price=current,
        high=high,
        low=low,
        prev_close=prev_close,
        volume=volume,
        prev_volume=prev_volume,
        daily_prices=daily_prices if len(daily_prices) >= 5 else None,
    )

    payload = {
        "ticker": {"code": ticker_code},
        "chart_type": chart_type,
        "current": {
            "price": current,
            "change_pct": parsed["change_pct"],
            "volume": volume,
            "high": high,
            "low": low,
            "open": parsed["open"],
        },
        "technicals": {
            "ma5": technicals.get("ma5"),
            "ma20": technicals.get("ma20"),
            "ma60": technicals.get("ma60"),
            "volume_ratio": technicals.get("volume_ratio"),
            "pullback_pct_vs_high": technicals.get("pullback_pct_vs_high"),
            "bollinger_upper": technicals.get("bollinger_upper"),
            "bollinger_mid": technicals.get("bollinger_mid"),
            "bollinger_lower": technicals.get("bollinger_lower"),
        },
    }
    if chart_type and chart_type.lower() != "day" and minute_candles:
        payload["minute"] = {
            "requested_interval": chart_type,
            "candles": minute_candles,
            "note": "당일 분봉. 한투 API는 구간별 봉 단위 미지원, 이평/볼린저는 일봉 기준.",
        }
    return json.dumps(payload, ensure_ascii=False, indent=2)


@mcp.tool()
def get_institutional_buying(
    ticker_code: str,
    days_back: int = 5,
) -> str:
    """
    특정 종목의 외국인/기관 수급(순매수·순매도)을 최근 N일간 반환합니다.
    ticker_code: 종목코드 6자리
    days_back: 최근 N일간 수급 조회
    """
    ticker_code = ticker_code.strip()
    try:
        res = get_investor_trend(ticker_code)
    except Exception as e:
        return json.dumps({"error": str(e), "ticker_code": ticker_code}, ensure_ascii=False, indent=2)

    # 한투 API: frgn_ntby_tr_pbmn 등은 천원 단위. 원으로 변환하여 반환.
    INVESTOR_AMT_MULTIPLIER = 1000

    rows = _parse_investor(res)
    foreign_daily: list[dict[str, Any]] = []
    inst_daily: list[dict[str, Any]] = []
    individual_daily: list[dict[str, Any]] = []
    individual_net = 0
    foreign_net = 0
    inst_net = 0
    for row in (rows[:days_back] if rows else []):
        if not isinstance(row, dict):
            continue
        date_str = row.get("stck_bsop_date") or ""
        f_amt = _row_int(row, "frgn_ntby_tr_pbmn") * INVESTOR_AMT_MULTIPLIER
        o_amt = _row_int(row, "orgn_ntby_tr_pbmn") * INVESTOR_AMT_MULTIPLIER
        p_amt = _row_int(row, "prsn_ntby_tr_pbmn") * INVESTOR_AMT_MULTIPLIER
        foreign_net += f_amt
        inst_net += o_amt
        individual_net += p_amt
        foreign_daily.append({"date": date_str, "net_buying_amt": f_amt})
        inst_daily.append({"date": date_str, "net_buying_amt": o_amt})
        individual_daily.append({"date": date_str, "net_buying_amt": p_amt})

    payload = {
        "ticker": {"code": ticker_code},
        "days_requested": days_back,
        "foreign": {
            "net_buying_amt_sum": foreign_net,
            "daily": foreign_daily,
        },
        "institution": {
            "net_buying_amt_sum": inst_net,
            "daily": inst_daily,
        },
        "individual": {
            "net_buying_amt_sum": individual_net,
            "daily": individual_daily,
        },
        "unit": "원",
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def main() -> None:
    print("market-data MCP: stdio 대기 중. (직접 실행 시 여기서 입력하지 말고 Ctrl+C로 종료)", file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
