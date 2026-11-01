"""
Trend Scraper MCP 서버 (유튜브 + 뉴스).
"""
from __future__ import annotations

import json
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from config import YOUTUBE_CHANNEL_IDS
from services.youtube_service import (
    collect_youtube_videos,
    count_keyword_mentions,
    detect_stock_mentions,
)
from services.naver_news_service import search_news_for_keywords


mcp = FastMCP("trend-scraper", json_response=True)


@mcp.tool()
def search_youtube_keywords(
    keywords: list[str] | None = None,
    hours_back: int = 24,
    max_videos_per_channel: int = 10,
    include_transcript: bool = True,
    channel_ids: list[str] | None = None,
    auto_detect: bool = True,
    transcript_mode: str = "snippets",
    max_detected: int = 30,
) -> str:
    """
    지정한 유튜브 채널들에서 최근 N시간 이내 업로드된 영상들을 스캔해서
    어떤 종목이 언급됐는지 집계합니다.

    - keywords: 따로 세고 싶은 키워드 (테마명 등, 예: ["2차전지", "HBM"]). 비워도 됩니다
    - hours_back: 최근 N시간 이내 영상만 대상 (기본 24시간)
    - max_videos_per_channel: 채널당 최대 영상 개수 (기본 10개)
    - include_transcript: 자막까지 같이 수집할지 여부 (기본 True)
    - channel_ids: 명시하면 해당 채널 ID 목록만 사용.
                   None 또는 빈 리스트면 config.YOUTUBE_CHANNEL_IDS 사용.
    - auto_detect: 국내 상장 종목명 전체를 사전으로 자막에서 자동 검출 (기본 True)
      키워드를 미리 몰라도 유튜버가 처음 꺼낸 종목까지 잡힙니다.
      두 글자 종목명은 일반 단어와 겹쳐서 일부만 잡으니, 빠진 게 있으면 keywords로 넘기세요.
    - transcript_mode: snippets(기본) 언급 주변 문장만 / full 자막 전문 / none 자막 텍스트 생략
      전문은 영상당 수만 자라 토큰을 크게 씁니다. 논지 확인은 snippets로 충분합니다.
    - max_detected: detected_stocks 최대 종목 수

    반환:
      detected_stocks : 자동 검출 종목 (채널 수 → 영상 수 → 언급 수 순)
                        종목별 code, channels, videos[].snippets(언급 주변 원문)
      keyword_summary : keywords를 넘긴 경우 키워드별 언급 수와 snippets
      videos          : 영상 메타데이터 (transcript_mode=full일 때만 자막 전문 포함)
    여러 채널에서 동시에 언급된 종목일수록 관심이 넓게 퍼진 것입니다. 언급은 매수 신호가 아닙니다.
    """
    if transcript_mode not in ("snippets", "full", "none"):
        return json.dumps({"error": f"transcript_mode는 snippets/full/none 중 하나: {transcript_mode}"},
                          ensure_ascii=False)
    use_channels = channel_ids or YOUTUBE_CHANNEL_IDS
    videos = collect_youtube_videos(
        channel_ids=use_channels,
        hours_back=hours_back,
        max_videos_per_channel=max_videos_per_channel,
        include_transcript=include_transcript,
    )

    payload: dict[str, Any] = {
        "scan_config": {
            "hours_back": hours_back,
            "max_videos_per_channel": max_videos_per_channel,
            "include_transcript": include_transcript,
            "channel_ids": use_channels,
            "keywords": keywords or [],
            "auto_detect": auto_detect,
            "transcript_mode": transcript_mode,
        },
        "video_count": len(videos),
        "videos_with_transcript": sum(1 for v in videos if v.transcript_text),
    }
    failures: dict[str, int] = {}
    for v in videos:
        if v.transcript_error:
            failures[v.transcript_error] = failures.get(v.transcript_error, 0) + 1
    if failures:
        payload["transcript_errors"] = failures
        if failures.get("IpBlocked"):
            payload["transcript_note"] = (
                "YouTube가 이 PC의 IP를 일시 차단해 자막을 못 받았습니다(IpBlocked). "
                "보통 몇 시간 뒤 풀립니다. 그동안은 제목·설명에서만 종목을 검출합니다."
            )

    if auto_detect:
        try:
            payload["detected_stocks"] = detect_stock_mentions(videos, max_tickers=max_detected)
        except Exception as e:
            payload.setdefault("errors", {})["auto_detect"] = f"종목 마스터를 불러오지 못했습니다: {e}"
    if keywords:
        payload["keyword_summary"] = count_keyword_mentions(
            videos, keywords=keywords, with_snippets=transcript_mode != "none",
        )

    video_rows = []
    for v in videos:
        row = v.to_dict()
        text = row.pop("transcript_text")
        if not row.get("transcript_error"):
            row.pop("transcript_error", None)
        row["transcript_chars"] = len(text) if text else 0
        if transcript_mode == "full":
            row["transcript_text"] = text
        video_rows.append(row)
    payload["videos"] = video_rows
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

