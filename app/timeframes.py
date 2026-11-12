from __future__ import annotations

# 알람 타임프레임. 설정 키는 tf_<id> (예: tf_4h, tf_1mo)
TIMEFRAMES: tuple[str, ...] = ("4h", "1d", "1w", "1mo")

TF_LABEL: dict[str, str] = {
    "4h": "4시간봉",
    "1d": "일봉",
    "1w": "주봉",
    "1mo": "월봉",
}

BINANCE_INTERVAL: dict[str, str] = {
    "4h": "4h",
    "1d": "1d",
    "1w": "1w",
    "1mo": "1M",
}

# 4h는 야후에 없어서 1h를 세션 기준 4개씩 묶어 만든다
YAHOO_INTERVAL: dict[str, str] = {
    "4h": "1h",
    "1d": "1d",
    "1w": "1wk",
    "1mo": "1mo",
}
