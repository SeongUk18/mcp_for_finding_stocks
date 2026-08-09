"""
US Market Data MCP 서버 (미국주식).

Tools:
  - search_serenity_tickers      : 세레니티(@aleabitoreddit) X 게시물에서 언급 종목 수집
  - get_us_price_and_chart       : 시세 + 기술 지표 + 상대강도
  - get_us_institutional_flow    : 기관 보유(13F) + 공매도 + 애널리스트 (한국 '수급'의 대체)
  - get_us_news_sentiment        : 최신 뉴스 원문 메타데이터

모든 도구는 수치와 원문만 반환한다. 호재/악재 판단은 LLM이 한다.
"""
from __future__ import annotations

import json
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from config import BENCHMARKS
from services import serenity_service as serenity
from services import yf_service as yfs
from services.indicators import analyze_technicals

mcp = FastMCP("us-market-data", json_response=True)


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _err(message: str, **extra: Any) -> str:
    return _dump({"error": message, **extra})


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
def get_us_price_and_chart(
    symbol: str,
    period: str = "1y",
    interval: str = "1d",
    include_candles: bool = False,
    candle_limit: int = 60,
) -> str:
    """
    미국주식의 현재가·고저·거래량과 이평선/볼린저/ATR/상대강도 등 원시 수치를 반환합니다.

    - symbol: 티커 (예: NVDA, AAPL, BRK-B)
    - period: 조회 기간 (1mo, 3mo, 6mo, 1y, 2y, 5y)
    - interval: 봉 단위 (1d, 1h, 30m, 15m, 5m — 분봉은 최근 60일까지만 가능)
    - include_candles: 개별 봉 데이터를 함께 반환할지 여부
    - candle_limit: include_candles=True일 때 반환할 최근 봉 개수

    상대강도(relative_strength)는 SPY/QQQ 대비 초과수익률입니다.
    미국은 일별 외국인·기관 순매수 공시가 없어서, 지수 대비 강도로
    자금 유입 강도를 간접 추정합니다. 양수면 지수보다 강하다는 뜻입니다.
    """
    symbol = symbol.strip().upper()
    if not symbol:
        return _err("symbol이 비어 있습니다.")

    try:
        hist = yfs.get_history(symbol, period=period, interval=interval)
    except Exception as e:
        return _err(f"{symbol} 시세 조회 실패: {e}", symbol=symbol)

    # 지표는 항상 1년치 일봉으로 계산한다.
    # 요청 기간을 그대로 쓰면 period="6mo"일 때 ma200이 계산되지 않는다.
    if interval == "1d" and len(hist["close"]) >= 252:
        daily = hist
    else:
        try:
            daily = yfs.get_history(symbol, period="1y", interval="1d")
        except Exception:
            daily = hist

    bench = yfs.get_benchmark_closes(BENCHMARKS, period="1y")
    technicals = analyze_technicals(
        closes=daily["close"],
        highs=daily["high"],
        lows=daily["low"],
        volumes=daily["volume"],
        benchmark_closes=bench,
    )

    quote = yfs.get_quote(symbol)
    if quote.get("price") is None and daily["close"]:
        quote["price"] = round(daily["close"][-1], 2)
        quote["_note"] = "실시간 시세를 못 받아 최근 종가로 대체함"

    payload = {
        "symbol": symbol,
        "profile": yfs.get_profile(symbol),
        "request": {"period": period, "interval": interval},
        "current": quote,
        "last_daily_bar": {
            "date": daily["dates"][-1] if daily["dates"] else None,
            "open": round(daily["open"][-1], 2) if daily["open"] else None,
            "high": round(daily["high"][-1], 2) if daily["high"] else None,
            "low": round(daily["low"][-1], 2) if daily["low"] else None,
            "close": round(daily["close"][-1], 2) if daily["close"] else None,
            "volume": daily["volume"][-1] if daily["volume"] else None,
        },
        "technicals": technicals,
        "data_points": len(daily["close"]),
        "currency": quote.get("currency", "USD"),
    }

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
    """
    symbol = symbol.strip().upper()
    if not symbol:
        return _err("symbol이 비어 있습니다.")

    info, info_err = yfs.get_info(symbol)

    rs: dict[str, Any] | None = None
    volume_block: dict[str, Any] | None = None
    rs_err: str | None = None
    try:
        daily = yfs.get_history(symbol, period="1y", interval="1d")
        bench = yfs.get_benchmark_closes(BENCHMARKS, period="1y")
        tech = analyze_technicals(
            closes=daily["close"],
            highs=daily["high"],
            lows=daily["low"],
            volumes=daily["volume"],
            benchmark_closes=bench,
        )
        rs = tech["relative_strength"]
        volume_block = {
            "latest_volume": daily["volume"][-1] if daily["volume"] else None,
            "avg_volume_20d": tech["volume_avg20"],
            "volume_ratio_vs_avg20": tech["volume_ratio_vs_avg20"],
            "volume_ratio_vs_prev_day": tech["volume_ratio_vs_prev_day"],
        }
    except Exception as e:
        rs_err = str(e)

    payload = {
        "symbol": symbol,
        "profile": yfs.get_profile(symbol, info=info),
        "ownership": yfs.get_ownership(symbol),
        "short_interest": yfs.get_short_interest(symbol, info=info),
        "relative_strength": rs,
        "volume": volume_block,
        "analyst": yfs.get_analyst_view(symbol, info=info),
        "data_freshness": {
            "ownership": "분기 (13F, 최대 45일 지연)",
            "short_interest": "월 2회 (1~2주 지연)",
            "relative_strength": "일별 (전 거래일 종가 기준)",
            "analyst": "이벤트 발생 시",
        },
    }
    errors = {k: v for k, v in {"info": info_err, "relative_strength": rs_err}.items() if v}
    if errors:
        payload["errors"] = errors
    return _dump(payload)


@mcp.tool()
def get_us_news_sentiment(symbol: str, limit: int = 15) -> str:
    """
    미국주식 관련 최신 뉴스 헤드라인과 요약을 반환합니다.

    - symbol: 티커 (예: NVDA)
    - limit: 최대 기사 수 (기본 15)

    이 도구는 호재/악재 점수를 매기지 않습니다.
    제목·요약·발행처·시각 원문을 넘기니 직접 읽고 재료를 판단하세요.
    실적 발표일(profile.earnings_date)이 임박했는지도 같이 확인하세요.
    """
    symbol = symbol.strip().upper()
    if not symbol:
        return _err("symbol이 비어 있습니다.")

    articles = yfs.get_news(symbol, limit=limit)
    payload = {
        "symbol": symbol,
        "profile": yfs.get_profile(symbol),
        "article_count": len(articles),
        "articles": articles,
        "note": "감성 점수 없음. 원문을 읽고 재료의 성격과 강도를 직접 판단하세요.",
    }
    if not articles:
        payload["note"] = "최근 뉴스를 찾지 못했습니다. 티커가 맞는지, 거래가 활발한 종목인지 확인하세요."
    return _dump(payload)


def main() -> None:
    print(
        "us-market-data MCP: stdio 대기 중. (직접 실행 시 여기서 입력하지 말고 Ctrl+C로 종료)",
        file=sys.stderr,
    )
    mcp.run()


if __name__ == "__main__":
    main()
