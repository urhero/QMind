# -*- coding: utf-8 -*-
"""
Unit tests for calculate_factor_stats_batch() function (production 경로).

calculate_factor_stats_batch() 함수 테스트:
- 팩터의 5분위 포트폴리오 구성
- 섹터별/전체 분위수익률 계산
- Q1-Q5 스프레드 계산
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from service.pipeline.factor_analysis import (
    calculate_factor_stats_batch,
    filter_and_label_factors,
    label_book_at_date,
    label_window_frames,
    slice_recent_months,
)


# ═══════════════════════════════════════════════════════════════════════════
# calculate_factor_stats_batch (production 경로) 직접 단위 테스트
# ═══════════════════════════════════════════════════════════════════════════

def _make_multi_factor_frame(
    n_months: int = 6, n_stocks: int = 8, factors: tuple = ("FA", "FB"), seed: int = 7,
) -> pd.DataFrame:
    """factorAbbreviation 컬럼을 가진 batch 입력 프레임 생성 (2 섹터)."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-31", periods=n_months, freq="ME")
    rows = []
    for fa in factors:
        for m, d in enumerate(dates):
            for s in range(n_stocks):
                rows.append({
                    "gvkeyiid": f"G{s:02d}",
                    "ddt": d,
                    "sec": "S1" if s < n_stocks // 2 else "S2",
                    "val": float(rng.normal(s, 0.1)),       # 종목별 수준 + 노이즈
                    "M_RETURN": float(rng.normal(0.01 * (s % 3), 0.02)),
                    "factorAbbreviation": fa,
                })
    return pd.DataFrame(rows)


def test_book_labels_use_as_of_values() -> None:
    """배포 북 라벨은 기준일 값 순위 (전월 값으로 만들면 백테스트보다 신호가 한 달 늦다)."""
    dates = pd.date_range("2024-01-31", periods=6, freq="ME")
    rows = []
    for m, d in enumerate(dates):
        for s in range(10):
            # 마지막 달 직전까지 val 큰 종목이 수익도 높고, 마지막 달에 순위가 뒤집힌다
            val = float(9 - s if m == 5 else s)
            rows.append({"gvkeyiid": f"G{s}", "ddt": d, "sec": "S1", "val": val,
                         "M_RETURN": 0.01 * s, "factorAbbreviation": "FA", "ticker": f"T{s}", "isin": f"I{s}"})
    df = pd.DataFrame(rows)
    stats = calculate_factor_stats_batch(df, ["FA"], [0], test_mode=True)   # 0 = 높을수록 좋음
    kept, _, _, _, dropped, labeled = filter_and_label_factors(["FA"], ["FA"], ["S"], stats)
    T = dates[-1]

    old = labeled[0][labeled[0]["ddt"] == T].set_index("gvkeyiid")["label"]
    book = label_book_at_date(df, kept, dropped, labeled, ["FA"], [0], T, test_mode=True)[0]
    new = book.set_index("gvkeyiid")["label"]

    assert (book["ddt"] == T).all()
    assert old["G9"] == 1 and old["G0"] == -1      # 전월 값 순위: G9 롱
    assert new["G0"] == 1 and new["G9"] == -1      # 기준일 값 순위: 뒤집혀 G0 롱


def test_window_labels_keep_lag_from_before_window() -> None:
    """롤링 IS 수익률 프레임은 창 밖 값도 lag 로 쓴다 (엔진과 같음). 창 안에서 lag 하면 재편입 종목이 빠진다."""
    dates = pd.date_range("2024-01-31", periods=8, freq="ME")
    rows = []
    for m, d in enumerate(dates):
        for s in range(10):
            if s == 9 and m in (2, 3, 4):   # G9 는 3달 쉬고 창 첫 달(m=5) 에 돌아온다
                continue
            rows.append({"gvkeyiid": f"G{s}", "ddt": d, "sec": "S1", "val": float(s),
                         "M_RETURN": 0.01 * s, "factorAbbreviation": "FA", "ticker": f"T{s}", "isin": f"I{s}"})
    full = pd.DataFrame(rows)
    window = slice_recent_months(full, 3)                 # m=4 기저 + m=5~7
    start = dates[5]

    stats = calculate_factor_stats_batch(window, ["FA"], [0], test_mode=True)
    kept, _, _, _, dropped, labeled = filter_and_label_factors(["FA"], ["FA"], ["S"], stats)
    assert "G9" not in set(labeled[0].loc[labeled[0]["ddt"] == start, "gvkeyiid"])   # 창 안 lag: 탈락

    out = label_window_frames(full, start, kept, dropped, labeled, ["FA"], [0], test_mode=True)[0]
    assert out["ddt"].min() == start                       # 기저 달은 들어오지 않는다
    assert out.loc[(out["ddt"] == start) & (out["gvkeyiid"] == "G9"), "label"].iloc[0] == 1   # 전체 lag: 포함


