"""
일별 투자자 수급(확정 금액·확정 수량)을 날짜 기준으로 정리하고 검증한다.

한투 일별 투자자 API는 당일 행을 장 종료 후에야 채운다. 그 전에는
- inquire-investor(금액)는 당일 행을 0으로 채워 주고
- investor-trade-by-stock-daily(수량)는 당일 시세(종가·거래량)를 전 거래일 날짜로 붙여 주는 일이 있다
  (2026-11-09 15:34 대한항공: "20261106" 행에 11/09 종가 29,100 / 거래량 1,537,099)
그래서 행 순서는 믿지 않고 stck_bsop_date로만 날짜를 붙인 뒤, 일봉과 대조해서
다른 날짜 값이 붙은 행은 빼고 경고를 남긴다. API 호출은 하지 않는다.
"""
from typing import Any

from services.kis_api import f, i

# 같은 날 다른 호출 사이에 거래량이 조금 늘 수 있어 당일 비교에만 쓰는 허용 오차
VOLUME_TOLERANCE = 0.02


def parse_confirmed(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """inquire-investor(FHKST01010900) 행. 금액은 한투 원 단위(백만원) 그대로다."""
    out = []
    for r in rows:
        date = (r.get("stck_bsop_date") or "").strip()
        if not date:
            continue
        out.append({
            "date": date,
            "close": f(r.get("stck_clpr")),
            "foreign_amt": i(r.get("frgn_ntby_tr_pbmn")),
            "institution_amt": i(r.get("orgn_ntby_tr_pbmn")),
            "individual_amt": i(r.get("prsn_ntby_tr_pbmn")),
            "foreign_qty": i(r.get("frgn_ntby_qty")),
            "institution_qty": i(r.get("orgn_ntby_qty")),
            "individual_qty": i(r.get("prsn_ntby_qty")),
        })
    return out


def _blank(row: dict[str, Any], keys: tuple[str, ...]) -> bool:
    return all(not row.get(k) for k in keys)


_CONFIRMED_KEYS = ("foreign_amt", "institution_amt", "individual_amt",
                   "foreign_qty", "institution_qty", "individual_qty")
_DETAIL_KEYS = ("foreign_net_qty", "institution_net_qty", "individual_net_qty")


def _same_bar(close: float | None, volume: int | None, ref: dict[str, Any], tolerant: bool) -> bool:
    if close is None or ref.get("close") is None or close != ref["close"]:
        return False
    if volume is None or not ref.get("volume"):
        return True
    if tolerant:
        return abs(volume - ref["volume"]) <= ref["volume"] * VOLUME_TOLERANCE
    return volume == ref["volume"]


def check_detail_rows(
    rows: list[dict[str, Any]],
    ref_bars: dict[str, dict[str, Any]],
    today: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], list[str]]:
    """
    daily_detail 행을 일봉(ref_bars: 날짜 → {close, volume})과 대조한다.
    반환: (정상 행, 날짜가 밀린 것으로 보이는 행, 미확정 날짜, 경고)
    """
    ok, suspect, unconfirmed, warnings = [], [], [], []
    for row in sorted(rows, key=lambda r: r.get("date") or "", reverse=True):
        date = row.get("date") or ""
        if not date:
            continue
        if date == today and _blank(row, _DETAIL_KEYS):
            unconfirmed.append(date)
            continue
        own = ref_bars.get(date)
        if own is None or _same_bar(row.get("close"), row.get("volume"), own, tolerant=date == today):
            ok.append(row)
            continue
        other = next((d for d, b in sorted(ref_bars.items(), reverse=True)
                      if d != date and _same_bar(row.get("close"), row.get("volume"), b, tolerant=d == today)),
                     None)
        if other:
            suspect.append({**row, "matches_date": other})
            warnings.append(
                f"daily_detail {date} 행의 종가 {row.get('close')}·거래량 {row.get('volume')}이 "
                f"일봉 {other} 값과 같습니다. 날짜가 어긋난 행으로 보고 제외했습니다."
            )
        elif date != today:
            ok.append(row)
            warnings.append(
                f"daily_detail {date} 종가 {row.get('close')}가 일봉 종가 {own.get('close')}와 다릅니다."
            )
        else:
            ok.append(row)
    return ok, suspect, unconfirmed, warnings


