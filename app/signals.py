from __future__ import annotations

from dataclasses import dataclass, field

from app.config import MA_LINES, AppConfig, ma_label
from app.indicators import calc_ema, calc_rsi, calc_sma, calc_vwma
from app.series import Asset, Bar
from app.timeframes import TF_LABEL


@dataclass
class Snapshot:
    price: float
    rsi: float | None
    # ma50 / ma200 / ma100 → 값 (데이터 부족·거래량 없음이면 None)
    lines: dict[str, float | None] = field(default_factory=dict)
    # ma50 → "EMA50" 처럼 실제 계산한 선 이름
    labels: dict[str, str] = field(default_factory=dict)

    def dist_pct(self, line_key: str) -> float | None:
        ma = self.lines.get(line_key)
        if ma is None or ma <= 0:
            return None
        return (self.price - ma) / ma * 100.0


def compute_snapshot(bars: list[Bar], cfg: AppConfig) -> Snapshot | None:
    if not bars:
        return None
    closes = [b.close for b in bars]
    volumes = [b.volume for b in bars]
    lines: dict[str, float | None] = {}
    labels: dict[str, str] = {}
    for key, period in MA_LINES:
        ma_type = cfg.ma_alert.line(key).type
        if ma_type == "ema":
            lines[key] = calc_ema(closes, period)
        elif ma_type == "sma":
            lines[key] = calc_sma(closes, period)
        else:
            lines[key] = calc_vwma(closes, volumes, period)
        labels[key] = ma_label(ma_type, period)
    return Snapshot(
        price=closes[-1],
        rsi=calc_rsi(closes, cfg.rsi_alert.period),
        lines=lines,
        labels=labels,
    )


