"""
US Market Data MCP 서버 (미국주식).

Tools:
  - search_serenity_tickers      : 세레니티(@aleabitoreddit) X 게시물에서 언급 종목 수집
  - get_us_market_movers         : 한투 상승률·거래량·급등·시총 순위 (후보 발굴)
  - get_us_price_and_chart       : 실시간 시세 + 기술 지표 + 호가 + 분봉
  - get_us_institutional_flow    : 기관 보유(13F) + 공매도 + 애널리스트 (한국 '수급'의 대체)
  - get_us_news_sentiment        : 영문 + 한글 뉴스 원문 메타데이터
  - get_us_insider_trades        : SEC Form 4 내부자 장내 매수·매도 (2영업일 내 공시)
  - backtest_us_rule             : 일봉 규칙 백테스트 (표본 내/외 분리, ATR 손절)

시세는 한투 해외주식 API(무료실시간)를 우선 쓰고, 키가 없으면 yfinance로 폴백한다.
13F·공매도·애널리스트는 한투에 없으므로 항상 yfinance를 쓴다.
모든 도구는 수치와 원문만 반환한다. 호재/악재 판단은 LLM이 한다.
"""
from __future__ import annotations

import json
import math
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from config import BENCHMARKS, INDUSTRY_ETFS, SECTOR_ETFS, kis_enabled
from services import backtest
from services import kis_overseas as kis
from services import sec_insider as sec
from services import serenity_service as serenity
from services import yf_service as yfs
from services.indicators import analyze_technicals

mcp = FastMCP("us-market-data", json_response=True)


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _err(message: str, **extra: Any) -> str:
    return _dump({"error": message, **extra})


def _dash_date(ymd: str | None) -> str | None:
    """한투 YYYYMMDD를 yfinance와 같은 YYYY-MM-DD로."""
    if not ymd or len(ymd) != 8:
        return ymd
    return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"


def _merge_today_bar(daily: dict[str, list], kis_bars: list[dict[str, Any]]) -> tuple[dict[str, list], str | None]:
    """
    yfinance 일봉 뒤에 한투의 최신 봉을 이어 붙인다.

    yfinance 일봉은 당일 장중·프리마켓 데이터가 없어서, 그대로 지표를 계산하면
    이동평균과 상대강도가 하루 늦은 값이 된다. 한투가 주는 당일 봉을 얹어
    지표가 현재 세션을 반영하게 만든다.
    """
    if not kis_bars or not daily["dates"]:
        return daily, None
    last_yf = daily["dates"][-1]
    appended = None
    for bar in kis_bars:
        d = _dash_date(bar["date"])
        if not d or d <= last_yf:
            continue
        daily["dates"].append(d)
        daily["open"].append(bar["open"] or 0.0)
        daily["high"].append(bar["high"] or 0.0)
        daily["low"].append(bar["low"] or 0.0)
        daily["close"].append(bar["close"] or 0.0)
        daily["volume"].append(bar["volume"] or 0)
        appended = d
    return daily, appended


def _sector_benchmark(profile: dict[str, Any]) -> dict[str, Any] | None:
    """종목의 industry/sector에 대응하는 ETF. 대응이 없으면 None."""
    industry, sector = profile.get("industry"), profile.get("sector")
    if industry in INDUSTRY_ETFS:
        return {"etf": INDUSTRY_ETFS[industry], "basis": "industry", "matched": industry}
    if sector in SECTOR_ETFS:
        return {"etf": SECTOR_ETFS[sector], "basis": "sector", "matched": sector}
    return None


def _benchmarks_with_sector(profile: dict[str, Any]) -> tuple[dict[str, list[float]], dict[str, Any] | None]:
    """SPY/QQQ에 업종 ETF를 더한 벤치마크 종가. 종목 자신이 ETF면 중복이라 빼지 않는다."""
    sector = _sector_benchmark(profile)
    names = list(BENCHMARKS)
    if sector and sector["etf"] not in names:
        names.append(sector["etf"])
    bench = yfs.get_benchmark_closes(names, period="1y")
    if sector:
        sector["available"] = sector["etf"] in bench
    return bench, sector


