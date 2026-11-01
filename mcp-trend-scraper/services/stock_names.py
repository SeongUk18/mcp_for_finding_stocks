"""
자막에서 국내 종목명을 자동으로 찾아내는 유틸리티.

키워드를 미리 알아야만 셀 수 있으면 유튜버가 처음 꺼낸 종목은 영영 못 잡는다.
한투가 매일 배포하는 종목 마스터(kospi/kosdaq_code.mst, 키 불필요)의 종목명 전체를
트라이로 만들어 자막을 한 번 훑는다.

- 가장 긴 이름을 우선한다 ("SK하이닉스"가 "SK"로 잘리지 않게).
- 자동 자막은 "삼성 전자"처럼 띄어 쓰는 일이 많아 글자 사이 공백 1칸은 허용한다.
- 두 글자 이름은 일반 단어("대상", "나노", "태양")와 겹치는 게 많아서
  SHORT_NAME_ALLOW에 있는 것만 잡는다. 나머지는 keywords로 직접 넘기면 된다.
"""
from __future__ import annotations

import io
import json
import os
import time
import zipfile
from typing import Any

import httpx

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_PATH = os.path.join(_HERE, ".cache", "krx_names.json")
CACHE_TTL_SEC = 24 * 3600

_BASE = "https://new.real.download.dws.co.kr/common/master"
_MARKETS = {
    "KOSPI": (f"{_BASE}/kospi_code.mst.zip", 228),
    "KOSDAQ": (f"{_BASE}/kosdaq_code.mst.zip", 222),
}
# ST 주권, FS 외국주권, DR 예탁증서만. ETF·ETN·리츠·수익증권은 뺀다.
STOCK_GROUPS = {"ST", "FS", "DR"}

# 두 글자 이름 중 주식 방송에서 거의 항상 종목을 뜻하는 것만.
SHORT_NAME_ALLOW = {
    "기아", "두산", "한화", "효성", "농심", "루닛", "씨젠", "파두", "심텍", "엔켐", "천보",
    "휴젤", "안랩", "웹젠", "툴젠", "뷰노", "풍산", "영풍", "하림", "한샘", "대웅", "보령",
    "한진", "원익", "후성", "바텍", "고영", "삼립", "샘표", "한섬", "대덕", "쏘카", "엠로",
    "다날", "한컴", "빅텍", "KT",
}

# 방송에서 정식 명칭 대신 흔히 부르는 이름 → 종목코드.
# "하이닉스"는 이게 없으면 이름이 겹치는 다른 종목("이닉스")으로 잘못 잡힌다.
ALIASES = {
    "하이닉스": "000660",
    "삼전": "005930",
    "엘지엔솔": "373220",
    "엘엔솔": "373220",
    "에코비엠": "247540",
    "두산에너빌": "034020",
    "한화에어로": "012450",
}

_trie: dict[str, Any] | None = None
_loaded_at = 0.0
_END = "\0"


def _download(url: str, tail_len: int, market: str) -> list[dict[str, str]]:
    r = httpx.get(url, timeout=30)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        text = z.read(z.namelist()[0]).decode("cp949", errors="replace")
    out = []
    for line in text.splitlines():
        if len(line) <= tail_len:
            continue
        head, tail = line[:-tail_len], line[-tail_len:]
        code, name = head[0:9].strip(), head[21:].strip()
        if code and name and tail[1:3] in STOCK_GROUPS:
            out.append({"code": code, "name": name, "market": market})
    return out


def load_stocks() -> list[dict[str, str]]:
    """종목 목록. 파일 캐시 하루, 다운로드 실패 시 낡은 캐시라도 쓴다."""
    cached = None
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, encoding="utf-8") as fp:
                cached = json.load(fp)
        except (OSError, ValueError):
            cached = None
    if cached and time.time() - cached.get("fetched_at", 0) < CACHE_TTL_SEC:
        return cached["stocks"]
    try:
        stocks = []
        for market, (url, tail_len) in _MARKETS.items():
            stocks.extend(_download(url, tail_len, market))
    except Exception:
        if cached:
            return cached["stocks"]
        raise
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as fp:
        json.dump({"fetched_at": time.time(), "stocks": stocks}, fp, ensure_ascii=False)
    return stocks


def _build_trie() -> dict[str, Any]:
    global _trie, _loaded_at
    if _trie is not None and time.time() - _loaded_at < CACHE_TTL_SEC:
        return _trie
    root: dict[str, Any] = {}

    def insert(name: str, stock: dict[str, str]) -> None:
        node = root
        for ch in name.upper():
            node = node.setdefault(ch, {})
        node[_END] = stock

    by_code: dict[str, dict[str, str]] = {}
    for s in load_stocks():
        by_code[s["code"]] = s
        name = s["name"].replace(" ", "")
        if len(name) < 2 or (len(name) == 2 and name not in SHORT_NAME_ALLOW):
            continue
        insert(name, s)
    for alias, code in ALIASES.items():
        if code in by_code:
            insert(alias, by_code[code])
    _trie, _loaded_at = root, time.time()
    return root


def _is_hangul(ch: str) -> bool:
    return "가" <= ch <= "힣"


def find_stock_mentions(text: str) -> list[tuple[int, int, dict[str, str]]]:
    """
    (시작, 끝, 종목) 목록. 겹치지 않고 가장 긴 이름 우선.

    이름은 단어 첫머리에서만 시작한다. 앞 글자가 한글이면 다른 단어의 꼬리라서
    ("하이닉스" 안의 "이닉스", "정기 아파"의 "기아") 건너뛴다.
    """
    trie = _build_trie()
    upper = text.upper()
    n = len(upper)
    hits: list[tuple[int, int, dict[str, str]]] = []
    i = 0
    while i < n:
        node = trie.get(upper[i])
        if node is None or (i > 0 and _is_hangul(upper[i - 1])):
            i += 1
            continue
        j, best = i + 1, None
        while node is not None:
            if _END in node:
                best = (i, j, node[_END])
            if j >= n:
                break
            nxt = node.get(upper[j])
            if nxt is None and upper[j] == " " and j + 1 < n and j - i >= 2:
                # 자동 자막의 띄어쓰기 한 칸은 건너뛴다 ("삼성 전자", "SK 하이닉스").
                # 한 글자 뒤 공백은 넘지 않는다 ("기 아" 같은 우연한 조합 방지)
                nxt = node.get(upper[j + 1])
                if nxt is not None:
                    j += 1
            node = nxt
            j += 1
        if best:
            hits.append(best)
            i = best[1]
        else:
            i += 1
    return hits


def snippet(text: str, start: int, end: int, width: int = 60) -> str:
    left = max(0, start - width)
    right = min(len(text), end + width)
    return ("…" if left else "") + text[left:right].strip() + ("…" if right < len(text) else "")
