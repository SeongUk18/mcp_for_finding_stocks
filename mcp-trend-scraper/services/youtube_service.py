"""
YouTube 메타데이터/자막 수집 유틸리티.

역할:
- 지정한 채널 ID 목록에서 최근 N시간 이내 업로드된 영상을 가져온다.
- 제목, 설명, 기본 메타데이터를 수집한다.
- youtube-transcript-api를 사용해 자막(가능한 경우)을 가져온다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import httpx
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
from youtube_transcript_api.proxies import GenericProxyConfig, WebshareProxyConfig

from config import YOUTUBE_API_KEY



YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"


@dataclass
class YouTubeVideo:
    video_id: str
    channel_id: str
    channel_title: str | None
    title: str
    description: str
    published_at: str
    url: str
    view_count: int | None = None
    like_count: int | None = None
    transcript_text: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _iso8601_hours_back(hours_back: int) -> str:
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=hours_back)
    return start.isoformat().replace("+00:00", "Z")


def _fetch_recent_videos_for_channel(
    client: httpx.Client,
    channel_id: str,
    hours_back: int,
    max_results: int,
) -> list[dict[str, Any]]:
    if not YOUTUBE_API_KEY:
        raise RuntimeError("YOUTUBE_API_KEY 가 설정되어 있지 않습니다 (.env 또는 환경 변수 확인).")

    published_after = _iso8601_hours_back(hours_back)

    params = {
        "key": YOUTUBE_API_KEY,
        "channelId": channel_id,
        "part": "snippet",
        "order": "date",
        "type": "video",
        "maxResults": str(max_results),
        "publishedAfter": published_after,
    }
    r = client.get(f"{YOUTUBE_API_BASE}/search", params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    return data.get("items", []) or []


def _fetch_video_stats(
    client: httpx.Client,
    video_ids: Iterable[str],
) -> dict[str, dict[str, Any]]:
    """
    videos.list 를 사용해 viewCount, likeCount 등 통계를 한 번에 가져온다.
    """
    ids = list(video_ids)
    if not ids:
        return {}

    params = {
        "key": YOUTUBE_API_KEY,
        "id": ",".join(ids),
        "part": "statistics",
        "maxResults": str(len(ids)),
    }
    r = client.get(f"{YOUTUBE_API_BASE}/videos", params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    stats: dict[str, dict[str, Any]] = {}
    for item in data.get("items", []) or []:
        vid = item.get("id")
        stats[vid] = item.get("statistics", {}) or {}
    return stats


def _fetch_transcript_text(video_id: str) -> str | None:
    """
    youtube-transcript-api 를 사용해 자막을 텍스트로 가져온다.
    가능한 경우 한국어 우선, 없으면 영어 등 다른 언어도 허용.
    """
    preferred_languages = ["ko", "ko-KR", "en", "en-US"]

    try:
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id, languages=preferred_languages)
    except (TranscriptsDisabled, NoTranscriptFound):
        return None
    except Exception:
        # 네트워크, 레이트리밋, IpBlocked 등 다양한 예외가 발생할 수 있으므로
        # MCP 서버 전체에 영향을 주지 않도록 조용히 무시하고 None 반환.
        return None

    # 단순히 텍스트를 공백으로 이어 붙여 LLM이 자유롭게 분석하게 한다.
    return " ".join(getattr(snippet, "text", "") for snippet in fetched)


def collect_youtube_videos(
    channel_ids: list[str],
    hours_back: int = 24,
    max_videos_per_channel: int = 10,
    include_transcript: bool = True,
) -> list[YouTubeVideo]:
    """
    여러 채널에서 최근 N시간 이내 영상을 수집한다.
    """
    if not channel_ids:
        return []

    videos: list[YouTubeVideo] = []
    with httpx.Client() as client:
        for ch in channel_ids:
            search_items = _fetch_recent_videos_for_channel(
                client,
                channel_id=ch,
                hours_back=hours_back,
                max_results=max_videos_per_channel,
            )
            # 각 search 결과에서 videoId / snippet 추출
            channel_videos: list[YouTubeVideo] = []
            video_ids: list[str] = []
            for item in search_items:
                id_block = item.get("id") or {}
                video_id = id_block.get("videoId")
                if not video_id:
                    continue
                snippet = item.get("snippet") or {}
                title = snippet.get("title", "")
                description = snippet.get("description", "")
                published_at = snippet.get("publishedAt", "")
                channel_title = snippet.get("channelTitle")

                yt_video = YouTubeVideo(
                    video_id=video_id,
                    channel_id=ch,
                    channel_title=channel_title,
                    title=title,
                    description=description,
                    published_at=published_at,
                    url=f"https://www.youtube.com/watch?v={video_id}",
                )
                channel_videos.append(yt_video)
                video_ids.append(video_id)

            # 통계 정보(viewCount, likeCount) 붙이기
            stats = _fetch_video_stats(client, video_ids)
            for v in channel_videos:
                st = stats.get(v.video_id) or {}
                try:
                    v.view_count = int(st.get("viewCount")) if st.get("viewCount") is not None else None
                except (TypeError, ValueError):
                    v.view_count = None
                try:
                    v.like_count = int(st.get("likeCount")) if st.get("likeCount") is not None else None
                except (TypeError, ValueError):
                    v.like_count = None

                if include_transcript:
                    v.transcript_text = _fetch_transcript_text(v.video_id)

            videos.extend(channel_videos)

    return videos


def count_keyword_mentions(
    videos: Iterable[YouTubeVideo],
    keywords: list[str],
) -> dict[str, Any]:
    """
    키워드별로 제목/설명/자막에서 등장 횟수를 단순 카운트한다.
    - 키워드는 사용자가 넘겨준 문자열 그대로 사용 (예: '알테오젠', '삼성전자', '에코프로', '2차전지' 등).
    """
    normalized_keywords = [k for k in keywords if k]
    summary: dict[str, Any] = {
        "keywords": [],
    }

    # 키워드 전체 합계를 위한 초기화
    total_counts: dict[str, int] = {k: 0 for k in normalized_keywords}
    per_keyword_videos: dict[str, list[dict[str, Any]]] = {k: [] for k in normalized_keywords}

    for v in videos:
        base_text_parts = [v.title or "", v.description or ""]
        if v.transcript_text:
            base_text_parts.append(v.transcript_text)
        base_text = " ".join(base_text_parts)

        # 단순 포함 횟수 (대소문자 구분 없이)
        text_for_search = base_text.lower()

        video_keyword_counts: dict[str, int] = {}
        for k in normalized_keywords:
            k_lower = k.lower()
            if not k_lower:
                continue
            count = text_for_search.count(k_lower)
            if count > 0:
                video_keyword_counts[k] = count
                total_counts[k] = total_counts.get(k, 0) + count

        if video_keyword_counts:
            v_info = {
                "video_id": v.video_id,
                "url": v.url,
                "title": v.title,
                "channel_id": v.channel_id,
                "channel_title": v.channel_title,
                "published_at": v.published_at,
                "view_count": v.view_count,
                "like_count": v.like_count,
                "keyword_counts": video_keyword_counts,
            }
            for k, c in video_keyword_counts.items():
                per_keyword_videos.setdefault(k, []).append(v_info)

    for k in normalized_keywords:
        summary["keywords"].append(
            {
                "keyword": k,
                "total_mentions": total_counts.get(k, 0),
                "videos": per_keyword_videos.get(k, []),
            }
        )

    # 키워드별 total_mentions 내림차순 정렬
    summary["keywords"].sort(key=lambda x: x["total_mentions"], reverse=True)
    return summary