@mcp.tool()
def search_serenity_tickers(
    hours_back: int = 168,
    min_mentions: int = 1,
    max_tickers: int = 25,
    max_sample_posts: int = 3,
    include_retweets: bool = False,
    force_refresh: bool = False,
) -> str:
    """
    세레니티(@aleabitoreddit)의 최근 X 게시물을 스캔해서 언급된 미국주식 티커를 집계합니다.
    한국 분석의 '유튜버 언급 스캔'에 대응하는 미국장 후보 발굴 도구입니다.

    세레니티는 AI 하드웨어·반도체 공급망 리서치로 알려진 계정이며,
    이 도구는 X API 대신 공개된 게시물 아카이브(CSV)를 받아 캐시해서 사용합니다.

    - hours_back: 최근 N시간 이내 게시물만 집계 (기본 168 = 7일)
    - min_mentions: 이 횟수 미만으로 언급된 티커는 제외
    - max_tickers: 반환할 최대 티커 수 (언급 횟수 → 조회수 순 정렬)
    - max_sample_posts: 티커별로 붙일 대표 게시물 수 (조회수 상위)
    - include_retweets: 리트윗 포함 여부 (기본 False, 본인 글만)
    - force_refresh: 캐시를 무시하고 아카이브를 새로 받을지 여부

    반환값에는 티커별 언급 횟수, 조회수·좋아요 합계, 최초/최종 언급 시각,
    함께 언급된 티커(테마 묶음), 그리고 원문 게시물 발췌가 들어갑니다.
    호재/악재 판단은 하지 않으니 원문을 읽고 직접 해석하세요.
    """
    try:
        posts, archive_info = serenity.load_posts(
            hours_back=hours_back,
            include_retweets=include_retweets,
            force_refresh=force_refresh,
        )
    except Exception as e:
        return _err(
            f"세레니티 아카이브를 가져오지 못했습니다: {e}",
            hint="네트워크 또는 아카이브 저장소 문제일 수 있습니다. force_refresh=True로 재시도해 보세요.",
        )

    tickers = serenity.aggregate_tickers(
        posts,
        min_mentions=min_mentions,
        max_sample_posts=max_sample_posts,
    )

    payload = {
        "scan_config": {
            "source": "@aleabitoreddit (Serenity) X 공개 아카이브",
            "hours_back": hours_back,
            "min_mentions": min_mentions,
            "include_retweets": include_retweets,
        },
        "archive": archive_info,
        "ticker_count": len(tickers),
        "tickers": tickers[:max_tickers],
        "note": (
            "언급 횟수는 관심도 지표일 뿐 매수 신호가 아닙니다. "
            "아카이브는 주기적 동기화라 최근 몇 시간 게시물은 빠져 있을 수 있습니다."
        ),
    }
    if not tickers:
        payload["note"] = (
            f"최근 {hours_back}시간 내 캐시태그 언급이 없습니다. "
            "hours_back을 늘리거나 아카이브 최신성(archive.newest_post_utc)을 확인하세요."
        )
    return _dump(payload)


