"""
Market Data MCP 서버 (국내 퀀트/차트/수급 분석).

Tools:
  - get_kr_market_movers        : 거래량·등락률·체결강도·VI 순위 (후보 발굴)
  - get_current_price_and_chart : 현재가 + 이평/볼린저/ATR/52주/코스피 대비 상대강도 + 호가
  - get_institutional_buying    : 외인·기관 수급 종합 (확정치·가집계·창구·외국계·프로그램·공매도·융자)
  - get_kr_news_and_opinion     : 한투 속보 + 증권사 투자의견·목표주가 + 실적 컨센서스

모든 도구는 수치와 원문만 반환한다. 호재/악재 판단은 LLM이 한다.
"""
import json
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from services import kis_api as kis
from services.indicators import analyze_technicals

mcp = FastMCP("market-data", json_response=True)

# 한투 투자자 매매 대금은 천원 단위로 온다. 원으로 환산해서 내보낸다.
INVESTOR_AMT_MULTIPLIER = 1000


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _err(message: str, **extra: Any) -> str:
    return _dump({"error": message, **extra})


def _parse_price_response(res: dict[str, Any], ticker_code: str) -> dict[str, Any]:
    """한투 현재가 응답에서 숫자 필드 추출."""
    o = kis._one(res)
    price = kis.i(o.get("stck_prpr")) or 0
    prdy_ctrt = kis.f(o.get("prdy_ctrt"))
    return {
        "price": price,
        "change_pct": f"{prdy_ctrt:+.2f}%" if prdy_ctrt is not None else "0%",
        "volume": kis.i(o.get("acml_vol")) or 0,
        "high": kis.i(o.get("stck_hgpr")) or price,
        "low": kis.i(o.get("stck_lwpr")) or price,
        "open": kis.i(o.get("stck_oprc")) or 0,
        "prev_close": kis.i(o.get("stck_sdpr")) or kis.i(o.get("prdy_clpr")) or price,
        "market_cap": kis.i(o.get("hts_avls")),
        "per": kis.f(o.get("per")),
        "pbr": kis.f(o.get("pbr")),
        "foreign_hold_pct": kis.f(o.get("hts_frgn_ehrt")),
        "volume_power": kis.f(o.get("cttr")),
        "upper_limit": kis.i(o.get("stck_mxpr")),
        "lower_limit": kis.i(o.get("stck_llam")),
    }


def _parse_minute_chart(res: dict[str, Any]) -> list[dict[str, Any]]:
    """분봉 응답에서 시간·가격 리스트 추출."""
    out = []
    for row in kis._rows(res, "output2", "output1", "output"):
        prc = row.get("stck_prpr") or row.get("cntg_prpr")
        if prc is None:
            continue
        out.append({
            "time": row.get("stck_cntg_hour") or row.get("cntg_hour") or "",
            "price": kis.i(prc),
            "volume": kis.i(row.get("cntg_vol")),
        })
    return out


@mcp.tool()
def get_kr_market_movers(
    category: str = "volume",
    limit: int = 30,
    min_volume: int = 100000,
    min_price: int = 0,
    max_price: int = 1000000,
    exclude_noise: bool = True,
) -> str:
    """
    오늘 실제로 움직인 국내 종목을 훑습니다. 유튜브 스캔과 별개인 후보 발굴 채널입니다.

    - category:
        volume   : 거래량 순위 (기본)
        surge    : 등락률 상위
        plunge   : 등락률 하위
        power    : 체결강도 상위 (매수 체결이 매도보다 강한 종목)
        vi       : VI(변동성완화장치) 발동 종목 — 장중에만 값이 있음
    - limit: 반환할 최대 종목 수
    - min_volume: 최소 거래량
    - min_price / max_price: 가격 구간 필터
    - exclude_noise: ETF·ETN·인버스·우선주·관리종목·스팩 제외 (기본 True)
      끄면 상위가 KODEX 인버스 같은 ETF로 도배되니 웬만하면 켜두세요.

    순위는 관심도·변동성 지표일 뿐 매수 신호가 아닙니다.
    등락률 순위는 한투 응답 순서가 등락률과 일치하지 않아 서버에서 다시 정렬합니다.
    """
    try:
        if category == "volume":
            items = kis.get_volume_rank(min_price=min_price, max_price=max_price,
                                        min_volume=min_volume, exclude_noise=exclude_noise)
            label = "거래량"
        elif category == "surge":
            items = kis.get_fluctuation_rank(rank_sort="0", min_volume=min_volume,
                                             exclude_noise=exclude_noise)
            label = "등락률 상위"
        elif category == "plunge":
            items = kis.get_fluctuation_rank(rank_sort="1", min_volume=min_volume,
                                             exclude_noise=exclude_noise)
            label = "등락률 하위"
        elif category == "power":
            items = kis.get_volume_power_rank(min_price=min_price, max_price=max_price,
                                              min_volume=min_volume, exclude_noise=exclude_noise)
            label = "체결강도"
        elif category == "vi":
            items = kis.get_vi_status()
            label = "VI 발동"
        else:
            return _err(f"지원하지 않는 category: {category} (가능: volume, surge, plunge, power, vi)")
    except Exception as e:
        return _err(f"순위 조회 실패: {e}", category=category)

    payload = {
        "category": category,
        "category_label": label,
        "exclude_noise": exclude_noise,
        "returned": len(items[:limit]),
        "items": items[:limit],
        "note": "순위는 관심도 지표일 뿐 매수 신호가 아닙니다.",
    }
    if category == "vi" and not items:
        payload["note"] = "VI 발동 종목이 없습니다. 장 마감 후에는 항상 비어 있습니다."
    return _dump(payload)


