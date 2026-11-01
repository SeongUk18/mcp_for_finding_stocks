"""
금융감독원 OpenDART 래퍼 (공시).

단타에서 공시는 재료의 원본이다. 뉴스는 공시를 늦게, 해석을 섞어 전한다.
- 공시검색(list.json): 최근 공시 목록. 보고서명으로 성격을 분류만 하고 호재/악재 판단은 안 한다.
- 임원·주요주주 소유보고(elestock.json): 한국판 내부자 거래.
- 대량보유 상황보고(majorstock.json): 5% 이상 보유자 변동.

DART는 종목코드가 아니라 8자리 고유번호(corp_code)로 조회한다.
corpCode.xml(zip)을 받아 종목코드 → 고유번호 표를 만들고 7일간 캐시한다.
"""
from __future__ import annotations

import io
import json
import os
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Any

import httpx

from config import DART_API_KEY

BASE = "https://opendart.fss.or.kr/api"
VIEWER = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORP_CODE_CACHE = os.path.join(_HERE, ".cache", "dart_corp_codes.json")
CORP_CODE_TTL_SEC = 7 * 24 * 3600

_corp_codes: dict[str, str] | None = None

# 보고서명 키워드 → 분류. 위에서부터 먼저 걸리는 것을 쓴다.
# 분류는 공시의 성격일 뿐 호재/악재 판단이 아니다.
CATEGORIES: list[tuple[str, tuple[str, ...]]] = [
    ("risk", ("거래정지", "매매거래정지", "불성실공시", "관리종목", "상장폐지", "상장적격성",
              "감사의견", "횡령", "배임", "회생절차", "파산", "부도", "투자주의", "투자경고",
              "투자위험", "단기과열", "소송", "감자")),
    ("dilution", ("유상증자", "유무상증자", "전환사채", "신주인수권부사채", "교환사채",
                  "전환청구권", "신주인수권행사", "주식관련사채", "추가상장")),
    ("contract", ("단일판매", "공급계약", "수주")),
    ("earnings", ("영업(잠정)실적", "잠정실적", "매출액또는손익구조", "손익구조")),
    ("shareholder_return", ("자기주식", "자사주", "배당", "무상증자", "주식소각")),
    ("ownership", ("최대주주", "대량보유", "특정증권등소유", "공개매수", "주식등의대량")),
    ("corporate_action", ("합병", "분할", "영업양수", "영업양도", "자산양수", "자산양도",
                          "타법인주식", "유형자산")),
]

CATEGORY_LABELS = {
    "risk": "매매 제약·위험",
    "dilution": "자금조달·주식 희석",
    "contract": "수주·공급계약",
    "earnings": "실적",
    "shareholder_return": "주주환원·자기주식",
    "ownership": "지분 변동",
    "corporate_action": "합병·분할·자산 거래",
    "other": "기타",
}


def _require_key() -> None:
    if not DART_API_KEY:
        raise RuntimeError(
            "DART_API_KEY가 없습니다. opendart.fss.or.kr에서 무료로 발급받아 "
            "mcp-market-data/.env에 DART_API_KEY=... 로 넣으세요."
        )


def _get(path: str, params: dict[str, str]) -> dict[str, Any]:
    _require_key()
    r = httpx.get(f"{BASE}/{path}", params={"crtfc_key": DART_API_KEY, **params}, timeout=20)
    r.raise_for_status()
    data = r.json()
    status = str(data.get("status") or (data.get("result") or {}).get("status") or "")
    if status == "013":  # 조회된 데이터 없음
        return {"list": []}
    if status != "000":
        raise RuntimeError(f"DART 오류 {status}: {data.get('message')}")
    return data


def _num(value: Any) -> float | None:
    if value is None:
        return None
    s = str(value).strip().replace(",", "")
    if not s or s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        return None


# 키워드는 걸리지만 성격이 다른 공시. 유동성공급계약은 LP(호가 조성) 계약이지 수주가 아니다.
_NOT_CATEGORIZED = ("유동성공급계약",)


def classify(report_nm: str) -> str:
    name = (report_nm or "").replace(" ", "")
    if any(k in name for k in _NOT_CATEGORIZED):
        return "other"
    for category, keywords in CATEGORIES:
        if any(k in name for k in keywords):
            return category
    return "other"


def _load_corp_codes() -> dict[str, str]:
    """종목코드(6자리) → DART 고유번호(8자리)."""
    global _corp_codes
    if _corp_codes is not None:
        return _corp_codes
    if os.path.exists(CORP_CODE_CACHE):
        try:
            with open(CORP_CODE_CACHE, encoding="utf-8") as fp:
                cached = json.load(fp)
            if time.time() - cached.get("fetched_at", 0) < CORP_CODE_TTL_SEC:
                _corp_codes = cached["codes"]
                return _corp_codes
        except (OSError, ValueError, KeyError):
            pass

    _require_key()
    r = httpx.get(f"{BASE}/corpCode.xml", params={"crtfc_key": DART_API_KEY}, timeout=60)
    r.raise_for_status()
    if not r.content.startswith(b"PK"):
        # 키 오류 등은 zip 대신 에러 XML/JSON이 온다
        raise RuntimeError(f"DART 고유번호 파일을 받지 못했습니다: {r.text[:200]}")
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        root = ET.fromstring(z.read(z.namelist()[0]))
    codes: dict[str, str] = {}
    for node in root.iter("list"):
        stock = (node.findtext("stock_code") or "").strip()
        corp = (node.findtext("corp_code") or "").strip()
        if stock and corp:
            codes[stock] = corp
    os.makedirs(os.path.dirname(CORP_CODE_CACHE), exist_ok=True)
    with open(CORP_CODE_CACHE, "w", encoding="utf-8") as fp:
        json.dump({"fetched_at": time.time(), "codes": codes}, fp)
    _corp_codes = codes
    return codes


