"""
US Market Data MCP 서버 설정.

yfinance와 세레니티 아카이브는 키가 필요 없다.
한국투자증권 해외주식 API 키는 선택 사항이며, 있으면 실시간 시세·호가·순위·한글 뉴스를 쓴다.
"""
import os

from dotenv import load_dotenv

_HERE = os.path.dirname(os.path.abspath(__file__))

load_dotenv()

# 한투 키는 mcp-market-data 쪽에 이미 있을 수 있으므로, 자체 .env에 없으면 그쪽을 참조한다.
# (키를 두 군데 중복해서 넣지 않아도 되게 하려는 것)
if not os.getenv("KIS_APPKEY"):
    _sibling = os.path.join(os.path.dirname(_HERE), "mcp-market-data", ".env")
    if os.path.exists(_sibling):
        load_dotenv(_sibling)

KIS_APPKEY = os.getenv("KIS_APPKEY", "")
KIS_APPSECRET = os.getenv("KIS_APPSECRET", "")
KIS_BASE_URL = os.getenv("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443")

# 한투는 토큰 발급을 분당 1회로 제한한다. 프로세스가 재시작되거나 서버 두 개가
# 같은 키를 쓰면 403이 나므로, 토큰을 파일에 캐시해 프로세스·서버 간에 공유한다.
KIS_TOKEN_CACHE = os.getenv(
    "KIS_TOKEN_CACHE",
    os.path.join(
        os.getenv("LOCALAPPDATA") or os.path.expanduser("~"),
        "kis-mcp",
        "token.json",
    ),
)


def kis_enabled() -> bool:
    """한투 키가 있는지. 없으면 yfinance 단독으로 동작한다."""
    return bool(KIS_APPKEY and KIS_APPSECRET)


# 세레니티(@aleabitoreddit) X 게시물 공개 아카이브.
# yan-labs/serenity-aleabitoreddit 저장소가 자동 갱신하는 CSV를 그대로 받아 쓴다.
SERENITY_CSV_URL = os.getenv(
    "SERENITY_CSV_URL",
    "https://raw.githubusercontent.com/yan-labs/serenity-aleabitoreddit/main/data/aleabitoreddit_tweets.csv",
)
SERENITY_SYNC_STATE_URL = os.getenv(
    "SERENITY_SYNC_STATE_URL",
    "https://raw.githubusercontent.com/yan-labs/serenity-aleabitoreddit/main/data/sync_state.json",
)

# 아카이브가 4~5MB라 매 호출마다 받지 않고 캐시한다. 기본 1시간.
SERENITY_CACHE_TTL_SEC = int(os.getenv("SERENITY_CACHE_TTL_SEC", "3600"))

CACHE_DIR = os.getenv("US_MCP_CACHE_DIR", os.path.join(_HERE, ".cache"))

# 상대강도(RS) 비교 대상. 시세 조회 1회당 벤치마크도 같이 받으므로 짧게 유지.
BENCHMARKS = [s.strip().upper() for s in os.getenv("BENCHMARKS", "SPY,QQQ").split(",") if s.strip()]

# 캐시태그 오탐 제거용. 통화 기호로 쓰인 $USD 같은 토큰만 걸러낸다.
# 한 글자 티커(F, T, X 등)는 실제 종목이므로 제외하지 않는다.
CASHTAG_STOPLIST = {
    "USD", "EUR", "JPY", "CNY", "KRW", "GBP", "CHF", "AUD", "CAD", "HKD",
    "USDT", "USDC", "BTC", "ETH",
}

# 한투 해외 거래소 코드. 티커가 어느 거래소인지 모를 때 이 순서로 조회한다.
KIS_EXCHANGES = ["NAS", "NYS", "AMS"]
