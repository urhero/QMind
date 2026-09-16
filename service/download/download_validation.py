# -*- coding: utf-8 -*-
"""다운로드 데이터 검증 및 커버리지 리포트 모듈.

pipeline-ready parquet 데이터의 월별/팩터별 커버리지를 검증하고
Rich 테이블로 시각화한다.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from service.download.parquet_io import load_factor_parquet, month_gap_issues
from service.paths import MRETURN_FILE

logger = logging.getLogger(__name__)


def validate_parquet_coverage(
    data_dir: Path,
    mreturn_path: Path | None = None,
    *,
    gap_threshold_days: int = 35,
    factor_drop_pct: float = 0.10,
    stock_drop_pct: float = 0.20,
) -> tuple[list[dict], pd.DataFrame, pd.DataFrame]:
    """Pipeline-ready parquet(연도별 분할 factor_YYYY.parquet)의 월별·팩터별 커버리지를 검증한다.

    검증 항목:
      1. 빈 월 감지: 연속 날짜 간격이 gap_threshold_days 초과
      2. 팩터 커버리지: 이전 월 대비 factor_drop_pct 이상 팩터 수 감소
      3. 종목 커버리지: 이전 월 대비 stock_drop_pct 이상 종목 수 감소
      4. M_RETURN 정합성: factor와 mreturn의 월 불일치

    Args:
        data_dir: parquet 디렉토리 (data/{BENCHMARK}/)
        mreturn_path: mreturn parquet 경로 (None이면 data_dir에서 추론)
        gap_threshold_days: 빈 월 판단 기준 일수 (기본 35)
        factor_drop_pct: 팩터 수 급감 판단 기준 (기본 10%)
        stock_drop_pct: 종목 수 급감 판단 기준 (기본 20%)

    Returns:
        (warnings_list, factor_df, mret_df) 튜플
        - warnings_list: [{"level": "WARN"|"ERROR", "type": str, "message": str}, ...]
        - factor_df: 로드된 팩터 DataFrame (재사용 가능)
        - mret_df: 로드된 M_RETURN DataFrame (재사용 가능)
    """
    if mreturn_path is None:
        mreturn_path = Path(data_dir) / MRETURN_FILE

    factor_df = load_factor_parquet(
        data_dir, columns=["ddt", "factorAbbreviation", "gvkeyiid", "val"]
    )
    mret_df = pd.read_parquet(mreturn_path, columns=["ddt", "gvkeyiid"])

    warnings_list = _validate_parquet_coverage_impl(factor_df, mret_df, gap_threshold_days, factor_drop_pct, stock_drop_pct)
    return warnings_list, factor_df, mret_df


def _validate_parquet_coverage_impl(
    factor_df: pd.DataFrame,
    mret_df: pd.DataFrame,
    gap_threshold_days: int,
    factor_drop_pct: float,
    stock_drop_pct: float,
) -> list[dict]:
    """validate_parquet_coverage의 내부 구현."""
    warnings_list: list[dict] = []

    dates = sorted(factor_df["ddt"].unique())
    if len(dates) < 2:
        warnings_list.append({"level": "ERROR", "type": "INSUFFICIENT_DATA", "message": f"Only {len(dates)} month(s) in data"})
        return warnings_list

    # ─── [1] 빈 월 감지 ───
    warnings_list.extend(month_gap_issues(dates, gap_threshold_days))

    # ─── [2] 월별 팩터 수 ───
    monthly_factors = factor_df.groupby("ddt")["factorAbbreviation"].nunique().sort_index()
    for i in range(1, len(monthly_factors)):
        prev, curr = monthly_factors.iloc[i - 1], monthly_factors.iloc[i]
        if prev > 0 and (prev - curr) / prev > factor_drop_pct:
            dt = monthly_factors.index[i]
            warnings_list.append({
                "level": "WARN",
                "type": "FACTOR_DROP",
                "message": f"{pd.Timestamp(dt).strftime('%Y-%m')}: factors {prev}->{curr} ({(prev-curr)/prev:.0%} drop)",
            })

    # ─── [3] 월별 종목 수 ───
    monthly_stocks = factor_df.groupby("ddt")["gvkeyiid"].nunique().sort_index()
    for i in range(1, len(monthly_stocks)):
        prev, curr = monthly_stocks.iloc[i - 1], monthly_stocks.iloc[i]
        if prev > 0 and (prev - curr) / prev > stock_drop_pct:
            dt = monthly_stocks.index[i]
            warnings_list.append({
                "level": "WARN",
                "type": "STOCK_DROP",
                "message": f"{pd.Timestamp(dt).strftime('%Y-%m')}: stocks {prev}->{curr} ({(prev-curr)/prev:.0%} drop)",
            })

    # ─── [4] M_RETURN 정합성 ───
    factor_months = set(pd.to_datetime(dates))
    mret_months = set(mret_df["ddt"].unique())
    missing_in_mret = factor_months - mret_months
    if missing_in_mret:
        for m in sorted(missing_in_mret):
            warnings_list.append({
                "level": "ERROR",
                "type": "MRETURN_MISSING",
                "message": f"M_RETURN missing for {pd.Timestamp(m).strftime('%Y-%m')}",
            })

    # ─── [5] val NULL 비율 검증 ───
    if "val" in factor_df.columns:
        monthly_null_pct = factor_df.groupby("ddt")["val"].apply(lambda x: x.isna().mean()).sort_index()
        if len(monthly_null_pct) >= 3:
            hist = monthly_null_pct.iloc[:-1]
            latest_pct = monthly_null_pct.iloc[-1]
            hist_mean = hist.mean()
            hist_std = hist.std() if len(hist) > 1 else 0.01
            if latest_pct >= 0.99:
                dt = monthly_null_pct.index[-1]
                warnings_list.append({
                    "level": "ERROR",
                    "type": "VAL_ALL_NULL",
                    "message": (
                        f"{pd.Timestamp(dt).strftime('%Y-%m')}: val NULL {latest_pct:.0%} "
                        f"(historical avg {hist_mean:.1%}) - data not loaded"
                    ),
                })
            elif latest_pct > hist_mean + max(3 * hist_std, 0.10):
                dt = monthly_null_pct.index[-1]
                warnings_list.append({
                    "level": "WARN",
                    "type": "VAL_HIGH_NULL",
                    "message": (
                        f"{pd.Timestamp(dt).strftime('%Y-%m')}: val NULL {latest_pct:.1%} "
                        f"(historical avg {hist_mean:.1%})"
                    ),
                })

    # ─── [6] 신규 월 팩터 누락 확인 ───
    if len(dates) >= 3:
        recent_3 = dates[-3:]
        recent_factors = factor_df[factor_df["ddt"].isin(recent_3)].groupby("ddt")["factorAbbreviation"].apply(set)
        if len(recent_factors) == 3:
            all_factors = recent_factors.iloc[0] | recent_factors.iloc[1] | recent_factors.iloc[2]
            latest_factors = recent_factors.iloc[-1]
            missing_latest = all_factors - latest_factors
            if missing_latest:
                warnings_list.append({
                    "level": "WARN",
                    "type": "FACTOR_MISSING_LATEST",
                    "message": f"Latest month missing {len(missing_latest)} factors: {', '.join(sorted(missing_latest)[:5])}{'...' if len(missing_latest) > 5 else ''}",
                })

    return warnings_list
