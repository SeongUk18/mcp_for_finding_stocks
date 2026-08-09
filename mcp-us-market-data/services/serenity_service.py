"""
세레니티(@aleabitoreddit) X 게시물 아카이브 수집.

X API는 유료라 쓰지 않는다. 대신 yan-labs/serenity-aleabitoreddit 저장소가
자동 갱신하는 공개 CSV 아카이브를 받아서 캐시하고, 캐시태그($NVDA)를 집계한다.

이 모듈은 호재/악재 판단을 하지 않는다. 원문과 집계 수치만 제공하고
해석은 LLM에게 맡긴다.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from config import (
    CACHE_DIR,
    CASHTAG_STOPLIST,
    SERENITY_CACHE_TTL_SEC,
    SERENITY_CSV_URL,
    SERENITY_SYNC_STATE_URL,
)

# 노트 트윗(장문)이 들어있어 기본 필드 제한(131072)을 넘길 수 있다.
csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

_CACHE_CSV = os.path.join(CACHE_DIR, "serenity_tweets.csv")
_CACHE_META = os.path.join(CACHE_DIR, "serenity_meta.json")

# $NVDA, $BRK.B 형태. 앞에 영숫자가 붙은 경우(예: US$5)는 제외.
_CASHTAG_RE = re.compile(r"(?<![A-Za-z0-9])\$([A-Z]{1,5})(\.[A-Z])?\b")


def _read_meta() -> dict[str, Any]:
    try:
        with open(_CACHE_META, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_meta(meta: dict[str, Any]) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_CACHE_META, "w", encoding="utf-8") as f:
        json.dump(meta, f)


def _fetch_archive(force_refresh: bool = False) -> tuple[str, dict[str, Any]]:
    """
    아카이브 CSV 본문과 캐시 상태를 반환.

    TTL 안이면 로컬 캐시를 그대로 쓰고, TTL이 지나면 ETag 조건부 요청으로
    변경 여부만 확인한다(304면 재다운로드 없음).
    """
    meta = _read_meta()
    now = time.time()
    cached_at = float(meta.get("cached_at") or 0)
    has_cache = os.path.exists(_CACHE_CSV)

    if has_cache and not force_refresh and (now - cached_at) < SERENITY_CACHE_TTL_SEC:
        with open(_CACHE_CSV, "r", encoding="utf-8", newline="") as f:
            return f.read(), {
                "source": "local_cache",
                "cached_at_utc": _iso(cached_at),
                "age_sec": int(now - cached_at),
            }

    headers: dict[str, str] = {}
    etag = meta.get("etag")
    if etag and has_cache and not force_refresh:
        headers["If-None-Match"] = etag

    try:
        with httpx.Client(follow_redirects=True) as client:
            r = client.get(SERENITY_CSV_URL, headers=headers, timeout=60)
    except httpx.HTTPError as e:
        if has_cache:
            with open(_CACHE_CSV, "r", encoding="utf-8", newline="") as f:
                return f.read(), {
                    "source": "stale_cache_after_network_error",
                    "cached_at_utc": _iso(cached_at),
                    "error": str(e),
                }
        raise RuntimeError(f"세레니티 아카이브 다운로드 실패: {e}") from e

    if r.status_code == 304 and has_cache:
        meta["cached_at"] = now
        _write_meta(meta)
        with open(_CACHE_CSV, "r", encoding="utf-8", newline="") as f:
            return f.read(), {
                "source": "local_cache_revalidated_304",
                "cached_at_utc": _iso(now),
            }

    r.raise_for_status()
    text = r.text
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_CACHE_CSV, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    _write_meta({"cached_at": now, "etag": r.headers.get("ETag")})
    return text, {
        "source": "remote_download",
        "cached_at_utc": _iso(now),
        "bytes": len(text.encode("utf-8")),
    }


def _iso(ts: float) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _to_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def extract_cashtags(text: str) -> list[str]:
    """본문에서 캐시태그를 중복 없이 추출. 통화 토큰은 제외."""
    out: list[str] = []
    for base, suffix in _CASHTAG_RE.findall(text or ""):
        if base in CASHTAG_STOPLIST:
            continue
        sym = f"{base}{suffix}" if suffix else base
        if sym not in out:
            out.append(sym)
    return out


def load_posts(
    hours_back: int = 168,
    include_retweets: bool = False,
    force_refresh: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """최근 N시간 게시물과 아카이브 메타데이터를 반환."""
    text, cache_info = _fetch_archive(force_refresh=force_refresh)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)

    posts: list[dict[str, Any]] = []
    newest: datetime | None = None
    total_rows = 0

    for row in csv.DictReader(io.StringIO(text)):
        total_rows += 1
        created = _parse_dt(row.get("createdAtISO", ""))
        if created is None:
            continue
        if newest is None or created > newest:
            newest = created
        if created < cutoff:
            continue
        is_rt = str(row.get("isRetweet", "")).strip().lower() == "true"
        if is_rt and not include_retweets:
            continue
        posts.append(
            {
                "id": row.get("id", ""),
                "url": row.get("url", ""),
                "created_at_utc": created.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "text": row.get("text", ""),
                "quoted_author": row.get("quoted_author", "") or None,
                "quoted_text": row.get("quoted_text", "") or None,
                "likes": _to_int(row.get("likes")),
                "retweets": _to_int(row.get("retweets")),
                "replies": _to_int(row.get("replies")),
                "views": _to_int(row.get("views")),
                "is_retweet": is_rt,
                "cashtags": extract_cashtags(row.get("text", "")),
            }
        )

    posts.sort(key=lambda p: p["created_at_utc"], reverse=True)
    archive_info = {
        **cache_info,
        "total_posts_in_archive": total_rows,
        "newest_post_utc": newest.strftime("%Y-%m-%dT%H:%M:%SZ") if newest else None,
        "posts_in_window": len(posts),
    }
    return posts, archive_info


def aggregate_tickers(
    posts: list[dict[str, Any]],
    min_mentions: int = 1,
    max_sample_posts: int = 3,
    sample_text_chars: int = 600,
) -> list[dict[str, Any]]:
    """
    캐시태그별 언급 횟수·참여 지표·대표 게시물을 집계.
    감성 라벨은 붙이지 않는다. 원문을 그대로 넘겨 LLM이 판단하게 한다.
    """
    buckets: dict[str, dict[str, Any]] = {}
    for post in posts:
        for sym in post["cashtags"]:
            b = buckets.setdefault(
                sym,
                {
                    "ticker": sym,
                    "mentions": 0,
                    "total_views": 0,
                    "total_likes": 0,
                    "first_mention_utc": post["created_at_utc"],
                    "last_mention_utc": post["created_at_utc"],
                    "posts": [],
                },
            )
            b["mentions"] += 1
            b["total_views"] += post["views"]
            b["total_likes"] += post["likes"]
            # posts는 최신순이므로 first는 계속 뒤로 밀린다.
            b["first_mention_utc"] = post["created_at_utc"]
            b["posts"].append(post)

    out: list[dict[str, Any]] = []
    for b in buckets.values():
        if b["mentions"] < min_mentions:
            continue
        samples = sorted(b["posts"], key=lambda p: p["views"], reverse=True)[:max_sample_posts]
        out.append(
            {
                "ticker": b["ticker"],
                "mentions": b["mentions"],
                "total_views": b["total_views"],
                "total_likes": b["total_likes"],
                "first_mention_utc": b["first_mention_utc"],
                "last_mention_utc": b["last_mention_utc"],
                "co_mentioned_with": _co_mentions(b["ticker"], b["posts"]),
                "sample_posts": [
                    {
                        "url": p["url"],
                        "created_at_utc": p["created_at_utc"],
                        "views": p["views"],
                        "likes": p["likes"],
                        "text": _truncate(p["text"], sample_text_chars),
                    }
                    for p in samples
                ],
            }
        )
    out.sort(key=lambda x: (x["mentions"], x["total_views"]), reverse=True)
    return out


def _co_mentions(ticker: str, posts: list[dict[str, Any]], limit: int = 5) -> list[str]:
    """같은 글에서 함께 언급된 티커. 테마 묶음 파악용."""
    counts: dict[str, int] = {}
    for p in posts:
        for other in p["cashtags"]:
            if other != ticker:
                counts[other] = counts.get(other, 0) + 1
    return [k for k, _ in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:limit]]


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def get_sync_state() -> dict[str, Any]:
    """아카이브 저장소의 마지막 동기화 시각. 데이터 신선도 확인용."""
    try:
        with httpx.Client(follow_redirects=True) as client:
            r = client.get(SERENITY_SYNC_STATE_URL, timeout=15)
            r.raise_for_status()
            return r.json()
    except (httpx.HTTPError, ValueError) as e:
        return {"error": str(e)}
