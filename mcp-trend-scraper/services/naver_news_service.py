"""
Naver 뉴스 검색 유틸리티.

역할:
- 지정한 키워드 목록에 대해 네이버 뉴스 검색 API 결과를 가져온다.
- 기사 제목, 설명, 링크, 날짜 등 원본 정보를 최대한 그대로 넘긴다.

이 모듈은 호재/악재 판단이나 스코어링을 하지 않는다.
LLM이 기사 텍스트를 기반으로 스스로 해석할 수 있도록
필요한 원본 필드만 정리해서 반환한다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from config import NAVER_CLIENT_ID, NAVER_CLIENT_SECRET


NAVER_NEWS_SEARCH_URL = "https://openapi.naver.com/v1/search/news.json"


def _ensure_naver_credentials() -> None:
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        raise RuntimeError(
            "NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 이 설정되어 있지 않습니다 (.env 또는 환경 변수 확인)."
        )


def _parse_pubdate(pubdate: str | None) -> str | None:
    if not pubdate:
        return None
    try:
        # 예: 'Tue, 20 Feb 2026 14:32:00 +0900'
        dt = datetime.strptime(pubdate, "%a, %d %b %Y %H:%M:%S %z")
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return pubdate


def search_news_for_keyword(
    keyword: str,
    days_back: int = 1,
    display: int = 30,
) -> dict[str, Any]:
    """
    단일 키워드에 대해 네이버 뉴스 검색 결과를 가져온다.
    - days_back: 최근 N일 이내 기사만 필터링 (대략적인 시간 필터, pubDate 기반).
    - display: 최대 기사 수 (네이버 기본 최대 100, 여기서는 기본 30).
    """
    _ensure_naver_credentials()
    keyword = keyword.strip()
    if not keyword:
        return {
            "keyword": keyword,
            "articles": [],
        }

    params = {
        "query": f"{keyword} 주식",
        "display": str(display),
        "sort": "date",
    }

    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }

    with httpx.Client() as client:
        r = client.get(NAVER_NEWS_SEARCH_URL, params=params, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()

    items = data.get("items", []) or []

    # 날짜 필터링
    now = datetime.now(timezone.utc)
    threshold = now - timedelta(days=days_back)

    articles: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_pubdate = item.get("pubDate")
        iso_pubdate = _parse_pubdate(raw_pubdate)

        include = True
        if iso_pubdate:
            try:
                dt = datetime.fromisoformat(iso_pubdate.replace("Z", "+00:00"))
                if dt < threshold:
                    include = False
            except Exception:
                # 파싱 실패 시에는 필터링 없이 포함
                include = True

        if not include:
            continue

        # 네이버 응답에는 HTML 태그, 엔티티가 포함될 수 있으나
        # 여기서는 원본을 그대로 넘기고, LLM이 필요시 후처리하도록 둔다.
        articles.append(
            {
                "title": item.get("title"),
                "description": item.get("description"),
                "link": item.get("link"),
                "originallink": item.get("originallink"),
                "pubDate_raw": raw_pubdate,
                "pubDate_iso": iso_pubdate,
            }
        )

    return {
        "keyword": keyword,
        "articles": articles,
    }


def search_news_for_keywords(
    keywords: list[str],
    days_back: int = 1,
    display: int = 30,
) -> dict[str, Any]:
    """
    여러 키워드에 대해 뉴스 검색 결과를 한 번에 가져온다.
    - 키워드별로 별도 요청을 보내고, 결과를 keyword → articles 구조로 묶는다.
    """
    results: dict[str, Any] = {
        "keywords": [],
    }
    for k in keywords:
        k = k.strip()
        if not k:
            continue
        single = search_news_for_keyword(k, days_back=days_back, display=display)
        results["keywords"].append(single)
    return results

