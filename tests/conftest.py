# -*- coding: utf-8 -*-
"""
Shared pytest fixtures for QMind tests.

이 파일에는 테스트에서 공통으로 사용하는 데이터와 설정이 포함됩니다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# 기본 데이터 Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def sample_time_series() -> pd.DataFrame:
    """시계열 테스트용 샘플 데이터 (prepend_start_zero 테스트용)"""
    dates = pd.date_range("2024-01-31", periods=3, freq="ME")
    return pd.DataFrame(
        {"factor_A": [0.05, 0.03, -0.02]},
        index=dates
    )


@pytest.fixture
def single_value_time_series() -> pd.DataFrame:
    """단일 값 시계열 데이터 (엣지 케이스 테스트용)"""
    dates = pd.date_range("2024-01-31", periods=1, freq="ME")
    return pd.DataFrame({"factor_A": [0.05]}, index=dates)


# ═══════════════════════════════════════════════════════════════════════════════
# 시뮬레이션 테스트용 Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def sample_style_returns() -> tuple[pd.DataFrame, list[str]]:
    """optimize_constrained_weights 테스트용 스타일 수익률"""
    np.random.seed(42)
    dates = pd.date_range("2020-01-31", periods=36, freq="ME")  # 3년

    rtn_df = pd.DataFrame({
        "val_factor_1": np.random.randn(36) * 0.03,
        "val_factor_2": np.random.randn(36) * 0.025,
        "mom_factor_1": np.random.randn(36) * 0.04,
        "mom_factor_2": np.random.randn(36) * 0.035,
        "qual_factor_1": np.random.randn(36) * 0.02,
    }, index=dates)

    style_list = ["Valuation", "Valuation", "Momentum", "Momentum", "Quality"]

    return rtn_df, style_list