@mcp.tool()
def get_us_market_movers(
    category: str = "volume",
    exchange: str = "NAS",
    limit: int = 30,
    min_volume: str = "0",
    period: str = "0",
) -> str:
    """
    한국투자증권 해외주식 순위 API로 오늘 움직인 종목을 훑습니다.
    세레니티 스캔과 별개인 두 번째 후보 발굴 채널입니다.

    - category: 조회 종류
        volume     : 거래량 순위 (기본)
        surge      : 상승률 순위
        plunge     : 하락률 순위
        fluct_up   : 가격 급등
        fluct_down : 가격 급락
        market_cap : 시가총액 순위
    - exchange: NAS(나스닥) / NYS(뉴욕) / AMS(아멕스)
    - limit: 반환할 최대 종목 수
    - min_volume: 거래량 필터 0:전체 1:1000주↑ 2:10만주↑ 3:100만주↑ 4:1000만주↑
    - period: 기준 기간 0:당일 1:2일 2:3일 3:5일 4:10일 5:20일 6:30일 7:60일 8:120일 9:1년
              (volume·surge·plunge에만 적용)

    응답의 data_status가 "무료실시간"이면 지연 없는 실시간 데이터입니다.
    이 도구는 순위만 알려줄 뿐 매수 근거가 아닙니다. 급등 상위에는 저가주와
    소형주가 많이 섞이므로 min_volume으로 걸러서 보세요.
    """
    if not kis_enabled():
        return _err(
            "한투 API 키가 없어 순위 조회를 쓸 수 없습니다.",
            hint="mcp-us-market-data/.env 또는 mcp-market-data/.env에 KIS_APPKEY/KIS_APPSECRET을 설정하세요.",
        )
    try:
        data = kis.get_ranking(
            category=category,
            excd=exchange.upper(),
            limit=limit,
            vol_rang=min_volume,
            nday=period,
        )
    except Exception as e:
        return _err(f"순위 조회 실패: {e}", category=category, exchange=exchange)

    data["note"] = (
        "순위는 관심도·변동성 지표일 뿐 매수 신호가 아닙니다. "
        "급등 상위에는 저가주·소형주가 섞이니 min_volume으로 걸러 보세요."
    )
    return _dump(data)


