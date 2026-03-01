"""
한국투자증권 Open API REST 래퍼.
현재가, 분봉 차트, 투자자별 매매 동향.
"""
from typing import Any

import httpx

from config import KIS_BASE_URL, KIS_APPKEY, KIS_APPSECRET
from services.token_manager import get_access_token


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
    }
    with httpx.Client() as client:
        r = client.get(url, params=params, headers=headers, timeout=15)
        r.raise_for_status()
        return r.json()


def get_price(ticker_code: str) -> dict[str, Any]:
    """현재가 조회 (FHKST01010100)"""
    return _kis_request(
        "/uapi/domestic-stock/v1/quotations/inquire-price",
        "FHKST01010100",
        {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker_code,
        },
    )


def get_minute_chart(ticker_code: str, interval: str = "15") -> dict[str, Any]:
    """분봉 차트 (FHKST03010200). interval: 1, 3, 5, 10, 15, 30, 60 등"""
    return _kis_request(
        "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
        "FHKST03010200",
        {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker_code,
            "FID_INPUT_HOUR_1": "153000",
            "FID_PW_DATA_INCU_YN": "N",
        },
    )


def get_investor_trend(ticker_code: str) -> dict[str, Any]:
    """투자자별 매매 동향 (외인/기관/개인)"""
    return _kis_request(
        "/uapi/domestic-stock/v1/quotations/inquire-investor",
        "FHKST01010900",
        {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker_code,
        },
    )


def get_daily_chart(
    ticker_code: str,
    period: str = "D",
    start_ymd: str | None = None,
    end_ymd: str | None = None,
) -> dict[str, Any]:
    """일봉 차트. 기간 미지정 시 최근 120일 구간 사용."""
    if not start_ymd or not end_ymd:
        from datetime import datetime, timedelta
        end = datetime.now()
        start = end - timedelta(days=120)
        end_ymd = end.strftime("%Y%m%d")
        start_ymd = start.strftime("%Y%m%d")
    return _kis_request(
        "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
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