@mcp.tool()
def get_current_price_and_chart(
    ticker_code: str,
    chart_type: str = "day",
    include_orderbook: bool = False,
    include_relative_strength: bool = True,
) -> str:
    """
    특정 종목의 현재가·고저·거래량과 이평/볼린저/ATR/52주/상대강도 등 원시 수치를 반환합니다.

    - ticker_code: 종목코드 6자리 (예: 005930)
    - chart_type: 1min, 5min, 15min, 60min, day 중 하나
    - include_orderbook: 호가 10단계와 예상체결가를 함께 반환할지
    - include_relative_strength: 코스피·코스닥 지수 대비 초과수익률 계산 여부
      (지수 시세를 추가로 조회하므로 필요 없으면 False로 끄세요)

    이평선·볼린저·ATR은 chart_type과 무관하게 항상 일봉 기준입니다.
    상대강도가 양수면 지수보다 강했다는 뜻이며, 시장 상승에 묻어간 것인지
    종목 자체가 강한 것인지를 구분하는 데 씁니다.
    """
    ticker_code = ticker_code.strip()
    if not ticker_code:
        return _err("ticker_code가 비어 있습니다.")

    errors: dict[str, str] = {}
    try:
        price_res = kis.get_price(ticker_code)
    except Exception as e:
        return _err(f"현재가 조회 실패: {e}", ticker_code=ticker_code)

    parsed = _parse_price_response(price_res, ticker_code)
    current = parsed["price"]

    bars: list[dict[str, Any]] = []
    try:
        bars = kis.get_daily_bars(ticker_code, count=250)
    except Exception as e:
        errors["daily_bars"] = str(e)

    closes = [b["close"] for b in bars if b["close"] is not None]
    highs = [b["high"] or b["close"] for b in bars]
    lows = [b["low"] or b["close"] for b in bars]
    volumes = [b["volume"] for b in bars]
    prev_volume = volumes[-2] if len(volumes) >= 2 else 0

    index_closes: dict[str, list[float]] = {}
    if include_relative_strength:
        for name, code in (("KOSPI", kis.INDEX_KOSPI), ("KOSDAQ", kis.INDEX_KOSDAQ)):
            try:
                idx = kis.get_index_daily_bars(code, count=250)
                index_closes[name] = [b["close"] for b in idx if b["close"] is not None]
            except Exception as e:
                errors[f"index_{name}"] = str(e)

    technicals = analyze_technicals(
        current_price=current,
        high=parsed["high"],
        low=parsed["low"],
        prev_close=parsed["prev_close"],
        volume=parsed["volume"],
        prev_volume=prev_volume,
        daily_prices=closes if len(closes) >= 5 else None,
        daily_highs=highs,
        daily_lows=lows,
        daily_volumes=volumes,
        index_closes=index_closes,
    )

    payload: dict[str, Any] = {
        "ticker": {"code": ticker_code},
        "chart_type": chart_type,
        "current": {
            "price": current,
            "change_pct": parsed["change_pct"],
            "volume": parsed["volume"],
            "high": parsed["high"],
            "low": parsed["low"],
            "open": parsed["open"],
            "prev_close": parsed["prev_close"],
            "upper_limit": parsed["upper_limit"],
            "lower_limit": parsed["lower_limit"],
        },
        "fundamentals": {
            "market_cap_eok": parsed["market_cap"],
            "per": parsed["per"],
            "pbr": parsed["pbr"],
            "foreign_hold_pct": parsed["foreign_hold_pct"],
            "volume_power": parsed["volume_power"],
        },
        "technicals": technicals,
        "recent_daily": [
            {"date": b["date"], "close": b["close"], "high": b.get("high"), "low": b.get("low")}
            for b in bars[-15:]
        ],
        "unit": "원",
    }

    if chart_type and chart_type.lower() != "day":
        try:
            candles = _parse_minute_chart(kis.get_minute_chart(ticker_code))
            if candles:
                payload["minute"] = {
                    "requested_interval": chart_type,
                    "candles": candles,
                    "note": "당일 분봉. 한투 API는 구간별 봉 단위 미지원, 이평·볼린저는 일봉 기준.",
                }
        except Exception as e:
            errors["minute_chart"] = str(e)

    if include_orderbook:
        try:
            payload["orderbook"] = kis.get_asking_price(ticker_code)
        except Exception as e:
            errors["orderbook"] = str(e)

    if errors:
        payload["errors"] = errors
    return _dump(payload)


