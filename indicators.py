# -*- coding: utf-8 -*-
"""지표 계산 단일 소스 (2026-08-12 신규).

설계 근거: 지표계산_단일화_설계안_20260812.md
감사 근거: 선물_전략전수분석_및_사이징검증_종합_20260811.md §(a)

같은 이름의 지표가 파일마다 다르게 계산되던 문제(불일치 11건)를 한 곳으로 모은다.

핵심 규약 넷
-----------
1. **지표 함수는 인덱스를 받지 않는다.** 이미 잘린 ``Window``만 받으므로 ``arr[i]``를
   쓸 방법이 구조적으로 없다. 미래참조 5건이 전부 "배열+인덱스를 넘기고 내부에서
   자르는" 패턴에서 나왔다.
2. **데이터 공급은 ``FeedSpec`` 파라미터.** 갭 처리 A/B/C1 중 어느 것도 코드에 고정하지 않는다.
3. **``Series.space``로 pt 공간과 실제가격 공간을 구분.** back-adjust된 가격이 밴드폭
   분모에 들어가면 예외를 던진다(실측 3.8배 왜곡).
4. **SAR AF 가속은 ``on_bar``가 기본, ``on_tick``은 별도 메서드.** 호출부에 그대로 드러난다.

에러 처리 정책
-------------
- **데이터 부족**(워밍업 등): ``None`` 또는 NaN 배열 반환. 정상 상황이므로 예외로 만들지 않는다.
- **오용**(공간 태그 불일치, 창 길이 모순): ``ValueError``. 조용히 틀린 값을 내는 것보다 낫다.
- **NaN 입력**: numpy 의미론대로 전파. 단 ``kalman_residual_std``만 라이브와 동일하게
  비정상 결과를 0.5로 폴백한다(era_order_manager.py:1155-1157).

이 모듈은 라이브·백테스터를 아직 대체하지 않는다. (d) 마이그레이션에서 하나씩 갈아끼운다.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal, Optional, Sequence

import numpy as np

Space = Literal["actual", "adjusted"]
Unit = Literal["pt", "ratio", "index"]

__all__ = [
    "Window", "Series", "FeedSpec", "PriceFrame", "SarState",
    "FEED_A", "FEED_B", "FEED_C1",
    "kalman_path", "kalman_residual_std", "kalman_atr",
    "true_range", "intraday_range",
    "bollinger", "bandwidth", "squeeze_threshold", "percent_b",
    "bollinger_series", "bandwidth_series", "moving_average_series",
    "rolling_quantile", "percent_b_series",
    "moving_average", "wilder_adx", "wilder_rsi",
    "back_adjust", "filter_session", "merge_night",
    "breakout_targets", "kalman_band_targets", "prev_session_range",
    "ta_ema_series", "ta_rsi_series", "ta_macd_series", "ta_atr_series",
]


# ─────────────────────────────────────────────────────────────────────────────
# 1. 타입
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Window:
    """확정된 봉만 담는 불변 뷰.

    생성 시점에 '현재 진행 중인 봉'은 이미 제외되어 있다. 지표 함수는 이것만 받으므로
    인덱스를 넘길 방법이 없고, 따라서 미래참조가 구조적으로 불가능하다.
    """
    values: np.ndarray
    space: Space = "actual"

    def __post_init__(self):
        v = np.asarray(self.values, dtype=float)
        object.__setattr__(self, "values", v)
        if v.ndim != 1:
            raise ValueError("Window.values는 1차원이어야 합니다 (받은 차원: %d)" % v.ndim)

    def __len__(self) -> int:
        return len(self.values)

    @staticmethod
    def closed_upto(arr: Sequence[float], upto_exclusive: int, size: int,
                    space: Space = "actual") -> Optional["Window"]:
        """``arr[upto_exclusive-size : upto_exclusive]``.

        이름이 배타적(exclusive)임을 드러낸다 — ``upto_exclusive``번째 봉은 **포함되지 않는다**.
        백테스트에서 ``i``번째 봉을 판정할 때 ``closed_upto(closes, i, 40)``을 쓰면
        ``closes[i]``가 창에 들어가지 않는다.

        데이터가 모자라면 None(예외 아님 — 워밍업은 정상 상황).
        """
        a = np.asarray(arr, dtype=float)
        if size <= 0:
            raise ValueError("size는 양수여야 합니다")
        start = upto_exclusive - size
        if start < 0 or upto_exclusive > len(a):
            return None
        return Window(a[start:upto_exclusive], space)


@dataclass(frozen=True)
class Series:
    """공간·단위 태그가 붙은 시계열. back-adjust 오용을 런타임에 잡기 위한 장치."""
    values: np.ndarray
    space: Space = "actual"
    unit: Unit = "pt"

    def __post_init__(self):
        object.__setattr__(self, "values", np.asarray(self.values, dtype=float))

    def __len__(self) -> int:
        return len(self.values)

    def as_actual(self, offset: np.ndarray, mode: str = "subtract") -> "Series":
        """조정 공간 → 실제 가격 공간 환원."""
        if self.space == "actual":
            return self
        v = self.values + offset if mode == "subtract" else self.values * offset
        return Series(v, "actual", self.unit)


@dataclass(frozen=True)
class FeedSpec:
    """지표에 어떤 봉을 먹일지에 대한 계약. 갭 처리 A/B/C1을 전부 표현한다."""
    session: Optional[tuple] = ("09:00", "15:35")
    merge_night: bool = False
    night_code: Optional[str] = None
    night_cutoff_hours: int = 36
    night_resample: str = "5min"
    back_adjust: Optional[Literal["subtract", "ratio"]] = None
    warmup_bars: int = 200


FEED_A = FeedSpec(session=None, merge_night=True, night_code="A05608", back_adjust=None)
FEED_B = FeedSpec(session=("09:00", "15:35"), merge_night=False, back_adjust=None)
FEED_C1 = FeedSpec(session=("09:00", "15:35"), merge_night=False, back_adjust="subtract")


@dataclass
class PriceFrame:
    """``build_feed`` 결과. 조정 공간과 실제 공간을 함께 들고 다닌다."""
    index: np.ndarray
    opens: np.ndarray
    highs: np.ndarray
    lows: np.ndarray
    closes: Series          # space가 spec.back_adjust에 따라 결정됨
    session_ids: np.ndarray
    offset: np.ndarray      # 실제가격 = closes.values + offset (subtract) / * offset (ratio)
    spec: FeedSpec
    actual_closes: np.ndarray = field(default=None)

    def to_actual(self, values: np.ndarray) -> np.ndarray:
        if self.closes.space == "actual":
            return np.asarray(values, dtype=float)
        if self.spec.back_adjust == "ratio":
            return np.asarray(values, dtype=float) * self.offset
        return np.asarray(values, dtype=float) + self.offset


# ─────────────────────────────────────────────────────────────────────────────
# 2. 칼만 (전체 재계산 — 설계 D-4)
# ─────────────────────────────────────────────────────────────────────────────
def kalman_path(w: Window, q: float, r: float) -> np.ndarray:
    """1차원 칼만 평활 경로.

    기준: era_order_manager.py:1131-1144 / backtest_sar_bb_20260809.py:155-167
    (두 곳의 수식이 이미 동일함을 감사에서 확인)
    """
    x, P = None, 1.0
    out = np.empty(len(w), dtype=float)
    for j, z in enumerate(w.values):
        if x is None:
            x = z
        else:
            P = P + q
            K = P / (P + r)
            x = x + K * (z - x)
            P = (1 - K) * P
        out[j] = x
    return out


def kalman_residual_std(w: Window, q: float, r: float, std_window: int = 20,
                        trim: int = 0, fallback: float = 0.5) -> tuple:
    """(std_error, kf_last) 반환.

    기준: era_order_manager.py:1146-1160.
    - 잔차 = 원값 − 평활값
    - 마지막 ``std_window``개에서 |잔차| 최대 ``trim``개 제외
    - ``np.std``(**ddof=0** — numpy 기본. 라이브·백테스트 양쪽 모두 ddof=0)
    - 비정상(NaN 또는 <=0)이면 ``fallback``
    """
    path = kalman_path(w, q, r)
    err = w.values - path
    sl = err[-std_window:] if std_window > 0 else err
    if trim > 0 and len(sl) > trim:
        order = np.argsort(np.abs(sl))
        sl = sl[order[:-trim]]
    s = float(np.std(sl))
    if not np.isfinite(s) or s <= 0:
        s = fallback
    return s, float(path[-1])


def kalman_atr(tr: Sequence[float], q: float = 0.002, r: float = 0.2) -> np.ndarray:
    """TR(또는 일중 레인지) 계열에 칼만 평활을 적용한 ATR 경로.

    기준: era_order_manager.py:861-875 / backtest_sar_bb_20260809.py:57-67
    """
    a = np.asarray(tr, dtype=float)
    x, P = None, 1.0
    out = np.empty(len(a), dtype=float)
    for j, v in enumerate(a):
        if x is None:
            x = v
        else:
            P = P + q
            K = P / (P + r)
            x = x + K * (v - x)
            P = (1 - K) * P
        out[j] = x
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 3. ATR / TR
# ─────────────────────────────────────────────────────────────────────────────
def true_range(high, low, prev_close) -> np.ndarray:
    """TR = max(H−L, |H−prevC|, |L−prevC|). prevC가 NaN인 첫 행은 H−L로 채운다.

    기준: era_order_manager.py:860-862 (오버나잇 갭을 **포함**한다)
    """
    h = np.asarray(high, dtype=float)
    l = np.asarray(low, dtype=float)
    pc = np.asarray(prev_close, dtype=float)
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return np.where(np.isnan(tr), h - l, tr)


def intraday_range(high, low) -> np.ndarray:
    """H−L. 오버나잇 갭을 **제외**한다(방식 C의 ATR)."""
    return np.asarray(high, dtype=float) - np.asarray(low, dtype=float)


# ─────────────────────────────────────────────────────────────────────────────
# 4. 볼린저 / 이동평균
# ─────────────────────────────────────────────────────────────────────────────
def bollinger(w: Window, window: int, sigma: float = 2.0, ddof: int = 1) -> tuple:
    """(mid, upper, lower) 스칼라 반환 — 창의 마지막 시점 값.

    기준: era_order_manager.py:984-988 (pandas rolling().std() = **ddof=1**)
    라이브의 σ 하드코딩 2는 파라미터로 승격(K-2 해소).
    데이터 부족이면 (nan, nan, nan).
    """
    if len(w) < window:
        return (np.nan, np.nan, np.nan)
    seg = w.values[-window:]
    mid = float(np.mean(seg))
    sd = float(np.std(seg, ddof=ddof))
    return (mid, mid + sigma * sd, mid - sigma * sd)


def bandwidth(upper: float, lower: float, mid_actual: Series) -> float:
    """밴드폭 = (upper − lower) / 실제가격 중심선.

    **분모는 반드시 실제 가격 공간이어야 한다.** back-adjust된 중심선을 쓰면 가격 수준이
    낮아져 실측 3.8배 왜곡(8.419% vs 2.188%)이 발생한다.
    """
    if not isinstance(mid_actual, Series):
        raise ValueError("bandwidth의 mid_actual은 Series여야 합니다 (공간 태그 확인용)")
    if mid_actual.space != "actual":
        raise ValueError(
            "밴드폭 분모에는 실제 가격 공간의 중심선이 필요합니다. back-adjust된 "
            "시계열을 넘겼습니다 — frame.to_actual(mid)로 환원하세요."
        )
    m = float(mid_actual.values[-1]) if len(mid_actual) else np.nan
    if not np.isfinite(m) or m == 0:
        return np.nan
    return (upper - lower) / m


def bollinger_series(closes: Sequence[float], window: int, sigma: float = 2.0,
                     ddof: int = 1) -> tuple:
    """``bollinger``의 벡터화 변형 — 전 구간 롤링 배열을 한 번에.

    백테스터는 봉마다 스칼라를 부르는 대신 배열을 미리 만들어 쓴다. 스칼라판을 봉마다
    호출한 것과 **수치가 동일**함을 test_indicators_equivalence가 검증한다.
    (pandas rolling을 그대로 쓰므로 ddof 의미론도 스칼라판과 같다.)
    """
    import pandas as pd
    s = pd.Series(np.asarray(closes, dtype=float))
    mid = s.rolling(window).mean().values
    sd = s.rolling(window).std(ddof=ddof).values
    return mid, mid + sigma * sd, mid - sigma * sd


def bandwidth_series(upper, lower, mid_actual: Series) -> np.ndarray:
    """``bandwidth``의 벡터화 변형. 공간 태그 강제는 동일하게 적용된다."""
    if not isinstance(mid_actual, Series):
        raise ValueError("bandwidth_series의 mid_actual은 Series여야 합니다")
    if mid_actual.space != "actual":
        raise ValueError(
            "밴드폭 분모에는 실제 가격 공간의 중심선이 필요합니다. back-adjust된 "
            "시계열을 넘겼습니다 — frame.to_actual(mid)로 환원하세요."
        )
    with np.errstate(divide="ignore", invalid="ignore"):
        return (np.asarray(upper, dtype=float) - np.asarray(lower, dtype=float)) / mid_actual.values


def moving_average_series(closes: Sequence[float], period: int) -> np.ndarray:
    """``moving_average``의 벡터화 변형."""
    import pandas as pd
    return pd.Series(np.asarray(closes, dtype=float)).rolling(period).mean().values


def rolling_quantile(arr: Sequence[float], window: int, quantile: float) -> np.ndarray:
    """``squeeze_threshold``의 벡터화 변형(롤링 분위수 배열)."""
    import pandas as pd
    return pd.Series(np.asarray(arr, dtype=float)).rolling(window).quantile(quantile).values


def percent_b_series(closes, upper, lower) -> np.ndarray:
    """``percent_b``의 벡터화 변형."""
    c = np.asarray(closes, dtype=float)
    u = np.asarray(upper, dtype=float)
    l = np.asarray(lower, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return (c - l) / (u - l)


def squeeze_threshold(bw_history: Sequence[float], window: int = 100,
                      quantile: float = 0.25) -> float:
    """밴드폭 이력의 분위수. 기준: era_order_manager.py:989"""
    a = np.asarray(bw_history, dtype=float)
    a = a[-window:] if window > 0 else a
    a = a[np.isfinite(a)]
    if len(a) == 0:
        return np.nan
    return float(np.quantile(a, quantile))


def percent_b(price: float, upper: float, lower: float) -> float:
    """%B = (price − lower) / (upper − lower). 기준: backtest_bb_meanrev_20260811.py:115"""
    d = upper - lower
    if not np.isfinite(d) or d == 0:
        return np.nan
    return (price - lower) / d


def moving_average(w: Window, period: int) -> float:
    """창 마지막 시점의 단순이동평균. 기준: era_order_manager.py:999"""
    if len(w) < period:
        return np.nan
    return float(np.mean(w.values[-period:]))


# ─────────────────────────────────────────────────────────────────────────────
# 5. ADX / RSI (Wilder — ta 라이브러리 의존 제거, 설계 D-5)
# ─────────────────────────────────────────────────────────────────────────────
def wilder_adx(high, low, close, window: int = 14) -> np.ndarray:
    """Wilder ADX. 기준: backtest_bb_meanrev_20260811.py:36-62

    ``ta.ADXIndicator``를 대체한다. ta는 내부적으로 ddof=0 표준편차를 쓰는 등
    구현 세부가 달라 국면 모니터와 백테스터가 서로 다른 ADX를 보고 있었다(K-4, N-3).
    """
    h = np.asarray(high, dtype=float)
    l = np.asarray(low, dtype=float)
    c = np.asarray(close, dtype=float)
    n = len(c)
    tr = np.zeros(n); pdm = np.zeros(n); ndm = np.zeros(n)
    if n == 0:
        return np.array([])
    tr[0] = h[0] - l[0]
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1]))
        up, dn = h[i] - h[i-1], l[i-1] - l[i]
        pdm[i] = up if (up > dn and up > 0) else 0.0
        ndm[i] = dn if (dn > up and dn > 0) else 0.0

    def _rma(x):
        o = np.full(n, np.nan)
        if n <= window:
            return o
        o[window] = np.nansum(x[1:window+1]) / window
        for i in range(window+1, n):
            o[i] = (o[i-1] * (window - 1) + x[i]) / window
        return o

    atr_, pd_, nd_ = _rma(tr), _rma(pdm), _rma(ndm)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100 * pd_ / atr_
        ndi = 100 * nd_ / atr_
        dx = 100 * np.abs(pdi - ndi) / (pdi + ndi)
    adx = np.full(n, np.nan)
    valid = np.where(~np.isnan(dx))[0]
    if len(valid) > window:
        st = valid[0] + window
        if st < n:
            adx[st] = np.nanmean(dx[valid[0]:st])
            for i in range(st + 1, n):
                adx[i] = (adx[i-1] * (window - 1) + dx[i]) / window
    return adx


def wilder_rsi(w: Window, window: int = 14) -> np.ndarray:
    """Wilder RSI. 기준: backtest_bb_meanrev_20260811.py:65-77"""
    c = w.values
    n = len(c)
    if n <= window:
        return np.full(n, np.nan)
    d = np.diff(c, prepend=c[0])
    g = np.where(d > 0, d, 0.0)
    l = np.where(d < 0, -d, 0.0)
    ag = np.full(n, np.nan); al = np.full(n, np.nan)
    ag[window] = g[1:window+1].mean()
    al[window] = l[1:window+1].mean()
    for i in range(window+1, n):
        ag[i] = (ag[i-1] * (window - 1) + g[i]) / window
        al[i] = (al[i-1] * (window - 1) + l[i]) / window
    with np.errstate(divide="ignore", invalid="ignore"):
        return 100 - 100 / (1 + ag / al)


# ─────────────────────────────────────────────────────────────────────────────
# 6. Parabolic SAR (상태 객체 — 설계 D-1, D-4)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class SarState:
    """Parabolic SAR 상태.

    AF는 진입 이후 신고가 횟수에 누적 의존하므로 전체 재계산이 불가능하다 → 상태 객체.

    ``on_bar``가 표준 정의이며 **기본**이다. ``on_tick``은 현행 라이브 동작을 표현하기
    위한 **명시적 옵션**으로, 호출부에 ``sar.on_tick(...)``이라고 그대로 드러난다.
    라이브는 틱마다 AF를 올려 5분에 걸쳐 일어날 가속이 수 초 만에 끝나고, 그 결과
    회전율이 백테스트의 18배(22건/일 vs 1.2건/일)가 됐다(K-1).
    """
    sar: float
    ep: float
    af: float = 0.02
    bull: bool = True
    af_init: float = 0.02
    af_step: float = 0.02
    af_max: float = 0.10
    clamp: Optional[float] = None      # era의 futures_day_peak 클램프(사실상 no-op, M-1)

    # ── 갱신 ────────────────────────────────────────────────────────────
    def _advance(self, extreme: float) -> float:
        """SAR 한 스텝. 기준: era_order_manager.py:4457-4461 / 4664-4668
        (클램프 → EP/AF 갱신 순서까지 동일하게 재현)
        """
        if self.bull:
            self.sar = self.sar + self.af * (self.ep - self.sar)
            if self.clamp is not None:
                self.sar = min(self.sar, self.clamp)
            if extreme > self.ep:
                self.ep = extreme
                self.af = min(self.af + self.af_step, self.af_max)
        else:
            self.sar = self.sar - self.af * (self.sar - self.ep)
            if self.clamp is not None:
                self.sar = max(self.sar, self.clamp)
            if extreme < self.ep:
                self.ep = extreme
                self.af = min(self.af + self.af_step, self.af_max)
        return self.sar

    def on_bar(self, high: float, low: float, clamp: Optional[float] = None) -> float:
        """표준 Parabolic SAR — 봉마다 1회. **기본 경로.**"""
        if clamp is not None:
            self.clamp = clamp
        return self._advance(high if self.bull else low)

    def on_tick(self, live_price: float, clamp: Optional[float] = None) -> float:
        """틱마다 갱신 — 비표준. 현행 라이브 동작 재현용 **명시적 옵션.**"""
        if clamp is not None:
            self.clamp = clamp
        return self._advance(live_price)

    def flip(self, entry_price: float, atr: float, bull: bool) -> None:
        """진입 시 초기화. 기준: era_order_manager.py:4936-4940"""
        self.bull = bull
        self.ep = entry_price
        self.af = self.af_init
        self.sar = entry_price - atr if bull else entry_price + atr
        self.clamp = None

    # ── 직렬화 (8단계 상태 영속화와 맞물림) ──────────────────────────────
    def to_dict(self) -> dict:
        return {"sar_value": self.sar, "sar_ep": self.ep, "sar_af": self.af,
                "sar_bull": self.bull, "sar_af_init": self.af_init,
                "sar_af_step": self.af_step, "sar_af_max": self.af_max}

    @staticmethod
    def from_dict(d: dict) -> "SarState":
        """오늘(2026-08-12 07:51) 추가된 futures_exit_state.json의 평면 키를 그대로 흡수한다.
        중첩 형태({"sar": {...}})와 평면 형태 둘 다 받는다."""
        src = d.get("sar", d) if isinstance(d, dict) else {}
        return SarState(
            sar=float(src.get("sar_value", 0.0)),
            ep=float(src.get("sar_ep", 0.0)),
            af=float(src.get("sar_af", 0.02)),
            bull=bool(src.get("sar_bull", True)),
            af_init=float(src.get("sar_af_init", 0.02)),
            af_step=float(src.get("sar_af_step", 0.02)),
            af_max=float(src.get("sar_af_max", 0.10)),
        )


# ─────────────────────────────────────────────────────────────────────────────
# 7. 데이터 공급 (세션 / 야간 병합 / back-adjust)
# ─────────────────────────────────────────────────────────────────────────────
def filter_session(df, start: str, end: str):
    """DatetimeIndex 기반 세션 필터. pandas 의존은 이 함수에만 둔다."""
    return df.between_time(start, end)


def merge_night(day_close, night_close, resample: str = "5min", cutoff=None):
    """주간 종가에 야간 종가를 리샘플해 병합.

    기준: era_order_manager.py:1094-1112 (KIS 야간 데이터 병합).
    ``cutoff``가 주어지면 그 이후 야간 봉만 사용한다(라이브의 36시간 cutoff).
    """
    import pandas as pd
    n = night_close if cutoff is None else night_close[night_close.index > cutoff]
    n5 = n.resample(resample).last().dropna()
    merged = pd.concat([day_close, n5]).sort_index()
    return merged[~merged.index.duplicated(keep="first")]


def back_adjust(closes: Series, session_ids, mode: str = "subtract") -> tuple:
    """세션 경계 갭을 제거해 연속 시계열을 만든다 (선물 연속물 back-adjust).

    반환: (adjusted: Series, offset: np.ndarray)
      - subtract: 실제가격 = adjusted + offset   (pt 단위 보존 → 이 전략에 적합)
      - ratio   : 실제가격 = adjusted * offset   (수익률 보존, pt 스케일 왜곡)

    측정 결과 차감식이 칼만 지연을 개장 직후 11.44pt→6.94pt로 줄였다.
    비율식은 게이트 임계(1.5pt)와 스케일이 안 맞아 부적합.
    """
    if closes.space != "actual":
        raise ValueError("back_adjust의 입력은 실제 가격 공간이어야 합니다")
    v = closes.values
    sid = np.asarray(session_ids)
    if len(v) != len(sid):
        raise ValueError("closes와 session_ids의 길이가 다릅니다")
    off = np.zeros(len(v)) if mode == "subtract" else np.ones(len(v))
    acc = 0.0 if mode == "subtract" else 1.0
    prev_close = None
    for i in range(len(v)):
        if i > 0 and sid[i] != sid[i-1]:
            if prev_close is not None and np.isfinite(prev_close) and prev_close != 0:
                if mode == "subtract":
                    acc += v[i] - prev_close
                else:
                    acc *= v[i] / prev_close
        off[i] = acc
        if i + 1 == len(v) or sid[i+1] != sid[i]:
            prev_close = v[i]
    adj = (v - off) if mode == "subtract" else (v / off)
    return Series(adj, "adjusted", closes.unit), off


# ─────────────────────────────────────────────────────────────────────────────
# 8. 진입 타점
# ─────────────────────────────────────────────────────────────────────────────
def breakout_targets(day_open: float, prev_range: float, k: float) -> tuple:
    """변동성 돌파. 기준: era_order_manager.py:4317-4318"""
    return (day_open + prev_range * k, day_open - prev_range * k)


def kalman_band_targets(kf_price: float, std_error: float, mult: float) -> tuple:
    """칼만 밴드. 기준: era_order_manager.py:1240-1241"""
    band = std_error * mult
    return (kf_price + band, kf_price - band)


def prev_session_range(daily_high, daily_low, upto_exclusive: int) -> float:
    """직전 세션의 고저폭. 인덱스 규약은 Window와 동일하게 배타적."""
    h = np.asarray(daily_high, dtype=float)
    l = np.asarray(daily_low, dtype=float)
    j = upto_exclusive - 1
    if j < 0 or j >= len(h):
        return 0.0
    return float(h[j] - l[j])


# ─────────────────────────────────────────────────────────────────────────────
# 9. ta 라이브러리 호환 변형 (ta_* 접두)
# ─────────────────────────────────────────────────────────────────────────────
# 구 백테스터 3종(bqa/backtester.py, bqa/enhanced_backtester.py,
# futures_trader/backtester.py)이 `ta` 패키지로 계산하던 지표들이다.
#
# **ta의 정의는 이 모듈의 표준 정의와 다르다.** 마이그레이션 4번의 실측(31,797봉):
#
#   - 볼린저: ta는 `.std(ddof=0)` (모표준편차). 이 모듈 기본 ddof=1과 상단밴드
#     최대 3.195pt 차이. → bollinger_series(..., ddof=0)로 재현되므로 별도 함수 없음.
#   - RSI: ta는 **Wilder RSI가 아니다.** 0에서 출발하는 EWM(adjust=False)이라
#     초기 SMA 시드를 쓰는 wilder_rsi와 최대 26.15 차이, 첫 유효 인덱스도 13 vs 14.
#
# 즉 이 백테스터들은 라이브(era_order_manager: ddof=1)와 **원래부터 다른 지표로**
# 돌고 있었다. 동작 무변경 리팩터링이 목표이므로 그 차이를 고치지 않고 여기에
# `ta_` 접두로 박제한다. 새 코드에서는 표준판(wilder_rsi 등)을 쓸 것.
#
# 각 함수는 ta 원본 소스를 1:1 재현하며 scratch/test_ta_compat_*.py 가 maxdiff=0 검증.


def _ta_ema(series, periods: int):
    """ta.utils._ema 재현: ewm(span, min_periods=periods, adjust=False).mean()"""
    return series.ewm(span=periods, min_periods=periods, adjust=False).mean()


def ta_ema_series(closes: Sequence[float], window: int) -> np.ndarray:
    """``ta.trend.EMAIndicator(close, window).ema_indicator()`` 재현.

    표준 EMA와 달리 min_periods=window라 앞 window-1개가 NaN이다.
    """
    import pandas as pd
    return _ta_ema(pd.Series(np.asarray(closes, dtype=float)), window).values


def ta_rsi_series(closes: Sequence[float], window: int = 14) -> np.ndarray:
    """``ta.momentum.RSIIndicator(close, window).rsi()`` 재현.

    ⚠️ **Wilder RSI가 아니다.** wilder_rsi()와는 다음이 다르다:
      - 상승/하락분을 `.where(diff>0, 0.0)`로 만들어 **index 0의 NaN이 0.0으로 치환**된다
        (`.clip()`을 쓰면 NaN이 남아 결과가 달라진다 — 실측 21.2 차이)
      - 초기 SMA 시드 없이 0에서 출발하는 EWM(alpha=1/window, adjust=False)
      - 첫 유효 인덱스가 window-1 (Wilder는 window)
      - 하락평균이 0이면 100으로 고정
    """
    import pandas as pd
    s = pd.Series(np.asarray(closes, dtype=float))
    diff = s.diff(1)
    up = diff.where(diff > 0, 0.0)
    dn = -diff.where(diff < 0, 0.0)
    emaup = up.ewm(alpha=1.0 / window, min_periods=window, adjust=False).mean()
    emadn = dn.ewm(alpha=1.0 / window, min_periods=window, adjust=False).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = emaup / emadn
        return np.where(emadn == 0, 100.0, 100.0 - (100.0 / (1.0 + rs)))


def ta_macd_series(closes: Sequence[float], window_slow: int = 26,
                   window_fast: int = 12, window_sign: int = 9) -> tuple:
    """``ta.trend.MACD`` 재현 → (macd, signal, diff).

    signal은 macd 시리즈에 다시 _ta_ema를 먹인 것이라 NaN 구간이 누적된다.
    """
    import pandas as pd
    s = pd.Series(np.asarray(closes, dtype=float))
    macd = _ta_ema(s, window_fast) - _ta_ema(s, window_slow)
    signal = _ta_ema(macd, window_sign)
    return macd.values, signal.values, (macd - signal).values


def ta_atr_series(high, low, close, window: int = 14) -> np.ndarray:
    """``ta.volatility.AverageTrueRange(...).average_true_range()`` 재현.

    ⚠️ ta는 결과 배열을 ``np.zeros``로 만들어 워밍업 구간이 **NaN이 아니라 0.0**이다.
    (`df.dropna()`로 걸러지지 않는다는 뜻 — 호출부에서 워밍업을 따로 잘라야 한다.)
    TR 자체는 prev_close가 NaN인 첫 봉에서 high-low가 된다(pandas max가 NaN 무시).
    """
    import pandas as pd
    h = pd.Series(np.asarray(high, dtype=float))
    l = pd.Series(np.asarray(low, dtype=float))
    c = pd.Series(np.asarray(close, dtype=float))
    prev_close = c.shift(1)
    tr = pd.DataFrame({
        "tr1": h - l,
        "tr2": (h - prev_close).abs(),
        "tr3": (l - prev_close).abs(),
    }).max(axis=1)
    atr = np.zeros(len(c))
    if len(c) >= window:
        atr[window - 1] = tr[0:window].mean()
        for i in range(window, len(atr)):
            atr[i] = (atr[i - 1] * (window - 1) + tr.iloc[i]) / float(window)
    return atr