@mcp.tool()
def get_us_price_and_chart(
    symbol: str,
    period: str = "1y",
    interval: str = "1d",
    include_candles: bool = False,
    candle_limit: int = 60,
    include_orderbook: bool = False,
    include_intraday: bool = False,
    intraday_minutes: int = 5,
) -> str:
    """
    미국주식의 현재가·고저·거래량과 이평선/볼린저/ATR/상대강도 등 원시 수치를 반환합니다.

    - symbol: 티커 (예: NVDA, AAPL)
    - period: 조회 기간 (1mo, 3mo, 6mo, 1y, 2y, 5y)
    - interval: 봉 단위 (1d, 1h, 30m, 15m, 5m)
    - include_candles: 개별 봉 데이터를 함께 반환할지 여부
    - candle_limit: include_candles=True일 때 반환할 최근 봉 개수
    - include_orderbook: 호가와 잔량을 함께 반환할지 (한투 키 필요)
    - include_intraday: 분봉을 함께 반환할지 — 프리마켓·애프터마켓 포함 (한투 키 필요)
    - intraday_minutes: 분봉 단위 (1, 5, 10, 15, 30, 60)

    현재가는 한투 해외주식 API(무료실시간)를 우선 사용하고, 키가 없으면
    yfinance로 폴백합니다. 어느 쪽을 썼는지는 price_source에 표시됩니다.
    yfinance 일봉에는 당일 데이터가 없어서, 한투가 주는 당일 봉을 이어 붙인 뒤
    이동평균과 상대강도를 계산합니다.

    상대강도(relative_strength)는 SPY/QQQ 대비 초과수익률입니다.
    미국은 일별 외국인·기관 순매수 공시가 없어서, 지수 대비 강도로
    자금 유입 강도를 간접 추정합니다. 양수면 지수보다 강하다는 뜻입니다.
    vs_benchmark에는 업종 ETF(예: 반도체는 SMH)도 들어가며 어떤 ETF인지는
    sector_benchmark에 있습니다. 지수 대비는 강한데 업종 대비 약하면 업종 전체에 묻어간 것입니다.
    """
    symbol = symbol.strip().upper()
    if not symbol:
        return _err("symbol이 비어 있습니다.")

    errors: dict[str, str] = {}

    try:
        hist = yfs.get_history(symbol, period=period, interval=interval)
    except Exception as e:
        return _err(f"{symbol} 시세 조회 실패: {e}", symbol=symbol)

    # 지표는 항상 1년치 일봉으로 계산한다.
    # 요청 기간을 그대로 쓰면 period="6mo"일 때 ma200이 계산되지 않는다.
    if interval == "1d" and len(hist["close"]) >= 252:
        daily = dict(hist)
    else:
        try:
            daily = yfs.get_history(symbol, period="1y", interval="1d")
        except Exception:
            daily = dict(hist)
    daily = {k: list(v) for k, v in daily.items()}

    quote: dict[str, Any] = {}
    price_source = "yfinance"
    excd = None
    appended_date = None
    orderbook = None
    intraday = None

    if kis_enabled():
        try:
            excd = kis.resolve_exchange(symbol)
            quote = kis.get_quote(symbol, excd=excd)
            price_source = "kis_realtime"
            try:
                daily, appended_date = _merge_today_bar(daily, kis.get_daily(symbol, excd=excd))
            except Exception as e:
                errors["kis_daily"] = str(e)
        except Exception as e:
            errors["kis_quote"] = str(e)

    if not quote:
        quote = yfs.get_quote(symbol)
        price_source = "yfinance"
    if quote.get("price") is None and daily["close"]:
        quote["price"] = round(daily["close"][-1], 2)
        quote["_note"] = "실시간 시세를 못 받아 최근 종가로 대체함"

    profile = yfs.get_profile(symbol)
    bench, sector_bench = _benchmarks_with_sector(profile)
    technicals = analyze_technicals(
        closes=daily["close"],
        highs=daily["high"],
        lows=daily["low"],
        volumes=daily["volume"],
        benchmark_closes=bench,
        last_bar_partial=appended_date is not None,
    )

    if include_orderbook:
        if kis_enabled():
            try:
                orderbook = kis.get_orderbook(symbol, excd=excd)
            except Exception as e:
                errors["orderbook"] = str(e)
        else:
            errors["orderbook"] = "한투 API 키가 없어 호가를 조회할 수 없습니다."

    if include_intraday:
        if kis_enabled():
            try:
                intraday = kis.get_minute_bars(symbol, excd=excd, nmin=str(intraday_minutes))
            except Exception as e:
                errors["intraday"] = str(e)
        else:
            errors["intraday"] = "한투 API 키가 없어 분봉을 조회할 수 없습니다."

    payload: dict[str, Any] = {
        "symbol": symbol,
        "profile": profile,
        "sector_benchmark": sector_bench,
        "request": {"period": period, "interval": interval},
        "price_source": price_source,
        "current": quote,
        "last_daily_bar": {
            "date": daily["dates"][-1] if daily["dates"] else None,
            "open": round(daily["open"][-1], 2) if daily["open"] else None,
            "high": round(daily["high"][-1], 2) if daily["high"] else None,
            "low": round(daily["low"][-1], 2) if daily["low"] else None,
            "close": round(daily["close"][-1], 2) if daily["close"] else None,
            "volume": daily["volume"][-1] if daily["volume"] else None,
            "from_kis_today": appended_date is not None,
        },
        "technicals": technicals,
        "recent_daily": [
            {"date": d, "close": round(c, 2), "volume": v}
            for d, c, v in list(zip(daily["dates"], daily["close"],
                                    daily.get("volume") or [None] * len(daily["close"])))[-15:]
        ],
        "data_points": len(daily["close"]),
        "currency": quote.get("currency", "USD"),
    }
    if orderbook:
        payload["orderbook"] = orderbook
    if intraday:
        payload["intraday"] = intraday
    if include_candles:
        n = max(1, candle_limit)
        payload["candles"] = [
            {
                "date": hist["dates"][i],
                "open": round(hist["open"][i], 2),
                "high": round(hist["high"][i], 2),
                "low": round(hist["low"][i], 2),
                "close": round(hist["close"][i], 2),
                "volume": hist["volume"][i],
            }
            for i in range(max(0, len(hist["dates"]) - n), len(hist["dates"]))
        ]
    if errors:
        payload["errors"] = errors
    return _dump(payload)


