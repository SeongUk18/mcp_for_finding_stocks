"""
Trend Scraper MCP 서버 (네이버 뉴스).
"""
from __future__ import annotations

import json
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from services.naver_news_service import search_news_for_keywords


mcp = FastMCP("trend-scraper", json_response=True)


@mcp.tool()
def search_news_for_trend_keywords(
    keywords: list[str],
    days_back: int = 1,
    max_articles_per_keyword: int = 30,
) -> str:
    """
    지정한 키워드(종목명/테마명 등)에 대해 네이버 뉴스 검색을 수행하고,
    최근 N일 이내 기사 목록을 키워드별로 반환합니다.

    - keywords: 관심 키워드 목록 (예: ["알테오젠", "에코프로"])
    - days_back: 최근 N일 이내 기사만 포함 (기본 1일)
    - max_articles_per_keyword: 키워드별 최대 기사 수 (기본 30개)

    이 도구 역시 호재/악재 판단을 하지 않으며,
    LLM이 참고할 수 있도록 기사 원문 메타데이터만 제공합니다.
    """
    news = search_news_for_keywords(
        keywords=keywords,
        days_back=days_back,
        display=max_articles_per_keyword,
    )
    payload: dict[str, Any] = {
        "search_config": {
            "keywords": keywords,
            "days_back": days_back,
            "max_articles_per_keyword": max_articles_per_keyword,
        },
        "results": news,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def main() -> None:
    print(
        "trend-scraper MCP: stdio 대기 중. (직접 실행 시 여기서 입력하지 말고 Ctrl+C로 종료)",
        file=sys.stderr,
    )
    mcp.run()


if __name__ == "__main__":
    main()