@mcp.tool()
def get_institutional_buying(
    ticker_code: str,
    days_back: int = 5,
    include_intraday_estimate: bool = True,
    include_brokers: bool = True,
    include_program: bool = True,
    include_short_sale: bool = True,
    include_loan: bool = False,
) -> str:
    """
    특정 종목의 수급을 종합해서 반환합니다. 한국 시장의 핵심 분석 도구입니다.

    - ticker_code: 종목코드 6자리
    - days_back: 일별 확정 수급을 며칠치 볼지
    - include_intraday_estimate: 장중 외인·기관 추정 가집계 포함 여부
    - include_brokers: 증권사 창구별 매매와 외국계 창구 추이 포함 여부
    - include_program: 프로그램매매 추이 포함 여부
    - include_short_sale: 일별 공매도 추이 포함 여부
    - include_loan: 신용융자 잔고 추이 포함 여부 (기본 False)

    반환 항목의 성격이 서로 다르니 구분해서 읽으세요:
      daily_confirmed  — 장 마감 후 확정된 일별 순매수. 가장 신뢰도 높음
      intraday_estimate— 장중 추정 가집계. 확정치가 아니며 마감 후 바뀔 수 있음
      brokers          — 증권사 창구별 매매. JP모간·골드만 등 외국계 창구가 매수 상위면
                         외국인 자금 유입으로 해석하는 게 일반적임
      program          — 프로그램매매 순매수. 지수 연동 자금 흐름
      short_sale       — 일별 공매도 비중
      loan             — 신용융자 잔고. 급증 시 반대매매 리스크

    개인만 순매수하고 외인·기관이 모두 순매도면 이른바 '설거지 패턴'이니 주의하세요.
    """
    ticker_code = ticker_code.strip()
    if not ticker_code:
        return _err("ticker_code가 비어 있습니다.")

    errors: dict[str, str] = {}
    payload: dict[str, Any] = {"ticker": {"code": ticker_code}, "days_requested": days_back}

    # 1) 일별 확정 수급
    try:
        rows = kis._rows(kis.get_investor_trend(ticker_code), "output", "output1", "output2")
        foreign_daily, inst_daily, indiv_daily = [], [], []
        f_sum = o_sum = p_sum = 0
        for row in rows[:days_back]:
            date = row.get("stck_bsop_date") or ""
            f_amt = (kis.i(row.get("frgn_ntby_tr_pbmn")) or 0) * INVESTOR_AMT_MULTIPLIER
            o_amt = (kis.i(row.get("orgn_ntby_tr_pbmn")) or 0) * INVESTOR_AMT_MULTIPLIER
            p_amt = (kis.i(row.get("prsn_ntby_tr_pbmn")) or 0) * INVESTOR_AMT_MULTIPLIER
            f_sum += f_amt
            o_sum += o_amt
            p_sum += p_amt
            foreign_daily.append({"date": date, "net_buying_amt": f_amt})
            inst_daily.append({"date": date, "net_buying_amt": o_amt})
            indiv_daily.append({"date": date, "net_buying_amt": p_amt})
        payload["daily_confirmed"] = {
            "foreign": {"net_buying_amt_sum": f_sum, "daily": foreign_daily},
            "institution": {"net_buying_amt_sum": o_sum, "daily": inst_daily},
            "individual": {"net_buying_amt_sum": p_sum, "daily": indiv_daily},
            "unit": "원",
        }
    except Exception as e:
        errors["daily_confirmed"] = str(e)

    # 2) 종목별 투자자 일별 상세 (외국인 등록/미등록 분리)
    try:
        payload["daily_detail"] = {
            "rows": kis.get_investor_by_stock_daily(ticker_code, days=days_back),
            "unit": "주",
            "note": "수량 기준입니다. 금액은 daily_confirmed를 보세요.",
        }
    except Exception as e:
        errors["daily_detail"] = str(e)

    # 3) 장중 추정 가집계
    if include_intraday_estimate:
        try:
            payload["intraday_estimate"] = {
                "slots": kis.get_investor_trend_estimate(ticker_code),
                "unit": "주",
                "note": "장중 추정치입니다. 확정치가 아니며 마감 후 달라질 수 있습니다.",
            }
        except Exception as e:
            errors["intraday_estimate"] = str(e)

    # 4) 창구
    if include_brokers:
        try:
            payload["brokers"] = kis.get_member_trend(ticker_code)
        except Exception as e:
            errors["brokers"] = str(e)
        try:
            payload["foreign_brokers"] = kis.get_foreign_member_trend(ticker_code)
        except Exception as e:
            errors["foreign_brokers"] = str(e)

    # 5) 프로그램매매
    if include_program:
        try:
            program = kis.get_program_trade(ticker_code)
            payload["program"] = {
                "recent": program[:10],
                "latest_net_qty": program[0]["program_net_qty"] if program else None,
                "unit_qty": "주", "unit_amt": "원",
            }
        except Exception as e:
            errors["program"] = str(e)

    # 6) 공매도
    if include_short_sale:
        try:
            payload["short_sale"] = kis.get_daily_short_sale(ticker_code, days=days_back * 2)
        except Exception as e:
            errors["short_sale"] = str(e)

    # 7) 신용융자
    if include_loan:
        try:
            payload["loan"] = {
                "daily": kis.get_daily_loan_trans(ticker_code, days=days_back * 2),
                "note": "잔고가 급증하면 반대매매 물량 리스크를 함께 고려하세요.",
            }
        except Exception as e:
            errors["loan"] = str(e)

    payload["data_freshness"] = {
        "daily_confirmed": "장 마감 후 확정",
        "daily_detail": "장 마감 후 확정",
        "intraday_estimate": "장중 추정 (확정 아님)",
        "brokers": "장중 실시간",
        "program": "장중 실시간",
        "short_sale": "일별 (전 거래일까지)",
        "loan": "일별 (전 거래일까지)",
    }
    if errors:
        payload["errors"] = errors
    return _dump(payload)


