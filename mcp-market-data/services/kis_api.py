"""
한국투자증권 Open API REST 래퍼 (국내주식).

시세·차트뿐 아니라 수급(가집계·창구·외국계·프로그램·공매도·융자),
순위(거래량·등락률·체결강도·VI), 재료(속보·투자의견·컨센서스)까지 다룬다.

한투 응답은 모든 수치를 문자열로 주고, 엔드포인트마다 output/output1/output2로
키가 흔들린다. 파싱 헬퍼로 흡수한다.
"""
from datetime import datetime, timedelta
from typing import Any

import httpx

from config import KIS_BASE_URL, KIS_APPKEY, KIS_APPSECRET
from services.token_manager import get_access_token

Q = "/uapi/domestic-stock/v1/quotations"
RANK = "/uapi/domestic-stock/v1/ranking"

# 지수 코드
INDEX_KOSPI = "0001"
INDEX_KOSDAQ = "1001"


def _kis_request(
    path: str,
    tr_id: str,
    params: dict[str, str],
) -> dict[str, Any]:
    token = get_access_token()
    url = f"{KIS_BASE_URL}{path}"
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": KIS_APPKEY,
        "appsecret": KIS_APPSECRET,
        "tr_id": tr_id,
        "custtype": "P",
    }
    with httpx.Client() as client:
        r = client.get(url, params=params, headers=headers, timeout=20)
        r.raise_for_status()
        return r.json()


def _checked(res: dict[str, Any], tr_id: str) -> dict[str, Any]:
    """rt_cd가 0이 아니면 예외. 조용한 실패를 만들지 않는다."""
    if str(res.get("rt_cd")) not in ("0", ""):
        raise RuntimeError(f"한투 API 오류 [{tr_id}] {res.get('msg_cd')}: {res.get('msg1')}")
    return res