@mcp.tool()
def get_us_institutional_flow(symbol: str) -> str:
    """
    미국주식의 '큰손 수급'을 확인합니다. 한국의 외국인/기관 순매수 조회를 대체하는 도구입니다.

    미국 시장에는 일별 외국인·기관 순매수 공시가 존재하지 않습니다.
    대신 아래 네 가지를 묶어서 반환합니다:

      1. ownership       — 기관 보유비율과 주요 기관별 보유 수량 (13F, 분기 단위·최대 45일 지연)
      2. short_interest  — 공매도 잔고, 유동주식 대비 비율, Days to Cover (월 2회 보고·지연 있음)
      3. relative_strength — SPY/QQQ 대비 초과수익률과 거래량 급증 배율 (매일 갱신, 유일한 실시간성 지표)
      4. analyst         — 등급 분포, 최근 상향/하향 조정, 목표주가 (이벤트성)

    - symbol: 티커 (예: NVDA)

    주의: 1·2번은 지연 데이터라 '오늘 누가 샀는지'를 알려주지 않습니다.
    단타 타이밍 판단에는 3번을, 중기 보유 주체 확인에는 1번을 쓰세요.
    한투 해외주식 API에는 13F·공매도·애널리스트 데이터가 없어 이 도구는 yfinance만 씁니다.
    """
    symbol = symbol.strip().upper()
    if not symbol:
        return _err("symbol이 비어 있습니다.")

    info, info_err = yfs.get_info(symbol)
    profile = yfs.get_profile(symbol, info=info)
    sector_bench: dict[str, Any] | None = None

    rs: dict[str, Any] | None = None
    volume_block: dict[str, Any] | None = None
    rs_err: str | None = None
    try:
        daily = yfs.get_history(symbol, period="1y", interval="1d")
        daily = {k: list(v) for k, v in daily.items()}
        appended = None
        if kis_enabled():
            try:
                daily, appended = _merge_today_bar(daily, kis.get_daily(symbol))
            except Exception:
                pass
        bench, sector_bench = _benchmarks_with_sector(profile)
        tech = analyze_technicals(
            closes=daily["close"],
            highs=daily["high"],
            lows=daily["low"],
            volumes=daily["volume"],
            benchmark_closes=bench,
            last_bar_partial=appended is not None,
        )
        rs = tech["relative_strength"]
        volume_block = {
            "avg_volume_20d": tech["volume_avg20"],
            "volume_ratio_vs_avg20": tech["volume_ratio_vs_avg20"],
            "volume_ratio_vs_prev_day": tech["volume_ratio_vs_prev_day"],
            "basis": tech["volume_basis"],
        }
        if tech.get("partial_session_volume"):
            volume_block["current_session"] = tech["partial_session_volume"]
    except Exception as e:
        rs_err = str(e)

    payload = {
        "symbol": symbol,
        "profile": profile,
        "ownership": yfs.get_ownership(symbol),
        "short_interest": yfs.get_short_interest(symbol, info=info),
        "relative_strength": rs,
        "sector_benchmark": sector_bench,
        "volume": volume_block,
        "analyst": yfs.get_analyst_view(symbol, info=info),
        "data_freshness": {
            "ownership": "분기 (13F, 최대 45일 지연)",
            "short_interest": "월 2회 (1~2주 지연)",
            "relative_strength": "일별 (한투 키가 있으면 당일 세션 반영)",
            "analyst": "이벤트 발생 시",
        },
    }
    errors = {k: v for k, v in {"info": info_err, "relative_strength": rs_err}.items() if v}
    if errors:
        payload["errors"] = errors
    return _dump(payload)


@mcp.tool()
def get_us_news_sentiment(symbol: str, limit: int = 15, include_korean: bool = True) -> str:
    """
    미국주식 관련 최신 뉴스 헤드라인과 요약을 반환합니다.

    - symbol: 티커 (예: NVDA)
    - limit: 최대 기사 수 (기본 15)
    - include_korean: 한투 해외뉴스(한글 제목)를 함께 가져올지 (한투 키 필요)

    articles는 영문 기사이며 is_primary_subject가 true인 기사가 해당 종목이 주제인 기사입니다.
    korean_news는 한글 속보 제목이라 재료 성격을 빠르게 훑는 데 유용합니다.

    이 도구는 호재/악재 점수를 매기지 않습니다.
    차기 실적 발표일(profile.earnings_date_next)이 임박했는지도 같이 확인하세요.
    profile.earnings_date_prev는 이미 지난 직전 실적일이라 임박 판단에 쓰면 안 됩니다.
    """
    symbol = symbol.strip().upper()
    if not symbol:
        return _err("symbol이 비어 있습니다.")

    articles = yfs.get_news(symbol, limit=limit)
    payload: dict[str, Any] = {
        "symbol": symbol,
        "profile": yfs.get_profile(symbol),
        "article_count": len(articles),
        "articles": articles,
        "note": "감성 점수 없음. 원문을 읽고 재료의 성격과 강도를 직접 판단하세요.",
    }

    if include_korean and kis_enabled():
        try:
            ko = kis.get_news(symbol=symbol, limit=limit)
            if not ko:
                ko = kis.get_news(symbol="", limit=limit)
                payload["korean_news_note"] = "해당 티커로 필터된 한글 기사가 없어 해외 시장 전체 속보를 반환합니다."
            payload["korean_news"] = ko
        except Exception as e:
            payload.setdefault("errors", {})["korean_news"] = str(e)

    if not articles:
        payload["note"] = "최근 영문 뉴스를 찾지 못했습니다. 티커가 맞는지 확인하세요."
    return _dump(payload)