class TestCalculateFactorStatsBatch:
    """batch 버전 직접 테스트: single 버전과의 parity 가 핵심 불변식."""

    def test_insufficient_history_returns_none(self) -> None:
        """월 수 <= 2 (lag 후) 팩터는 (None,)*4."""
        df = _make_multi_factor_frame(n_months=3)   # lag 후 2개월 -> 부족
        batch = calculate_factor_stats_batch(df, ["FA", "FB"], [1, 1], test_mode=True)
        assert batch[0] == (None, None, None, None)
        assert batch[1] == (None, None, None, None)

    def test_missing_factor_returns_none(self) -> None:
        """abbr_list 에 있으나 데이터에 없는 팩터는 (None,)*4, 위치 유지."""
        df = _make_multi_factor_frame(factors=("FA",))
        batch = calculate_factor_stats_batch(df, ["FA", "GHOST"], [1, 1], test_mode=True)
        assert batch[0][0] is not None
        assert batch[1] == (None, None, None, None)

    def test_small_sectors_skipped_for_quintile_coverage(self) -> None:
        """production 모드에서 전 섹터가 min_sector_stocks 미만이면 분위 배정
        불가 -> 'insufficient quintile coverage' skip 분기."""
        df = _make_multi_factor_frame(factors=("FA",), n_stocks=8)  # 섹터당 4 < 10
        batch = calculate_factor_stats_batch(
            df, ["FA"], [1], test_mode=False, min_sector_stocks=10,
        )
        assert batch[0] == (None, None, None, None)

    @staticmethod
    def _sparse_fb_frame() -> pd.DataFrame:
        """FB 의 val 을 8종목 중 4종목만 남긴 프레임 (FB 커버리지 50%)."""
        df = _make_multi_factor_frame()
        sparse = df["gvkeyiid"].isin({"G04", "G05", "G06", "G07"})
        df.loc[(df["factorAbbreviation"] == "FB") & sparse, "val"] = np.nan
        return df

    def test_low_coverage_factor_excluded(self) -> None:
        """min_coverage_pct: 유니버스 대비 유효 관측 비율(FB 50%)이 임계(60%)
        미만이면 (None,)*4. FA(100%)는 유지."""
        batch = calculate_factor_stats_batch(
            self._sparse_fb_frame(), ["FA", "FB"], [1, 1], test_mode=True, min_coverage_pct=0.6,
        )
        assert batch[0][0] is not None
        assert batch[1] == (None, None, None, None)

    def test_coverage_filter_off_by_default(self) -> None:
        """min_coverage_pct 기본값 0.0 이면 희소 팩터도 유지 (기존 동작 보존)."""
        batch = calculate_factor_stats_batch(
            self._sparse_fb_frame(), ["FA", "FB"], [1, 1], test_mode=True,
        )
        assert batch[1][0] is not None

    def test_result_order_follows_abbr_list(self) -> None:
        """결과 리스트 순서는 factor_abbr_list 순서 (데이터 순서 아님)."""
        df = _make_multi_factor_frame(factors=("FB", "FA"))
        batch = calculate_factor_stats_batch(df, ["FA", "FB"], [1, 1], test_mode=True)
        # spread 컬럼명으로 위치 검증
        assert batch[0][2].columns[0] == "FA"
        assert batch[1][2].columns[0] == "FB"


class TestSliceRecentMonths:
    """factor_analysis.slice_recent_months (mp 파이프라인 롤링 IS 슬라이스)."""

    def test_none_window_returns_input(self) -> None:
        from service.pipeline.factor_analysis import slice_recent_months
        df = _make_multi_factor_frame(n_months=12)
        assert slice_recent_months(df, None) is df
        assert slice_recent_months(df, 0) is df

    def test_keeps_window_plus_lag_base(self) -> None:
        from service.pipeline.factor_analysis import slice_recent_months
        df = _make_multi_factor_frame(n_months=12)
        out = slice_recent_months(df, 6)
        assert out["ddt"].nunique() == 7  # 6개월 + lag 기저 1개월
        assert out["ddt"].max() == df["ddt"].max()

    def test_short_history_noop(self) -> None:
        from service.pipeline.factor_analysis import slice_recent_months
        df = _make_multi_factor_frame(n_months=5)
        assert slice_recent_months(df, 6) is df


class TestRollingIsStart:
    """walk_forward_engine.rolling_is_start (롤링 IS 윈도우 경계)."""

    def test_none_window_is_expanding(self) -> None:
        from service.backtest.walk_forward_engine import rolling_is_start
        dates = list(pd.date_range("2020-01-31", periods=12, freq="ME"))
        assert rolling_is_start(dates, 5, None) is None
        assert rolling_is_start(dates, 5, 0) is None

    def test_window_slices_last_n_months(self) -> None:
        from service.backtest.walk_forward_engine import rolling_is_start
        dates = list(pd.date_range("2020-01-31", periods=12, freq="ME"))
        # is_end_idx=9 (2020-10-31), window 6 -> 시작 idx 4 (2020-05-31)
        assert rolling_is_start(dates, 9, 6) == dates[4]

    def test_short_history_falls_back_to_full(self) -> None:
        from service.backtest.walk_forward_engine import rolling_is_start
        dates = list(pd.date_range("2020-01-31", periods=12, freq="ME"))
        assert rolling_is_start(dates, 3, 60) == dates[0]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