@mcp.tool()
def get_kr_news_and_opinion(
    ticker_code: str = "",
    news_limit: int = 30,
    include_opinion: bool = True,
    include_estimate: bool = True,
) -> str:
    """
    국내 속보 뉴스와 증권사 투자의견·목표주가, 실적 컨센서스를 반환합니다.

    - ticker_code: 종목코드 6자리. 비우면 시장 전체 속보만 조회
    - news_limit: 최대 뉴스 수
    - include_opinion: 증권사 투자의견·목표주가 포함 여부
    - include_estimate: 실적 컨센서스 포함 여부

    뉴스에는 관련 종목코드가 붙어 오므로 `related_tickers`로 해당 종목 기사인지 확인하세요.
    투자의견의 `gap_pct`는 목표주가 대비 괴리율입니다. 음수가 클수록 목표가까지 여유가 크다는
    뜻이지만, 목표가 자체가 과거 시점 기준이라는 점을 감안해야 합니다.
    호재/악재 판단은 하지 않습니다. 원문을 읽고 직접 해석하세요.
    """
    ticker_code = ticker_code.strip()
    payload: dict[str, Any] = {"ticker": {"code": ticker_code or None}}
    errors: dict[str, str] = {}

    try:
        news = kis.get_news_title(ticker_code, limit=news_limit)
        if ticker_code and not news:
            news = kis.get_news_title("", limit=news_limit)
            payload["news_note"] = "해당 종목으로 필터된 기사가 없어 시장 전체 속보를 반환합니다."
        payload["news"] = news
        payload["news_count"] = len(news)
    except Exception as e:
        errors["news"] = str(e)

    if ticker_code and include_opinion:
        try:
            opinions = kis.get_invest_opinion(ticker_code)
            payload["invest_opinion"] = {
                "count": len(opinions),
                "recent": opinions[:15],
                "note": "목표주가는 발표 시점 기준입니다. gap_pct는 당시 종가 대비 괴리율입니다.",
            }
        except Exception as e:
            errors["invest_opinion"] = str(e)

    if ticker_code and include_estimate:
        try:
            payload["estimate_perform"] = kis.get_estimate_perform(ticker_code)
        except Exception as e:
            errors["estimate_perform"] = str(e)

    if errors:
        payload["errors"] = errors
    return _dump(payload)


def main() -> None:
    print("market-data MCP: stdio 대기 중. (직접 실행 시 여기서 입력하지 말고 Ctrl+C로 종료)", file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