@mcp.tool()
def get_us_insider_trades(symbol: str, days: int = 90, max_filings: int = 40, limit: int = 30) -> str:
    """
    SEC Form 4(임원·이사·10% 주주의 자사주 거래)를 원본에서 읽어 반환합니다.

    - symbol: 티커 (예: NVDA)
    - days: 최근 며칠치 제출분 (기본 90)
    - max_filings: 읽을 Form 4 최대 개수 (하나당 SEC 요청 1회)
    - limit: transactions 최대 개수

    13F(분기, 최대 45일 지연)와 달리 Form 4는 거래 후 2영업일 안에 제출됩니다.
    읽는 법:
      - code P(장내 매수)가 가장 의미 있는 신호입니다. 여러 내부자가 짧은 기간에 같이 사면(클러스터 매수) 더 강합니다.
      - code S(장내 매도)는 세금·분산 목적이 많아 약한 신호입니다. is_10b5_1_plan=true면 사전 계획 매도라 타이밍 정보가 거의 없습니다.
      - A(그랜트)·M(옵션 행사)·F(세금 원천징수)·G(증여)는 매매 의사와 무관합니다.
    summary.open_market_sells_outside_plan은 10b5-1 표시가 없는 매도입니다(구형 서식은 표시 자체가 없어 여기에 섞입니다).
    """
    symbol = symbol.strip().upper()
    if not symbol:
        return _err("symbol이 비어 있습니다.")
    try:
        data = sec.get_insider_transactions(symbol, days=days, max_filings=max_filings)
    except Exception as e:
        return _err(f"{symbol} 내부자 거래 조회 실패: {e}", symbol=symbol)
    data["transactions"] = data["transactions"][:limit]
    data["data_freshness"] = "거래 후 2영업일 내 제출 (SEC EDGAR 원본)"
    return _dump(data)


BACKTEST_MAX_TICKERS = 40


