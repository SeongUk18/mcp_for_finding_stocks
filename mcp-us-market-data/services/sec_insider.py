"""
SEC EDGAR Form 4 (내부자 거래) 파서.

Quiver Quantitative의 내부자 데이터와 같은 원천이지만 키 없이 무료다.
- company_tickers.json: 티커 → CIK
- submissions/CIK##########.json: 최근 제출 목록 (발행회사 CIK로 조회하면 그 회사 내부자들의 Form 4가 나온다)
- 각 Form 4 원본 XML: 거래 코드·수량·단가·10b5-1 여부

Form 4는 거래 후 2영업일 안에 제출된다. 13F(분기, 최대 45일 지연)보다 훨씬 빠르다.
거래 코드 P(장내 매수)가 가장 의미 있는 신호이고, S(매도)는 세금·분산 목적이 많아 약하다.
10b5-1 사전 계획 거래는 타이밍 정보가 거의 없다.
"""
from __future__ import annotations

import json
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Any

import httpx

from config import CACHE_DIR, SEC_USER_AGENT

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"

TICKER_CACHE = os.path.join(CACHE_DIR, "sec_company_tickers.json")
TICKER_TTL_SEC = 7 * 24 * 3600

# SEC 공정 접근 정책: 초당 10회 이하.
_MIN_INTERVAL_SEC = 0.12
_last_request = 0.0

TRANSACTION_CODES = {
    "P": "장내 매수",
    "S": "장내 매도",
    "A": "보상 지급(그랜트)",
    "M": "옵션 행사",
    "F": "세금 납부용 원천징수",
    "G": "증여",
    "C": "전환",
    "D": "회사에 처분",
    "X": "옵션 행사(장내)",
}

_tickers: dict[str, int] | None = None


def _get(url: str) -> httpx.Response:
    global _last_request
    wait = _MIN_INTERVAL_SEC - (time.time() - _last_request)
    if wait > 0:
        time.sleep(wait)
    r = httpx.get(url, headers={"User-Agent": SEC_USER_AGENT}, timeout=20, follow_redirects=True)
    _last_request = time.time()
    if r.status_code == 403:
        raise RuntimeError("SEC가 요청을 거부했습니다(403). .env의 SEC_USER_AGENT에 '이름 이메일'을 넣어 보세요.")
    r.raise_for_status()
    return r


def cik_of(symbol: str) -> int:
    global _tickers
    if _tickers is None:
        data = None
        if os.path.exists(TICKER_CACHE) and time.time() - os.path.getmtime(TICKER_CACHE) < TICKER_TTL_SEC:
            try:
                with open(TICKER_CACHE, encoding="utf-8") as fp:
                    data = json.load(fp)
            except (OSError, ValueError):
                data = None
        if data is None:
            data = _get(TICKERS_URL).json()
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(TICKER_CACHE, "w", encoding="utf-8") as fp:
                json.dump(data, fp)
        _tickers = {row["ticker"].upper(): int(row["cik_str"]) for row in data.values()}
    cik = _tickers.get(symbol.upper().replace(".", "-"))
    if cik is None:
        raise RuntimeError(f"{symbol}: SEC 티커 목록에 없습니다 (ETF·외국 기업일 수 있음)")
    return cik


def _text(node: ET.Element | None, path: str) -> str | None:
    if node is None:
        return None
    found = node.find(path)
    if found is None:
        return None
    value = found.findtext("value") if found.find("value") is not None else found.text
    return value.strip() if value and value.strip() else None


def _float(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _parse_form4(xml_text: str) -> dict[str, Any]:
    root = ET.fromstring(xml_text)
    owners = []
    for owner in root.findall("reportingOwner"):
        rel = owner.find("reportingOwnerRelationship")
        roles = []
        if _text(rel, "isDirector") in ("1", "true"):
            roles.append("director")
        if _text(rel, "isOfficer") in ("1", "true"):
            roles.append(_text(rel, "officerTitle") or "officer")
        if _text(rel, "isTenPercentOwner") in ("1", "true"):
            roles.append("10% owner")
        owners.append({"name": _text(owner, "reportingOwnerId/rptOwnerName"), "roles": roles})

    plan_flag = _text(root, "aff10b5One")
    transactions = []
    for tx in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = _text(tx, "transactionCoding/transactionCode")
        shares = _float(_text(tx, "transactionAmounts/transactionShares"))
        price = _float(_text(tx, "transactionAmounts/transactionPricePerShare"))
        transactions.append({
            "date": _text(tx, "transactionDate"),
            "code": code,
            "code_label": TRANSACTION_CODES.get(code or "", code),
            "acquired_or_disposed": _text(tx, "transactionAmounts/transactionAcquiredDisposedCode"),
            "shares": shares,
            "price": price,
            "value_usd": round(shares * price, 2) if shares and price else None,
            "shares_after": _float(_text(tx, "postTransactionAmounts/sharesOwnedFollowingTransaction")),
            "direct": _text(tx, "ownershipNature/directOrIndirectOwnership") == "D",
        })
    return {
        "owners": owners,
        "is_10b5_1_plan": plan_flag in ("1", "true") if plan_flag is not None else None,
        "transactions": transactions,
    }


def get_insider_transactions(symbol: str, days: int = 90, max_filings: int = 40) -> dict[str, Any]:
    """최근 days일 Form 4를 원본 XML까지 읽어 거래 단위로 펼친다."""
    cik = cik_of(symbol)
    recent = _get(SUBMISSIONS_URL.format(cik=f"{cik:010d}")).json()["filings"]["recent"]
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    filings = []
    for n, form in enumerate(recent["form"]):
        if form != "4" or recent["filingDate"][n] < cutoff:
            continue
        filings.append((recent["filingDate"][n], recent["accessionNumber"][n], recent["primaryDocument"][n]))
    truncated = len(filings) > max_filings
    filings = filings[:max_filings]

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for filed, acc, doc in filings:
        # primaryDocument는 xslF345X0N/ 렌더링 경로라 파일명만 떼서 원본 XML을 받는다
        url = ARCHIVE_URL.format(cik=cik, acc=acc.replace("-", ""), doc=doc.split("/")[-1])
        try:
            parsed = _parse_form4(_get(url).text)
        except Exception as e:
            errors.append(f"{acc}: {e}")
            continue
        owner_names = ", ".join(o["name"] or "?" for o in parsed["owners"])
        roles = sorted({r for o in parsed["owners"] for r in o["roles"]})
        for tx in parsed["transactions"]:
            rows.append({
                "filed": filed,
                "owner": owner_names,
                "roles": roles,
                "is_10b5_1_plan": parsed["is_10b5_1_plan"],
                **tx,
                "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/",
            })

    def _sum(code: str, plan: bool | None = None) -> dict[str, Any]:
        hit = [r for r in rows if r["code"] == code and (plan is None or bool(r["is_10b5_1_plan"]) == plan)]
        return {
            "count": len(hit),
            "insiders": len({r["owner"] for r in hit}),
            "shares": sum(r["shares"] or 0 for r in hit),
            "value_usd": round(sum(r["value_usd"] or 0 for r in hit), 2),
        }

    return {
        "symbol": symbol.upper(),
        "cik": cik,
        "days": days,
        "form4_filings": len(filings),
        "truncated": truncated,
        "summary": {
            "open_market_buys": _sum("P"),
            "open_market_sells": _sum("S"),
            "open_market_sells_outside_plan": _sum("S", plan=False),
        },
        "transactions": rows,
        "errors": errors or None,
    }