class AlertEngine:
    """
    이전 관측값과 비교해 '진입 순간'만 알람.
    - 같은 봉에서 같은 방향은 1회만
    - 알람 후에는 기준에서 충분히 멀어져야(재무장) 다시 울림 → 경계선 잔진동 방지
    - 기동 직후 첫 관측은 기준값만 기록 (이미 과매수/근접 상태인 종목은 알람 안 함)
    """

    def __init__(self) -> None:
        self._prev: dict[tuple[str, str, str], float] = {}
        self._armed: dict[tuple[str, str, str, str], bool] = {}
        self._alerted_bar: dict[tuple[str, str, str, str], int] = {}

    def forget(self, asset_key: str) -> None:
        for d in (self._prev, self._armed, self._alerted_bar):
            for k in [k for k in d if k[0] == asset_key]:
                del d[k]

    def evaluate(self, asset: Asset, tf: str, bars: list[Bar], cfg: AppConfig) -> list[str]:
        snap = compute_snapshot(bars, cfg)
        if snap is None:
            return []
        bar_ot = bars[-1].open_time
        messages: list[str] = []

        ra = cfg.rsi_alert
        rtf = ra.tf(tf)
        if snap.rsi is not None:
            rsi = snap.rsi
            prev = self._swap((asset.key, tf, "rsi"), rsi)
            active = ra.enabled and rtf.enabled
            ob, os_ = rtf.overbought, rtf.oversold
            if self._trigger(
                (asset.key, tf, "rsi", "overbought"),
                outside=rsi < ob,
                rearmed=rsi <= ob - ra.rearm_gap,
                hit=prev is not None and prev < ob <= rsi,
                enabled=active and rtf.overbought_enabled,
                bar_ot=bar_ot,
            ):
                messages.append(_rsi_message(asset, tf, "과매수", prev, rsi, ob, "상향", snap.price, ra.period))
            if self._trigger(
                (asset.key, tf, "rsi", "oversold"),
                outside=rsi > os_,
                rearmed=rsi >= os_ + ra.rearm_gap,
                hit=prev is not None and prev > os_ >= rsi,
                enabled=active and rtf.oversold_enabled,
                bar_ot=bar_ot,
            ):
                messages.append(_rsi_message(asset, tf, "과매도", prev, rsi, os_, "하향", snap.price, ra.period))

        ma = cfg.ma_alert
        for key, _period in MA_LINES:
            dist = snap.dist_pct(key)
            if dist is None:
                continue
            label = snap.labels[key]
            line_cfg = ma.line(key)
            # 상태 키에 선 종류를 넣어 EMA↔MA 전환 직후 값이 튀어도 알람이 나지 않게 함
            state = f"{key}:{line_cfg.type}"
            prev = self._swap((asset.key, tf, state), dist)
            tcfg = line_cfg.tf(tf)
            active = ma.enabled and line_cfg.enabled and tcfg.enabled
            p = tcfg.proximity_pct
            far = p * ma.rearm_mult
            line_value = snap.lines[key]
            if self._trigger(
                (asset.key, tf, state, "above"),
                outside=dist > p,
                rearmed=dist >= far,
                hit=prev is not None and prev > p and dist <= p,
                enabled=active and tcfg.from_above,
                bar_ot=bar_ot,
            ):
                messages.append(_ma_message(asset, tf, label, "위에서", prev, dist, p, snap.price, line_value))
            if self._trigger(
                (asset.key, tf, state, "below"),
                outside=dist < -p,
                rearmed=dist <= -far,
                hit=prev is not None and prev < -p and dist >= -p,
                enabled=active and tcfg.from_below,
                bar_ot=bar_ot,
            ):
                messages.append(_ma_message(asset, tf, label, "아래에서", prev, dist, p, snap.price, line_value))
        return messages

    def _swap(self, key: tuple[str, str, str], value: float) -> float | None:
        prev = self._prev.get(key)
        self._prev[key] = value
        return prev

    def _trigger(
        self,
        key: tuple[str, str, str, str],
        *,
        outside: bool,
        rearmed: bool,
        hit: bool,
        enabled: bool,
        bar_ot: int,
    ) -> bool:
        """outside: 첫 관측 때 무장 여부, rearmed: 알람 후 다시 무장되는 거리, hit: 이번 관측에서 진입."""
        armed = self._armed.get(key)
        if armed is None:
            armed = outside
        if rearmed:
            armed = True
        fire = hit and armed and enabled and self._alerted_bar.get(key) != bar_ot
        if fire:
            armed = False
            self._alerted_bar[key] = bar_ot
        self._armed[key] = armed
        return fire


def _rsi_message(
    asset: Asset,
    tf: str,
    zone: str,
    prev: float,
    rsi: float,
    level: float,
    direction: str,
    price: float,
    period: int,
) -> str:
    return (
        f"[RSI {zone}] {TF_LABEL[tf]}\n"
        f"{asset.display} · {asset.source_label}\n"
        f"RSI({period}) {prev:.1f} → {rsi:.1f} (기준 {level:g} {direction} 돌파)\n"
        f"가격 {fmt_price(price)}"
    )


def _ma_message(
    asset: Asset,
    tf: str,
    label: str,
    side: str,
    prev: float,
    dist: float,
    proximity: float,
    price: float,
    line_value: float | None,
) -> str:
    crossed = ""
    if side == "위에서" and dist < -proximity:
        crossed = " · 밴드 관통(하향 돌파)"
    elif side == "아래에서" and dist > proximity:
        crossed = " · 밴드 관통(상향 돌파)"
    return (
        f"[{label} 근접 · {side}] {TF_LABEL[tf]}\n"
        f"{asset.display} · {asset.source_label}\n"
        f"이격 {prev:+.2f}% → {dist:+.2f}% (근접 ±{proximity:g}%){crossed}\n"
        f"가격 {fmt_price(price)} / {label} {fmt_price(line_value or 0.0)}"
    )


def fmt_price(price: float) -> str:
    a = abs(price)
    if a >= 1000:
        return f"{price:,.2f}"
    if a >= 1:
        return f"{price:,.4f}"
    return f"{price:.6g}"