@mcp.tool()
def backtest_us_rule(
    symbols: list[str],
    conditions: list[dict[str, Any]],
    years: float = 3,
    stop_atr_mult: float = 1.5,
    target_atr_mult: float | None = 3.0,
    max_hold_days: int = 5,
    cost_pct: float = 0.1,
    max_entry_gap_pct: float | None = None,
    oos_fraction: float = 0.3,
    cooldown_after_losses: int = 0,
    cooldown_days: int = 0,
    benchmark: str = "SPY",
) -> str:
    """
    가격·거래량 규칙을 과거 일봉(yfinance, 분할 반영)에 돌려 승률·손익비·낙폭을 계산합니다 (미국).
    주간 튜닝에서 규칙 후보를 채택하기 전에 수년치로 먼저 검증하는 용도입니다.

    - symbols: 티커 목록 (최대 40개)
    - conditions: 신호 조건 목록. 모두 만족한 날(t일 종가 기준)의 다음 날 시가에 진입합니다
        [{"feature": "volume_ratio_avg20", "op": ">=", "value": 1.5},
         {"feature": "rs_20d", "op": ">", "value": 0}]
      op: > >= < <= == between
      feature: ret_1d, ret_5d, ret_20d, gap_pct, body_pct, close_position, volume_ratio_avg20,
               trade_value($), close_vs_ma5/20/60, ma_aligned, pct_from_52w_high, atr_pct, rsi14,
               rs_5d, rs_20d (benchmark 대비 %p)
    - years: 조회 기간 (기본 3년, 최대 10)
    - stop_atr_mult / target_atr_mult: 손절·목표 = 진입가 ∓ ATR14 x 배수 (target null이면 목표 없음)
    - max_hold_days: 최대 보유 거래일 (기본 5, 만료일 종가 청산)
    - cost_pct: 왕복 비용(%) (기본 0.1)
    - max_entry_gap_pct: 다음 날 시가 갭상승이 이 %를 넘으면 추격 안 함
    - oos_fraction: 최근 몇 %를 표본 외로 떼어 볼지 (기본 0.3)
    - cooldown_after_losses / cooldown_days: N연속 손실 후 D일(달력일) 신규 진입 중단
    - benchmark: rs 계산 기준 (기본 SPY, 반도체 테마면 SMH 등)

    프리마켓 갭은 일봉 시가에만 반영됩니다. 같은 날 손절가·목표가를 모두 건드리면 손절로 칩니다.
    생존 편향(상장폐지 종목 누락)이 있습니다.
    """
    symbols = [s.strip().upper() for s in symbols if s and s.strip()]
    if not symbols:
        return _err("symbols가 비어 있습니다.")
    if len(symbols) > BACKTEST_MAX_TICKERS:
        return _err(f"symbols는 최대 {BACKTEST_MAX_TICKERS}개입니다 (받은 개수: {len(symbols)}).")
    if not conditions:
        return _err("conditions가 비어 있습니다.", features=backtest.FEATURES)
    problem = backtest.validate_conditions(conditions)
    if problem:
        return _err(problem, features=backtest.FEATURES)

    period = f"{min(10, max(1, math.ceil(years)))}y"
    errors: dict[str, str] = {}

    def _bars(sym: str) -> list[dict[str, Any]]:
        h = yfs.get_history(sym, period=period, interval="1d")
        return [
            {"date": d, "open": o, "high": hi, "low": lo, "close": c, "volume": v}
            for d, o, hi, lo, c, v in zip(h["dates"], h["open"], h["high"], h["low"], h["close"], h["volume"])
        ]

    try:
        bench = {b["date"]: b["close"] for b in _bars(benchmark.upper())}
    except Exception as e:
        bench = {}
        errors[f"benchmark_{benchmark}"] = str(e)

    data: dict[str, list[dict[str, Any]]] = {}
    for s in symbols:
        try:
            data[s] = _bars(s)
        except Exception as e:
            errors[s] = str(e)

    result = backtest.run(
        data, {s: bench for s in data}, conditions,
        stop_atr_mult=stop_atr_mult, target_atr_mult=target_atr_mult, max_hold_days=max_hold_days,
        cost_pct=cost_pct, max_entry_gap_pct=max_entry_gap_pct, oos_fraction=oos_fraction,
        cooldown_after_losses=cooldown_after_losses, cooldown_days=cooldown_days, price_digits=2,
    )
    result["config"] = {
        "symbols": symbols, "period": period, "conditions": conditions,
        "stop_atr_mult": stop_atr_mult, "target_atr_mult": target_atr_mult,
        "max_hold_days": max_hold_days, "cost_pct": cost_pct, "max_entry_gap_pct": max_entry_gap_pct,
        "entry": "신호 다음 날 시가", "benchmark": benchmark.upper(),
    }
    result["note"] = "과거 성과는 미래 수익을 보장하지 않습니다. 표본 외 구간과 baseline 대비를 같이 보세요."
    if errors:
        result["errors"] = errors
    return _dump(result)


def main() -> None:
    mode = "한투 실시간 + yfinance" if kis_enabled() else "yfinance 단독 (한투 키 없음)"
    print(
        f"us-market-data MCP: stdio 대기 중. 시세 소스: {mode}",
        file=sys.stderr,
    )
    mcp.run()


if __name__ == "__main__":
    main()
