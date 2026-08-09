"""
US Market Data MCP 서버 설정.
yfinance는 API 키가 필요 없고, 세레니티 아카이브도 공개 URL이라 .env는 선택 사항.
"""
import os

from dotenv import load_dotenv

load_dotenv()

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

CACHE_DIR = os.getenv(
    "US_MCP_CACHE_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache"),
)

# 상대강도(RS) 비교 대상. 시세 조회 1회당 벤치마크도 같이 받으므로 짧게 유지.
BENCHMARKS = [s.strip().upper() for s in os.getenv("BENCHMARKS", "SPY,QQQ").split(",") if s.strip()]

# 캐시태그 오탐 제거용. 통화 기호로 쓰인 $USD 같은 토큰만 걸러낸다.
# 한 글자 티커(F, T, X 등)는 실제 종목이므로 제외하지 않는다.
CASHTAG_STOPLIST = {
    "USD", "EUR", "JPY", "CNY", "KRW", "GBP", "CHF", "AUD", "CAD", "HKD",
    "USDT", "USDC", "BTC", "ETH",
}
