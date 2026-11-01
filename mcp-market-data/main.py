"""
Market Data MCP 서버 (국내 퀀트/차트/수급 분석).

Tools:
  - get_kr_market_movers        : 거래량·등락률·체결강도·VI 순위 (후보 발굴)
  - get_current_price_and_chart : 현재가 + 이평/볼린저/ATR/52주/코스피 대비 상대강도 + 호가
  - get_institutional_buying    : 외인·기관 수급 종합 (확정치·가집계·창구·외국계·프로그램·공매도·융자)
  - get_kr_news_and_opinion     : 한투 속보 + 증권사 투자의견·목표주가 + 실적 컨센서스
  - get_kr_disclosures          : DART 공시 (성격 분류) + 임원·주요주주 소유보고 + 5% 대량보유
  - backtest_kr_rule            : 일봉 규칙 백테스트 (표본 내/외 분리, ATR 손절)

모든 도구는 수치와 원문만 반환한다. 호재/악재 판단은 LLM이 한다.
"""
import json
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from services import backtest
from services import dart_api as dart
from services import kis_api as kis
from services import krx_master
from services.indicators import analyze_technicals

mcp = FastMCP("market-data", json_response=True)

# 한투 투자자 매매 대금은 백만원 단위로 온다. 원으로 환산해서 내보낸다.
# (삼성전자 실측: raw -434,923 vs 수량 1,673,323주 x 260,000원 = -4,350억원)
INVESTOR_AMT_MULTIPLIER = 1_000_000


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
        "sector_name": (o.get("bstp_kor_isnm") or "").strip() or None,
        "market_warning": _parse_market_warning(o),
    }


# 한투 mrkt_warn_cls_code. 한국거래소 시장경보 단계다.
_MARKET_WARN_LABELS = {"00": None, "01": "투자주의", "02": "투자경고", "03": "투자위험"}