def corp_code_of(ticker_code: str) -> str:
    code = _load_corp_codes().get(ticker_code)
    if not code:
        raise RuntimeError(f"{ticker_code}: DART 고유번호를 찾지 못했습니다 (상장 종목인지 확인)")
    return code


def _ymd(d: datetime) -> str:
    return d.strftime("%Y%m%d")


def get_disclosures(
    ticker_code: str = "",
    days: int = 30,
    max_pages: int = 3,
    listed_only: bool = True,
) -> list[dict[str, Any]]:
    """
    공시 목록. ticker_code가 비면 시장 전체(이 경우 DART가 기간을 3개월로 제한한다).
    정정 공시는 최종본만 본다(last_reprt_at=Y).
    """
    end = datetime.now()
    start = end - timedelta(days=max(0, days - 1))
    params = {
        "bgn_de": _ymd(start), "end_de": _ymd(end),
        "last_reprt_at": "Y", "page_count": "100",
    }
    if ticker_code:
        params["corp_code"] = corp_code_of(ticker_code)

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        data = _get("list.json", {**params, "page_no": str(page)})
        fresh = 0
        for r in data.get("list") or []:
            rcept_no = r.get("rcept_no") or ""
            if rcept_no in seen:
                continue
            seen.add(rcept_no)
            fresh += 1
            stock = (r.get("stock_code") or "").strip()
            if listed_only and not stock:
                continue
            category = classify(r.get("report_nm") or "")
            d = r.get("rcept_dt") or ""
            out.append({
                "date": f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else d,
                "ticker": stock or None,
                "name": r.get("corp_name"),
                "market": {"Y": "KOSPI", "K": "KOSDAQ", "N": "KONEX"}.get(r.get("corp_cls"), r.get("corp_cls")),
                "report": (r.get("report_nm") or "").strip(),
                "category": category,
                "category_label": CATEGORY_LABELS[category],
                "filer": r.get("flr_nm"),
                "remarks": (r.get("rm") or "").strip() or None,
                "url": VIEWER + rcept_no,
            })
        if not fresh or page >= int(data.get("total_page") or 1):
            break
    return out


def _recent(rows: list[dict[str, Any]], days: int) -> list[dict[str, Any]]:
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    return [r for r in rows if (r.get("rcept_dt") or "") >= cutoff]


def get_insider_reports(ticker_code: str, days: int = 90) -> list[dict[str, Any]]:
    """임원·주요주주 특정증권등 소유상황보고 (elestock.json). 최근 days일만."""
    rows = _get("elestock.json", {"corp_code": corp_code_of(ticker_code)}).get("list") or []
    out = [{
        "date": r.get("rcept_dt"),
        "reporter": r.get("repror"),
        "registered_exec": r.get("isu_exctv_rgist_at"),
        "position": r.get("isu_exctv_ofcps"),
        "major_holder": r.get("isu_main_shrholdr"),
        "shares_held": _num(r.get("sp_stock_lmp_cnt")),
        "shares_change": _num(r.get("sp_stock_lmp_irds_cnt")),
        "holding_pct": _num(r.get("sp_stock_lmp_rate")),
        "holding_pct_change": _num(r.get("sp_stock_lmp_irds_rate")),
        "url": VIEWER + (r.get("rcept_no") or ""),
    } for r in _recent(rows, days)]
    out.sort(key=lambda x: x["date"] or "", reverse=True)
    return out


def get_major_holder_reports(ticker_code: str, days: int = 90) -> list[dict[str, Any]]:
    """주식등의 대량보유상황보고 (majorstock.json, 5% 룰). 최근 days일만."""
    rows = _get("majorstock.json", {"corp_code": corp_code_of(ticker_code)}).get("list") or []
    out = [{
        "date": r.get("rcept_dt"),
        "report_type": r.get("report_tp"),
        "reporter": r.get("repror"),
        "shares_held": _num(r.get("stkqy")),
        "shares_change": _num(r.get("stkqy_irds")),
        "holding_pct": _num(r.get("stkrt")),
        "holding_pct_change": _num(r.get("stkrt_irds")),
        "reason": r.get("report_resn"),
        "url": VIEWER + (r.get("rcept_no") or ""),
    } for r in _recent(rows, days)]
    out.sort(key=lambda x: x["date"] or "", reverse=True)
    return out
