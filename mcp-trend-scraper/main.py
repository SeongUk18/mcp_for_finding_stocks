"""
Trend Scraper MCP 서버 (유튜브 + 뉴스).
"""
from __future__ import annotations

import json
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from config import YOUTUBE_CHANNEL_IDS
from services.youtube_service import collect_youtube_videos, count_keyword_mentions
from services.naver_news_service import search_news_for_keywords


mcp = FastMCP("trend-scraper", json_response=True)


@mcp.tool()
def search_youtube_keywords(
    keywords: list[str],
    hours_back: int = 24,
    max_videos_per_channel: int = 10,
    include_transcript: bool = True,
    channel_ids: list[str] | None = None,
) -> str:
    """
    지정한 유튜브 채널들에서 최근 N시간 이내 업로드된 영상들을 스캔하고,
    주어진 키워드(종목명/테마명 등)가 제목/설명/자막에서 얼마나 자주 언급되는지 집계합니다.

    - keywords: 관심 있는 키워드 목록 (예: ["알테오젠", "에코프로", "한미반도체"])
    - hours_back: 최근 N시간 이내 영상만 대상 (기본 24시간)
    - max_videos_per_channel: 채널당 최대 영상 개수 (기본 10개)
    - include_transcript: 자막까지 같이 수집할지 여부 (기본 True)
    - channel_ids: 명시하면 해당 채널 ID 목록만 사용.
                   None 또는 빈 리스트면 config.YOUTUBE_CHANNEL_IDS 사용.

    반환 형식 (요약):
    {
      "scan_config": {...},
      "videos": [ {유튜브 메타데이터 + transcript_text}, ... ],
      "keyword_summary": {
        "keywords": [
          {
            "keyword": "알테오젠",
            "total_mentions": 12,
            "videos": [
              { "video_id": "...", "title": "...", "url": "...", "keyword_counts": {"알테오젠": 5} },
              ...
            ]
          },
          ...
        ]
      }
    }
    """
    use_channels = channel_ids or YOUTUBE_CHANNEL_IDS
    videos = collect_youtube_videos(
        channel_ids=use_channels,
        hours_back=hours_back,
        max_videos_per_channel=max_videos_per_channel,
        include_transcript=include_transcript,
    )

    keyword_summary = count_keyword_mentions(videos, keywords=keywords)

    payload: dict[str, Any] = {
        "scan_config": {
            "hours_back": hours_back,
            "max_videos_per_channel": max_videos_per_channel,
            "include_transcript": include_transcript,
            "channel_ids": use_channels,
            "keywords": keywords,
        },
        "videos": [v.to_dict() for v in videos],
        "keyword_summary": keyword_summary,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


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