def _parse_market_warning(o: dict[str, Any]) -> dict[str, Any]:
    """시장경보·단기과열·거래정지 등 매매 제약 플래그. 판단은 하지 않는다."""
    code = (o.get("mrkt_warn_cls_code") or "").strip()
    flags = {
        "level_code": code or None,
        "level": _MARKET_WARN_LABELS.get(code, code or None),
        "investment_caution": o.get("invt_caful_yn") == "Y",
        "short_term_overheat": o.get("short_over_yn") == "Y",
        "trading_halted": o.get("temp_stop_yn") == "Y",
        "managed_issue": o.get("mang_issu_cls_code") == "Y",
        "liquidation_trading": o.get("sltr_yn") == "Y",
        "vi_active": o.get("vi_cls_code") not in (None, "", "N"),
        "short_sale_allowed": o.get("ssts_yn") == "Y" if o.get("ssts_yn") else None,
    }
    flags["any"] = bool(flags["level"]) or any(
        flags[k] for k in ("investment_caution", "short_term_overheat", "trading_halted",
                           "managed_issue", "liquidation_trading", "vi_active")
    )
    return flags


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
    period_days: int = 0,
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
      투자주의·경고·위험 같은 시장경보 종목은 켜둬도 포함됩니다(급등주에 흔함).
      경보 여부는 get_current_price_and_chart의 current.market_warning으로 확인하세요.
    - period_days: 등락률 누적일수 — 0:당일(기본), N:최근 N일 누적 (surge·plunge에만 적용)
      주말에 period_days=5로 조회하면 직전 주 누적 급등주 순위가 나옵니다.
      누적 조회 시 items의 change_pct는 여전히 전일 대비이고, 순위 자체가 누적 기준입니다.

    순위는 관심도·변동성 지표일 뿐 매수 신호가 아닙니다.
    당일(period_days=0) 등락률 순위는 한투 응답 순서가 등락률과 일치하지 않아
    서버에서 다시 정렬합니다. 누적 조회(period_days>0)는 한투 순서를 그대로 씁니다.
    """
    try:
        if category == "volume":
            items = kis.get_volume_rank(min_price=min_price, max_price=max_price,
                                        min_volume=min_volume, exclude_noise=exclude_noise)
            label = "거래량"
        elif category == "surge":
            items = kis.get_fluctuation_rank(rank_sort="0", min_volume=min_volume,
                                             exclude_noise=exclude_noise, days=period_days)
            label = "등락률 상위" if period_days == 0 else f"등락률 상위(최근 {period_days}일 누적)"
        elif category == "plunge":
            items = kis.get_fluctuation_rank(rank_sort="1", min_volume=min_volume,
                                             exclude_noise=exclude_noise, days=period_days)
            label = "등락률 하위" if period_days == 0 else f"등락률 하위(최근 {period_days}일 누적)"
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
    - include_relative_strength: 코스피·코스닥·소속 업종 지수 대비 초과수익률 계산 여부
      (지수 시세를 추가로 조회하므로 필요 없으면 False로 끄세요)

    이평선·볼린저·ATR은 chart_type과 무관하게 항상 일봉 기준입니다.
    상대강도가 양수면 지수보다 강했다는 뜻이며, 시장 상승에 묻어간 것인지
    종목 자체가 강한 것인지를 구분하는 데 씁니다.
    vs_index.SECTOR는 소속 업종 지수(예: 전기·전자) 대비입니다. 시장 대비는 강한데
    업종 대비는 약하면 업종 전체가 오른 것에 묻어간 것입니다.

    current.market_warning에는 시장경보(투자주의/경고/위험)·단기과열·거래정지·VI 같은
    매매 제약 플래그가 들어 있습니다. 투자경고 이상이면 신용·미수 제한과 단일가 매매가
    붙을 수 있으니 진입 전에 반드시 보세요.
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

    master: dict[str, Any] | None = None
    try:
        master = krx_master.lookup(ticker_code)
    except Exception as e:
        errors["krx_master"] = str(e)

    index_closes: dict[str, list[float]] = {}
    if include_relative_strength:
        targets = [("KOSPI", kis.INDEX_KOSPI), ("KOSDAQ", kis.INDEX_KOSDAQ)]
        if master and master["sector"]:
            targets.append(("SECTOR", master["sector"]["index_code"]))
        for name, code in targets:
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
        "ticker": {
            "code": ticker_code,
            "name": master["name"] if master else None,
            "market": master["market"] if master else None,
        },
        "sector": {
            "name": parsed["sector_name"],
            "index_code": master["sector"]["index_code"] if master and master["sector"] else None,
            "index_name": master["sector"]["name"] if master and master["sector"] else None,
            "note": "technicals.relative_strength.vs_index.SECTOR가 이 업종 지수 대비 초과수익률입니다.",
        },
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
            "market_warning": parsed["market_warning"],
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
            {"date": b["date"], "close": b["close"], "high": b.get("high"), "low": b.get("low"),
             "volume": b.get("volume")}
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


@mcp.tool()
def get_kr_disclosures(
    ticker_code: str = "",
    days: int = 30,
    categories: list[str] | None = None,
    include_insider: bool = True,
    limit: int = 50,
) -> str:
    """
    금융감독원 DART 공시를 조회합니다. 뉴스보다 먼저 나오는 재료의 원본입니다.

    - ticker_code: 종목코드 6자리. 비우면 시장 전체 최근 공시 (후보 발굴용)
    - days: 최근 며칠치 (기본 30). 시장 전체 조회는 1~3일을 권장
    - categories: 이 분류만 남김. 비우면 종목 조회는 전체, 시장 전체 조회는 other 제외
        risk               : 거래정지·불성실공시·관리종목·감사의견·소송·감자 등
        dilution           : 유상증자·CB·BW·EB·전환청구·추가상장 (주식 수가 늘어나는 공시)
        contract           : 단일판매·공급계약 (수주)
        earnings           : 잠정실적·손익구조 변경
        shareholder_return : 자기주식 취득·소각·배당·무상증자
        ownership          : 최대주주 변경·대량보유·임원 소유보고
        corporate_action   : 합병·분할·영업/자산 양수도
    - include_insider: 종목 조회 시 임원·주요주주 소유보고와 5% 대량보유 변동을 함께 반환 (최근 90일)
    - limit: 공시 최대 개수

    분류는 보고서 제목으로 붙인 공시 성격일 뿐 호재/악재 판단이 아닙니다.
    공급계약은 계약금액/최근 매출액 비율을, 유상증자·CB는 발행 규모와 전환가를
    url의 원문에서 확인한 뒤 판단하세요. 순위 급등 종목에 뉴스가 없을 때 이유를 찾는 데도 씁니다.
    DART_API_KEY(무료)가 .env에 있어야 합니다.
    """
    ticker_code = ticker_code.strip()
    payload: dict[str, Any] = {"ticker": {"code": ticker_code or None}, "days": days}
    errors: dict[str, str] = {}

    try:
        rows = dart.get_disclosures(ticker_code, days=days, max_pages=3 if ticker_code else 10)
    except Exception as e:
        return _err(f"공시 조회 실패: {e}", ticker_code=ticker_code or None)

    if categories:
        wanted = set(categories)
        rows = [r for r in rows if r["category"] in wanted]
    elif not ticker_code:
        rows = [r for r in rows if r["category"] != "other"]

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["category"]] = counts.get(r["category"], 0) + 1
    payload["category_counts"] = counts
    payload["disclosures"] = rows[:limit]
    payload["returned"] = len(rows[:limit])

    if ticker_code and include_insider:
        try:
            payload["insider_reports"] = {
                "rows": dart.get_insider_reports(ticker_code, days=90)[:20],
                "note": "임원·주요주주(10%↑) 특정증권 소유상황보고. shares_change 양수면 보유 증가입니다. "
                        "스톡옵션 행사·증여도 섞이니 원문의 변동 사유를 확인하세요.",
            }
        except Exception as e:
            errors["insider_reports"] = str(e)
        try:
            payload["major_holder_reports"] = {
                "rows": dart.get_major_holder_reports(ticker_code, days=90)[:20],
                "note": "5% 이상 대량보유 상황보고. reason에 보유 목적(경영참가/단순투자)이 들어 있습니다.",
            }
        except Exception as e:
            errors["major_holder_reports"] = str(e)

    payload["note"] = "분류는 제목 기반이며 호재/악재 판단이 아닙니다. 원문(url)을 확인하세요."
    if errors:
        payload["errors"] = errors
    return _dump(payload)


BACKTEST_MAX_TICKERS = 40


@mcp.tool()
def backtest_kr_rule(
    tickers: list[str],
    conditions: list[dict[str, Any]],
    years: float = 3,
    stop_atr_mult: float = 1.5,
    target_atr_mult: float | None = 3.0,
    max_hold_days: int = 5,
    cost_pct: float = 0.25,
    max_entry_gap_pct: float | None = None,
    oos_fraction: float = 0.3,
    cooldown_after_losses: int = 0,
    cooldown_days: int = 0,
) -> str:
    """
    가격·거래량 규칙을 과거 일봉에 돌려 승률·손익비·낙폭을 계산합니다 (국내, 수정주가).
    주간 튜닝에서 규칙 후보를 채택하기 전에 수년치로 먼저 검증하는 용도입니다.

    - tickers: 종목코드 목록 (최대 40개). 관심 테마 종목이나 최근 순위 상위 종목을 넣으세요
    - conditions: 신호 조건 목록. 모두 만족한 날(t일 종가 기준)의 다음 날 시가에 진입합니다
        [{"feature": "volume_ratio_avg20", "op": ">=", "value": 2},
         {"feature": "close_position", "op": ">=", "value": 0.7},
         {"feature": "pct_from_52w_high", "op": "between", "value": [-15, 0]}]
      op: > >= < <= == between
      feature: ret_1d, ret_5d, ret_20d, gap_pct, body_pct, close_position, volume_ratio_avg20,
               trade_value(원), close_vs_ma5/20/60, ma_aligned, pct_from_52w_high, atr_pct, rsi14,
               rs_5d, rs_20d (종목 소속 시장 지수 대비 %p)
    - years: 조회 기간 (기본 3년)
    - stop_atr_mult: 손절 = 진입가 - ATR14 x 배수 (기본 1.5)
    - target_atr_mult: 목표 = 진입가 + ATR14 x 배수 (기본 3.0, null이면 목표 없이 보유일 만료 청산)
    - max_hold_days: 최대 보유 거래일 (기본 5, 만료일 종가 청산)
    - cost_pct: 왕복 비용(%) — 세금·수수료·슬리피지 (기본 0.25)
    - max_entry_gap_pct: 다음 날 시가가 신호일 종가보다 이만큼(%) 넘게 갭상승하면 추격 안 함
    - oos_fraction: 최근 몇 %를 표본 외로 떼어 볼지 (기본 0.3)
    - cooldown_after_losses / cooldown_days: N연속 손실 후 D일(달력일) 신규 진입 중단

    수급(외인·기관 순매수) 이력은 한투 API로 수년치를 받을 수 없어 조건으로 못 씁니다.
    거래량·상대강도·종가 위치를 수급의 대리 지표로 쓰세요.
    같은 날 손절가·목표가를 모두 건드리면 손절로 칩니다(보수적). 생존 편향(상장폐지 종목 누락)이 있습니다.
    """
    tickers = [t.strip() for t in tickers if t and t.strip()]
    if not tickers:
        return _err("tickers가 비어 있습니다.")
    if len(tickers) > BACKTEST_MAX_TICKERS:
        return _err(f"tickers는 최대 {BACKTEST_MAX_TICKERS}개입니다 (받은 개수: {len(tickers)}).")
    problem = backtest.validate_conditions(conditions or [])
    if problem:
        return _err(problem, features=backtest.FEATURES)
    if not conditions:
        return _err("conditions가 비어 있습니다.", features=backtest.FEATURES)

    count = int(years * 250) + 70
    errors: dict[str, str] = {}
    data: dict[str, list[dict[str, Any]]] = {}
    markets: dict[str, str] = {}
    for t in tickers:
        try:
            data[t] = kis.get_daily_bars(t, count=count)
        except Exception as e:
            errors[t] = str(e)
            continue
        try:
            info = krx_master.lookup(t)
            markets[t] = info["market"] if info else "KOSPI"
        except Exception:
            markets[t] = "KOSPI"

    bench_maps: dict[str, dict[str, float]] = {}
    for name, code in (("KOSPI", kis.INDEX_KOSPI), ("KOSDAQ", kis.INDEX_KOSDAQ)):
        if name not in markets.values():
            continue
        try:
            bench_maps[name] = {b["date"]: b["close"] for b in kis.get_index_daily_bars(code, count=count)}
        except Exception as e:
            errors[f"index_{name}"] = str(e)

    result = backtest.run(
        data, {t: bench_maps.get(m, {}) for t, m in markets.items()}, conditions,
        stop_atr_mult=stop_atr_mult, target_atr_mult=target_atr_mult, max_hold_days=max_hold_days,
        cost_pct=cost_pct, max_entry_gap_pct=max_entry_gap_pct, oos_fraction=oos_fraction,
        cooldown_after_losses=cooldown_after_losses, cooldown_days=cooldown_days, price_digits=0,
    )
    result["config"] = {
        "tickers": tickers, "years": years, "conditions": conditions,
        "stop_atr_mult": stop_atr_mult, "target_atr_mult": target_atr_mult,
        "max_hold_days": max_hold_days, "cost_pct": cost_pct, "max_entry_gap_pct": max_entry_gap_pct,
        "entry": "신호 다음 날 시가", "benchmark": "종목 소속 시장 지수 (KOSPI/KOSDAQ)",
    }
    result["note"] = "과거 성과는 미래 수익을 보장하지 않습니다. 표본 외 구간과 baseline 대비를 같이 보세요."
    if errors:
        result["errors"] = errors
    return _dump(result)


def main() -> None:
    print("market-data MCP: stdio 대기 중. (직접 실행 시 여기서 입력하지 말고 Ctrl+C로 종료)", file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