def _rows(res: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    """output2 → output1 → output 순으로 첫 리스트를 찾는다."""
    body = res.get("body") or res
    for k in (keys or ("output2", "output1", "output")):
        v = body.get(k)
        if isinstance(v, list) and v:
            return [r for r in v if isinstance(r, dict)]
        if isinstance(v, dict) and v:
            return [v]
    return []


def _one(res: dict[str, Any], *keys: str) -> dict[str, Any]:
    body = res.get("body") or res
    for k in (keys or ("output", "output1", "output2")):
        v = body.get(k)
        if isinstance(v, dict) and v:
            return v
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return v[0]
    return {}


def f(value: Any) -> float | None:
    """문자열 수치를 float으로. 빈 값은 None."""
    if value is None:
        return None
    s = str(value).strip().replace(",", "")
    if not s or s in ("-", "."):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def i(value: Any) -> int | None:
    v = f(value)
    return int(v) if v is not None else None


def _ymd(d: datetime) -> str:
    return d.strftime("%Y%m%d")


# ---------------------------------------------------------------- 시세·차트

def get_price(ticker_code: str) -> dict[str, Any]:
    """현재가 조회 (FHKST01010100)"""
    return _kis_request(
        f"{Q}/inquire-price",
        "FHKST01010100",
        {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker_code},
    )


def get_minute_chart(ticker_code: str, interval: str = "15") -> dict[str, Any]:
    """분봉 차트 (FHKST03010200)."""
    return _kis_request(
        f"{Q}/inquire-time-itemchartprice",
        "FHKST03010200",
        {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker_code,
            "FID_INPUT_HOUR_1": "153000",
            "FID_PW_DATA_INCU_YN": "N",
        },
    )


def get_investor_trend(ticker_code: str) -> dict[str, Any]:
    """투자자별 매매 동향 (외인/기관/개인) — 일별 확정치."""
    return _kis_request(
        f"{Q}/inquire-investor",
        "FHKST01010900",
        {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker_code},
    )


def get_daily_chart(
    ticker_code: str,
    period: str = "D",
    start_ymd: str | None = None,
    end_ymd: str | None = None,
) -> dict[str, Any]:
    """일봉 차트. 기간 미지정 시 최근 120일 구간 사용."""
    if not start_ymd or not end_ymd:
        end = datetime.now()
        start = end - timedelta(days=120)
        end_ymd = _ymd(end)
        start_ymd = _ymd(start)
    return _kis_request(
        f"{Q}/inquire-daily-itemchartprice",
        "FHKST03010100",
        {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker_code,
            "FID_INPUT_DATE_1": start_ymd,
            "FID_INPUT_DATE_2": end_ymd,
            "FID_PERIOD_DIV_CODE": period,
            "FID_ORG_ADJ_PRC": "0",
        },
    )


def _parse_ohlcv(rows: list[dict[str, Any]], close_key: str) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        date = r.get("stck_bsop_date")
        close = f(r.get(close_key))
        if not date or close is None:
            continue
        out.append({
            "date": date,
            "open": f(r.get("stck_oprc") or r.get("bstp_nmix_oprc")),
            "high": f(r.get("stck_hgpr") or r.get("bstp_nmix_hgpr")),
            "low": f(r.get("stck_lwpr") or r.get("bstp_nmix_lwpr")),
            "close": close,
            "volume": i(r.get("acml_vol")) or 0,
        })
    return out


def get_daily_bars(ticker_code: str, count: int = 250) -> list[dict[str, Any]]:
    """
    일봉을 count개 이상 모을 때까지 구간을 뒤로 밀며 페이징한다.

    한투 일봉 API는 한 번에 최대 100건이라, 이렇게 하지 않으면
    MA200이나 60일 상대강도를 계산할 수 없다.
    """
    bars: dict[str, dict[str, Any]] = {}
    end = datetime.now()
    for _ in range(6):
        start = end - timedelta(days=150)
        res = _checked(
            get_daily_chart(ticker_code, start_ymd=_ymd(start), end_ymd=_ymd(end)),
            "FHKST03010100",
        )
        rows = _parse_ohlcv(_rows(res, "output2", "output1", "output"), "stck_clpr")
        if not rows:
            break
        before = len(bars)
        for b in rows:
            bars[b["date"]] = b
        if len(bars) >= count or len(bars) == before:
            break
        end = datetime.strptime(min(bars), "%Y%m%d") - timedelta(days=1)
    return [bars[k] for k in sorted(bars)][-count:]


def get_index_daily_bars(index_code: str = INDEX_KOSPI, count: int = 250) -> list[dict[str, Any]]:
    """지수 일봉. 상대강도 계산용. 한 번에 50건이라 역시 페이징한다."""
    bars: dict[str, dict[str, Any]] = {}
    end = datetime.now()
    for _ in range(8):
        start = end - timedelta(days=100)
        res = _checked(
            _kis_request(
                f"{Q}/inquire-daily-indexchartprice",
                "FHKUP03500100",
                {
                    "FID_COND_MRKT_DIV_CODE": "U",
                    "FID_INPUT_ISCD": index_code,
                    "FID_INPUT_DATE_1": _ymd(start),
                    "FID_INPUT_DATE_2": _ymd(end),
                    "FID_PERIOD_DIV_CODE": "D",
                },
            ),
            "FHKUP03500100",
        )
        rows = _parse_ohlcv(_rows(res, "output2"), "bstp_nmix_prpr")
        if not rows:
            break
        before = len(bars)
        for b in rows:
            bars[b["date"]] = b
        if len(bars) >= count or len(bars) == before:
            break
        end = datetime.strptime(min(bars), "%Y%m%d") - timedelta(days=1)
    return [bars[k] for k in sorted(bars)][-count:]


def get_index_price(index_code: str = INDEX_KOSPI) -> dict[str, Any]:
    """지수 현재가 (FHPUP02100000)."""
    res = _checked(
        _kis_request(
            f"{Q}/inquire-index-price",
            "FHPUP02100000",
            {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": index_code},
        ),
        "FHPUP02100000",
    )
    o = _one(res)
    return {
        "index_code": index_code,
        "value": f(o.get("bstp_nmix_prpr")),
        "change": f(o.get("bstp_nmix_prdy_vrss")),
        "change_pct": f(o.get("bstp_nmix_prdy_ctrt")),
        "open": f(o.get("bstp_nmix_oprc")),
        "high": f(o.get("bstp_nmix_hgpr")),
        "low": f(o.get("bstp_nmix_lwpr")),
    }


def get_asking_price(ticker_code: str) -> dict[str, Any]:
    """
    호가 10단계 + 예상체결가 (FHKST01010200).
    장전 동시호가(08:30~09:00)의 예상체결가는 갭 예측에 직결된다.
    """
    res = _checked(
        _kis_request(
            f"{Q}/inquire-asking-price-exp-ccn",
            "FHKST01010200",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker_code},
        ),
        "FHKST01010200",
    )
    book = res.get("output1") or {}
    exp = res.get("output2") or {}

    levels = []
    for n in range(1, 11):
        bid, ask = f(book.get(f"bidp{n}")), f(book.get(f"askp{n}"))
        if bid is None and ask is None:
            continue
        levels.append({
            "level": n,
            "bid": bid, "bid_size": i(book.get(f"bidp_rsqn{n}")),
            "ask": ask, "ask_size": i(book.get(f"askp_rsqn{n}")),
        })

    best_bid = levels[0]["bid"] if levels else None
    best_ask = levels[0]["ask"] if levels else None
    spread = (best_ask - best_bid) if (best_bid and best_ask) else None
    return {
        "quote_time": book.get("aspr_acpt_hour"),
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "spread_pct": round(spread / best_ask * 100, 3) if (spread and best_ask) else None,
        "total_bid_size": i(book.get("total_bidp_rsqn")),
        "total_ask_size": i(book.get("total_askp_rsqn")),
        "levels": levels,
        "expected": {
            "price": f(exp.get("antc_cnpr")),
            "change": f(exp.get("antc_cntg_vrss")),
            "change_pct": f(exp.get("antc_cntg_prdy_ctrt")),
            "volume": i(exp.get("antc_vol")),
            "vi_triggered": exp.get("vi_cls_code") == "Y",
            "note": "장전·장마감 동시호가 구간에서만 의미가 있습니다.",
        },
    }


# ---------------------------------------------------------------- 순위

# 대상 제외 마스크 10자리. 차례대로
# 투자위험/경고/주의, 관리종목, 정리매매, 불성실공시, 우선주, 거래정지, ETF, ETN, 신용주문불가, SPAC
# 기본값은 잡주·ETF·인버스를 걸러낸다. 이걸 끄면 순위 상위가 KODEX 인버스로 도배된다.
EXCLUDE_NOISE = "1111111101"
EXCLUDE_NONE = "0000000000"

# 순위 API가 마스크를 지원하지 않는 경우를 대비한 이름 기반 2차 필터.
_ETF_NAME_HINTS = (
    "KODEX", "TIGER", "RISE", "HANARO", "ACE ", "SOL ", "PLUS ", "KOSEF", "ARIRANG",
    "TIMEFOLIO", "1Q ", "KBSTAR", "ETN", "레버리지", "인버스", "선물", "스팩", "액티브",
)


def is_etf_like(name: str | None) -> bool:
    """ETF·ETN·스팩처럼 개별 종목 분석 대상이 아닌 이름인지."""
    if not name:
        return False
    upper = name.upper()
    return any(h.upper() in upper for h in _ETF_NAME_HINTS)


def get_volume_rank(
    min_price: int = 0,
    max_price: int = 1000000,
    min_volume: int = 100000,
    exclude_noise: bool = True,
) -> list[dict[str, Any]]:
    """거래량 순위 (FHPST01710000)."""
    res = _checked(
        _kis_request(f"{Q}/volume-rank", "FHPST01710000", {
            "FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20171",
            "FID_INPUT_ISCD": "0000", "FID_DIV_CLS_CODE": "0", "FID_BLNG_CLS_CODE": "0",
            "FID_TRGT_CLS_CODE": "111111111",
            "FID_TRGT_EXLS_CLS_CODE": EXCLUDE_NOISE if exclude_noise else EXCLUDE_NONE,
            "FID_INPUT_PRICE_1": str(min_price), "FID_INPUT_PRICE_2": str(max_price),
            "FID_VOL_CNT": str(min_volume), "FID_INPUT_DATE_1": "",
        }),
        "FHPST01710000",
    )
    return [{
        "rank": i(r.get("data_rank")),
        "ticker": r.get("mksc_shrn_iscd"),
        "name": r.get("hts_kor_isnm"),
        "price": f(r.get("stck_prpr")),
        "change_pct": f(r.get("prdy_ctrt")),
        "volume": i(r.get("acml_vol")),
        "prev_volume": i(r.get("prdy_vol")),
        "volume_increase_pct": f(r.get("vol_inrt")),
        "turnover_rate": f(r.get("vol_tnrt")),
    } for r in _rows(res, "output")]


def get_fluctuation_rank(
    rank_sort: str = "0",
    min_volume: int = 100000,
    exclude_noise: bool = True,
    days: int = 0,
) -> list[dict[str, Any]]:
    """
    등락률 순위 (FHPST01700000). rank_sort 0:상승 1:하락.
    days는 누적일수(FID_INPUT_CNT_1) — 0:당일, N:최근 N일 누적 등락률 순위.
    주말에 days=5로 조회하면 직전 주 누적 급등주가 나온다.

    한투가 돌려주는 순서가 등락률 기준으로 정렬돼 있지 않아서
    (상승 조회인데 8%가 1위, 30%가 2위로 오는 식) 여기서 다시 정렬한다.
    """
    days = max(0, days)
    res = _checked(
        _kis_request(f"{RANK}/fluctuation", "FHPST01700000", {
            "FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20170",
            "FID_INPUT_ISCD": "0000", "FID_RANK_SORT_CLS_CODE": rank_sort,
            "FID_INPUT_CNT_1": str(days), "FID_PRC_CLS_CODE": "0",
            "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "", "FID_VOL_CNT": str(min_volume),
            "FID_TRGT_CLS_CODE": "0", "FID_TRGT_EXLS_CLS_CODE": "0",
            "FID_DIV_CLS_CODE": "0", "FID_RSFL_RATE1": "", "FID_RSFL_RATE2": "",
        }),
        "FHPST01700000",
    )
    items = [{
        "ticker": r.get("stck_shrn_iscd"),
        "name": r.get("hts_kor_isnm"),
        "price": f(r.get("stck_prpr")),
        "change_pct": f(r.get("prdy_ctrt")),
        "volume": i(r.get("acml_vol")),
        "high": f(r.get("stck_hgpr")),
        "low": f(r.get("stck_lwpr")),
        "pct_from_low": f(r.get("lwpr_vrss_prpr_rate")),
        "consecutive_up_days": i(r.get("cnnt_ascn_dynu")),
    } for r in _rows(res, "output")]

    if exclude_noise:
        items = [it for it in items if not is_etf_like(it["name"])]
    if days == 0:
        # change_pct는 전일 대비라서, 누적 조회(days>0)에서는 이 값으로
        # 재정렬하면 누적 순위가 깨진다. 그때는 한투 응답 순서를 유지한다.
        items.sort(key=lambda x: (x["change_pct"] is None, x["change_pct"] or 0),
                   reverse=(rank_sort == "0"))
    for n, it in enumerate(items, 1):
        it["rank"] = n
    return items


def get_volume_power_rank(
    min_price: int = 0,
    max_price: int = 1000000,
    min_volume: int = 100000,
    exclude_noise: bool = True,
) -> list[dict[str, Any]]:
    """
    체결강도 상위 (FHPST01680000). 매수 체결이 매도보다 얼마나 강한지.

    이 API는 제외 마스크를 지원하지 않아서, 거래량 하한과 이름 기반 필터로
    거래 없는 스팩·ETN이 상위를 차지하는 것을 막는다.
    """
    res = _checked(
        _kis_request(f"{RANK}/volume-power", "FHPST01680000", {
            "FID_TRGT_EXLS_CLS_CODE": "0", "FID_COND_MRKT_DIV_CODE": "J",
            "FID_COND_SCR_DIV_CODE": "20168", "FID_INPUT_ISCD": "0000",
            "FID_DIV_CLS_CODE": "0", "FID_INPUT_PRICE_1": str(min_price),
            "FID_INPUT_PRICE_2": str(max_price), "FID_VOL_CNT": str(min_volume),
            "FID_TRGT_CLS_CODE": "0",
        }),
        "FHPST01680000",
    )
    items = [{
        "ticker": r.get("stck_shrn_iscd"),
        "name": r.get("hts_kor_isnm"),
        "price": f(r.get("stck_prpr")),
        "change_pct": f(r.get("prdy_ctrt")),
        "volume": i(r.get("acml_vol")),
        "volume_power": f(r.get("tday_rltv")),
        "buy_volume": i(r.get("shnu_cnqn_smtn")),
        "sell_volume": i(r.get("seln_cnqn_smtn")),
    } for r in _rows(res, "output")]
    if exclude_noise:
        items = [it for it in items if not is_etf_like(it["name"])]
    for n, it in enumerate(items, 1):
        it["rank"] = n
    return items


def get_vi_status() -> list[dict[str, Any]]:
    """VI(변동성완화장치) 발동 종목 (FHPST01390000). 장중에만 값이 있다."""
    res = _checked(
        _kis_request(f"{Q}/inquire-vi-status", "FHPST01390000", {
            "FID_DIV_CLS_CODE": "0", "FID_COND_SCR_DIV_CODE": "20139",
            "FID_MRKT_CLS_CODE": "0", "FID_INPUT_ISCD": "",
            "FID_RANK_SORT_CLS_CODE": "0", "FID_INPUT_DATE_1": "",
            "FID_TRGT_CLS_CODE": "", "FID_TRGT_EXLS_CLS_CODE": "",
        }),
        "FHPST01390000",
    )
    return [{
        "ticker": r.get("stck_shrn_iscd"),
        "name": r.get("hts_kor_isnm"),
        "price": f(r.get("stck_prpr")),
        "vi_type": r.get("vi_cls_code"),
        "trigger_time": r.get("vi_trgr_hour") or r.get("vi_hour"),
        "release_time": r.get("vi_rlse_hour"),
    } for r in _rows(res, "output")]


# ---------------------------------------------------------------- 수급

def get_foreign_institution_total() -> list[dict[str, Any]]:
    """
    외국인·기관 매매종목 가집계 (FHPTJ04400000).
    확정치가 아닌 장중 추정이라 '오늘 누가 담고 있나'를 볼 수 있는 유일한 창구다.
    """
    res = _checked(
        _kis_request(f"{Q}/foreign-institution-total", "FHPTJ04400000", {
            "FID_COND_MRKT_DIV_CODE": "V", "FID_COND_SCR_DIV_CODE": "16449",
            "FID_INPUT_ISCD": "0000", "FID_DIV_CLS_CODE": "0",
            "FID_RANK_SORT_CLS_CODE": "0", "FID_ETC_CLS_CODE": "0",
        }),
        "FHPTJ04400000",
    )
    return [{
        "ticker": r.get("mksc_shrn_iscd"),
        "name": r.get("hts_kor_isnm"),
        "price": f(r.get("stck_prpr")),
        "change_pct": f(r.get("prdy_ctrt")),
        "volume": i(r.get("acml_vol")),
        "net_qty_total": i(r.get("ntby_qty")),
        "foreign_net_qty": i(r.get("frgn_ntby_qty")),
        "institution_net_qty": i(r.get("orgn_ntby_qty")),
        "investment_trust_net_qty": i(r.get("ivtr_ntby_qty")),
        "pension_net_qty": i(r.get("fund_ntby_qty")),
        "insurance_net_qty": i(r.get("insu_ntby_qty")),
    } for r in _rows(res, "output")]


def get_investor_trend_estimate(ticker_code: str) -> list[dict[str, Any]]:
    """종목별 외국인·기관 추정 가집계 (HHPTJ04160200). 시간대 구분별."""
    res = _checked(
        _kis_request(f"{Q}/investor-trend-estimate", "HHPTJ04160200",
                     {"MKSC_SHRN_ISCD": ticker_code}),
        "HHPTJ04160200",
    )
    return [{
        "session_slot": r.get("bsop_hour_gb"),
        "foreign_est_net_qty": i(r.get("frgn_fake_ntby_qty")),
        "institution_est_net_qty": i(r.get("orgn_fake_ntby_qty")),
        "total_est_net_qty": i(r.get("sum_fake_ntby_qty")),
    } for r in _rows(res, "output2", "output1", "output")]


def get_member_trend(ticker_code: str) -> dict[str, Any]:
    """
    회원사(증권사 창구)별 매매 동향 (FHKST01010600).
    JP모간·골드만 같은 외국계 창구가 매수 상위에 있으면 외국인 자금으로 해석한다.
    """
    res = _checked(
        _kis_request(f"{Q}/inquire-member", "FHKST01010600",
                     {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker_code}),
        "FHKST01010600",
    )
    o = _one(res)
    sell, buy = [], []
    for n in range(1, 6):
        if o.get(f"seln_mbcr_name{n}"):
            sell.append({
                "rank": n,
                "broker": o.get(f"seln_mbcr_name{n}"),
                "quantity": i(o.get(f"total_seln_qty{n}")),
            })
        if o.get(f"shnu_mbcr_name{n}"):
            buy.append({
                "rank": n,
                "broker": o.get(f"shnu_mbcr_name{n}"),
                "quantity": i(o.get(f"total_shnu_qty{n}")),
            })
    return {"top_sell_brokers": sell, "top_buy_brokers": buy}


def get_foreign_member_trend(ticker_code: str, min_volume: str = "1000") -> dict[str, Any]:
    """외국계 회원사 매매 추이 (FHPST04320000)."""
    res = _checked(
        _kis_request(f"{Q}/frgnmem-trade-trend", "FHPST04320000", {
            "FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20432",
            "FID_INPUT_ISCD": ticker_code, "FID_INPUT_ISCD_2": "99999",
            "FID_MRKT_CLS_CODE": "A", "FID_VOL_CNT": min_volume,
        }),
        "FHPST04320000",
    )
    head = _one(res, "output1")
    rows = _rows(res, "output2")
    return {
        "total_sell_qty": i(head.get("total_seln_qty")),
        "total_buy_qty": i(head.get("total_shnu_qty")),
        "recent": [{
            "time": r.get("bsop_hour"),
            "broker": r.get("mbcr_name"),
            "name": r.get("hts_kor_isnm"),
            "price": f(r.get("stck_prpr")),
            "trade_volume": i(r.get("cntg_vol")),
            "cumulative_net_qty": i(r.get("acml_ntby_qty")),
            "global_net_qty": i(r.get("glob_ntby_qty")),
        } for r in rows[:20]],
    }


def get_program_trade(ticker_code: str) -> list[dict[str, Any]]:
    """종목별 프로그램매매 추이 (FHPPG04650101). 시간대별."""
    res = _checked(
        _kis_request(f"{Q}/program-trade-by-stock", "FHPPG04650101",
                     {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker_code}),
        "FHPPG04650101",
    )
    return [{
        "time": r.get("bsop_hour"),
        "price": f(r.get("stck_prpr")),
        "change_pct": f(r.get("prdy_ctrt")),
        "program_sell_qty": i(r.get("whol_smtn_seln_vol")),
        "program_buy_qty": i(r.get("whol_smtn_shnu_vol")),
        "program_net_qty": i(r.get("whol_smtn_ntby_qty")),
        "program_net_amt": i(r.get("whol_smtn_ntby_tr_pbmn")),
    } for r in _rows(res, "output")]


def get_daily_short_sale(ticker_code: str, days: int = 30) -> dict[str, Any]:
    """일별 공매도 추이 (FHPST04830000)."""
    end = datetime.now()
    start = end - timedelta(days=days * 2)
    res = _checked(
        _kis_request(f"{Q}/daily-short-sale", "FHPST04830000", {
            "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker_code,
            "FID_INPUT_DATE_1": _ymd(start), "FID_INPUT_DATE_2": _ymd(end),
        }),
        "FHPST04830000",
    )
    rows = _rows(res, "output2")
    daily = [{
        "date": r.get("stck_bsop_date"),
        "close": f(r.get("stck_clpr")),
        "change_pct": f(r.get("prdy_ctrt")),
        "volume": i(r.get("acml_vol")),
        "short_qty": i(r.get("ssts_cntg_qty")),
        "short_ratio_pct": f(r.get("ssts_vol_rlim")),
        "cumulative_short_qty": i(r.get("acml_ssts_cntg_qty")),
        "cumulative_short_ratio_pct": f(r.get("acml_ssts_cntg_qty_rlim")),
        "short_amt": i(r.get("ssts_tr_pbmn")),
    } for r in rows][:days]
    ratios = [d["short_ratio_pct"] for d in daily if d["short_ratio_pct"] is not None]
    return {
        "daily": daily,
        "latest_short_ratio_pct": ratios[0] if ratios else None,
        "avg_short_ratio_pct": round(sum(ratios) / len(ratios), 2) if ratios else None,
        "note": "short_ratio_pct는 당일 거래량 중 공매도 비중(%)입니다.",
    }


def get_daily_loan_trans(ticker_code: str, days: int = 30) -> list[dict[str, Any]]:
    """
    일별 대차(신용융자) 잔고 추이 (HHPST074500C0).
    잔고가 급증하면 반대매매 물량 리스크를 같이 봐야 한다.

    MRKT_DIV_CLS_CODE는 1:코스피, 2:코스닥, 3:종목이다.
    3이 아니면 종목코드를 넣어도 시장 전체 집계가 돌아온다.
    """
    end = datetime.now()
    start = end - timedelta(days=days * 2)
    res = _checked(
        _kis_request(f"{Q}/daily-loan-trans", "HHPST074500C0", {
            "MRKT_DIV_CLS_CODE": "3", "MKSC_SHRN_ISCD": ticker_code,
            "START_DATE": _ymd(start), "END_DATE": _ymd(end), "CTS": "",
        }),
        "HHPST074500C0",
    )
    return [{
        "date": r.get("bsop_date"),
        "price": f(r.get("stck_prpr")),
        "change_pct": f(r.get("prdy_ctrt")),
        "new_loan_qty": i(r.get("new_stcn")),
        "repaid_qty": i(r.get("rdmp_stcn")),
        "balance_change": i(r.get("prdy_rmnd_vrss")),
        "balance_qty": i(r.get("rmnd_stcn")),
        "balance_amt": i(r.get("rmnd_amt")),
    } for r in _rows(res, "output1", "output")][:days]


def get_investor_by_stock_daily(ticker_code: str, days: int = 20) -> list[dict[str, Any]]:
    """
    종목별 투자자 일별 매매 (FHPTJ04160001).
    기존 inquire-investor보다 상세하다. 외국인 등록/미등록, 연기금·투신 등이 분리된다.
    """
    res = _checked(
        _kis_request(f"{Q}/investor-trade-by-stock-daily", "FHPTJ04160001", {
            "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker_code,
            "FID_INPUT_DATE_1": "", "FID_ORG_ADJ_PRC": "", "FID_ETC_CLS_CODE": "",
        }),
        "FHPTJ04160001",
    )
    return [{
        "date": r.get("stck_bsop_date"),
        "close": f(r.get("stck_clpr")),
        "change_pct": f(r.get("prdy_ctrt")),
        "volume": i(r.get("acml_vol")),
        "foreign_net_qty": i(r.get("frgn_ntby_qty")),
        "foreign_registered_net_qty": i(r.get("frgn_reg_ntby_qty")),
        "foreign_unregistered_net_qty": i(r.get("frgn_nreg_ntby_qty")),
        "individual_net_qty": i(r.get("prsn_ntby_qty")),
        "institution_net_qty": i(r.get("orgn_ntby_qty")),
        "securities_net_qty": i(r.get("scrt_ntby_qty")),
    } for r in _rows(res, "output2")][:days]


# ---------------------------------------------------------------- 재료

def get_news_title(ticker_code: str = "", limit: int = 30) -> list[dict[str, Any]]:
    """국내 뉴스 속보 제목 (FHKST01011800). 종목코드가 연결돼 온다."""
    res = _checked(
        _kis_request(f"{Q}/news-title", "FHKST01011800", {
            "FID_NEWS_OFER_ENTP_CODE": "", "FID_COND_MRKT_CLS_CODE": "",
            "FID_INPUT_ISCD": ticker_code, "FID_TITL_CNTT": "",
            "FID_INPUT_DATE_1": "", "FID_INPUT_HOUR_1": "",
            "FID_RANK_SORT_CLS_CODE": "", "FID_INPUT_SRNO": "",
        }),
        "FHKST01011800",
    )
    out = []
    for r in _rows(res, "output")[:limit]:
        d, t = r.get("data_dt") or "", r.get("data_tm") or ""
        codes = [r.get(f"iscd{n}") for n in range(1, 11)]
        names = [r.get(f"kor_isnm{n}") for n in range(1, 11)]
        out.append({
            "title": r.get("hts_pbnt_titl_cntt"),
            "source": r.get("dorg"),
            "published": f"{d[:4]}-{d[4:6]}-{d[6:8]} {t[:2]}:{t[2:4]}" if len(d) == 8 else None,
            "related_tickers": [c for c in codes if c and c.strip()],
            "related_names": [n.strip() for n in names if n and n.strip()],
        })
    return out


def get_invest_opinion(ticker_code: str, months: int = 6) -> list[dict[str, Any]]:
    """증권사 투자의견·목표주가 (FHKST663300C0)."""
    end = datetime.now()
    start = end - timedelta(days=months * 31)
    res = _checked(
        _kis_request(f"{Q}/invest-opinion", "FHKST663300C0", {
            "FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "16633",
            "FID_INPUT_ISCD": ticker_code,
            "FID_INPUT_DATE_1": _ymd(start), "FID_INPUT_DATE_2": _ymd(end),
        }),
        "FHKST663300C0",
    )
    return [{
        "date": r.get("stck_bsop_date"),
        "broker": r.get("mbcr_name"),
        "opinion": r.get("invt_opnn"),
        "prev_opinion": r.get("rgbf_invt_opnn"),
        "target_price": f(r.get("hts_goal_prc")),
        "close_at_report": f(r.get("stck_prdy_clpr")),
        "gap_pct": f(r.get("dprt")),
    } for r in _rows(res, "output")]


def get_estimate_perform(ticker_code: str) -> dict[str, Any]:
    """실적 예상(컨센서스) (HHKST668300C0)."""
    res = _checked(
        _kis_request(f"{Q}/estimate-perform", "HHKST668300C0", {"SHT_CD": ticker_code}),
        "HHKST668300C0",
    )
    head = _one(res, "output1")
    return {
        "ticker": head.get("sht_cd"),
        "name": head.get("item_kor_nm"),
        "analyst": head.get("name1") or None,
        "estimate_date": head.get("estdate"),
        "recommendation": head.get("rcmd_name"),
        "rows": _rows(res, "output2"),
        "note": "output2의 data1~data5는 한투가 정의한 연도별 추정 항목입니다. 원본 그대로 전달합니다.",
    }