def build(
    confirmed_raw: list[dict[str, Any]],
    detail_rows: list[dict[str, Any]] | None,
    ref_bars: dict[str, dict[str, Any]],
    short_closes: dict[str, float] | None,
    today: str,
    days_back: int,
    amt_multiplier: int,
) -> tuple[dict[str, Any], dict[str, Any] | None, list[str]]:
    """
    확정 금액(confirmed_raw)과 확정 수량(detail_rows)을 날짜 기준으로 정리한다.
    당일 0값 행은 미확정으로 빼고 합계에 넣지 않는다.
    반환: (daily_confirmed, daily_detail, 경고 목록)
    """
    warnings: list[str] = []

    confirmed = sorted(parse_confirmed(confirmed_raw), key=lambda r: r["date"], reverse=True)
    unconfirmed = [r["date"] for r in confirmed if r["date"] == today and _blank(r, _CONFIRMED_KEYS)]
    confirmed = [r for r in confirmed if r["date"] not in unconfirmed][:days_back]

    daily_confirmed: dict[str, Any] = {
        "as_of_date": confirmed[0]["date"] if confirmed else None,
        "today_included": bool(confirmed) and confirmed[0]["date"] == today,
        "unconfirmed_dates": unconfirmed,
    }
    for name in ("foreign", "institution", "individual"):
        daily = [{
            "date": r["date"],
            "net_buying_amt": (r[f"{name}_amt"] or 0) * amt_multiplier,
            "net_buying_qty": r[f"{name}_qty"],
        } for r in confirmed]
        daily_confirmed[name] = {
            "net_buying_amt_sum": sum(d["net_buying_amt"] for d in daily),
            "daily": daily,
        }
    daily_confirmed["unit"] = "원"
    if unconfirmed:
        daily_confirmed["note"] = (
            f"{', '.join(unconfirmed)}는 장 종료 후 확정 전이라 0으로 와서 합계에서 뺐습니다."
        )

    for r in confirmed:
        ref = ref_bars.get(r["date"])
        if r["date"] != today and ref and r["close"] is not None and r["close"] != ref.get("close"):
            warnings.append(f"daily_confirmed {r['date']} 종가 {r['close']}가 일봉 종가 {ref.get('close')}와 다릅니다.")

    if detail_rows is None:
        return daily_confirmed, None, warnings

    ok, suspect, detail_unconfirmed, detail_warnings = check_detail_rows(detail_rows, ref_bars, today)
    warnings += detail_warnings
    ok = ok[:days_back]

    by_date = {r["date"]: r for r in confirmed}
    for row in ok:
        c = by_date.get(row["date"])
        if c and c["foreign_qty"] is not None and row.get("foreign_net_qty") is not None \
                and c["foreign_qty"] != row["foreign_net_qty"]:
            warnings.append(
                f"{row['date']} 외국인 순매수 수량이 daily_confirmed({c['foreign_qty']})와 "
                f"daily_detail({row['foreign_net_qty']})에서 다릅니다."
            )
        if short_closes and row["date"] in short_closes and row.get("close") is not None \
                and short_closes[row["date"]] is not None and short_closes[row["date"]] != row["close"]:
            warnings.append(
                f"{row['date']} daily_detail 종가 {row['close']}와 short_sale 종가 "
                f"{short_closes[row['date']]}가 다릅니다."
            )

    daily_detail: dict[str, Any] = {
        "as_of_date": ok[0]["date"] if ok else None,
        "today_included": bool(ok) and ok[0]["date"] == today,
        "unconfirmed_dates": detail_unconfirmed,
        "rows": ok,
        "unit": "주",
        "note": "수량 기준입니다. 금액은 daily_confirmed를 보세요.",
    }
    if suspect:
        daily_detail["excluded_rows"] = suspect
        daily_detail["excluded_note"] = "일봉과 대조해 다른 날짜 값이 붙은 행입니다. 판단에 쓰지 마세요."
    return daily_confirmed, daily_detail, warnings
