# -*- coding: utf-8 -*-
"""팩터 분석 모듈: 5분위 포트폴리오 구성 및 섹터 필터링.

팩터 데이터를 5개 분위(Q1~Q5)로 분류하고 팩터 스프레드(Q1-Q5 수익률 차이)를 측정한 후,
비효과적인 섹터를 제거하고 롱/숏/중립(L/N/S) 라벨을 부여한다.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from rich.progress import track

logger = logging.getLogger(__name__)

# 5분위 분석 입력 컬럼 (calculate_factor_stats_batch 입력 스키마 단일 출처).
# model_portfolio / walk_forward_engine 가 동일 projection 에 사용한다.
ANALYZE_COLS = [
    "gvkeyiid", "ticker", "isin", "ddt", "sec", "val",
    "M_RETURN", "factorAbbreviation", "factorOrder",
]


def prepend_start_zero(series: pd.DataFrame) -> pd.DataFrame:
    """시계열 데이터 맨 앞에 0을 추가한다 (누적 수익률 계산의 기준선).

    첫 번째 날짜로부터 1개월 전 날짜에 0값을 삽입하여,
    누적 수익률 계산 시 시작점이 0%가 되도록 한다.

    Args:
        series: 날짜가 인덱스인 시계열 DataFrame

    Returns:
        맨 앞에 0이 추가되고 날짜순으로 정렬된 DataFrame
    """
    series.loc[series.index[0] - pd.DateOffset(months=1)] = 0
    return series.sort_index()


def filter_and_label_factors(
    factor_abbr_list: list[str],
    factor_name_list: list[str],
    style_name_list: list[str],
    factor_data_list: list[tuple[pd.DataFrame | None, pd.DataFrame | None, pd.DataFrame | None, pd.DataFrame | None]],
    spread_threshold_pct: float = 0.10,
) -> tuple[list[str], list[str], list[str], list[int], list[list[str]], list[pd.DataFrame]]:
    """음의 팩터 스프레드를 가진 섹터를 제거하고 L/N/S 라벨을 재계산한다.

    각 팩터-섹터 조합에서 팩터 스프레드(Q1-Q5)가 음수이면 해당 섹터를 제거하고,
    남은 데이터에서 임계값(팩터 스프레드 x spread_threshold_pct) 기반으로 롱/중립/숏 라벨을 부여한다.

    Args:
        factor_abbr_list: 팩터 약어 리스트
        factor_name_list: 팩터 이름 리스트
        style_name_list: 스타일 이름 리스트
        factor_data_list: calculate_factor_stats_batch() 결과 리스트

    Returns:
        (kept_abbrs, kept_names, kept_styles, kept_idx, dropped_sec, filtered_data) 튜플
        - kept_*: 유지된 팩터의 메타데이터
        - kept_idx: 원본 인덱스
        - dropped_sec: 팩터별 제거된 섹터 리스트
        - filtered_data: label 컬럼이 추가된 종목 데이터

    예시 Output (filtered_data 일부):
        | ddt        | gvkeyiid | ticker | sec      | quantile | label |
        |------------|----------|--------|----------|----------|-------|
        | 2024-01-31 | 001      | 600519 | Consumer | Q1       | 1     |
        | 2024-01-31 | 002      | 000858 | Consumer | Q5       | -1    |
        | 2024-01-31 | 003      | 601318 | Consumer | Q3       | 0     |
    """
    kept_factor_abbrs, kept_names, kept_styles, kept_idx = [], [], [], []
    dropped_sec: list[list[str]] = []
    filtered_raw_data_list: list[pd.DataFrame] = []

    for idx, (sector_return_df, _, _, raw_df) in track(
        enumerate(factor_data_list), description="Filtering sectors", total=len(factor_data_list)
    ):
        if sector_return_df is None or raw_df is None:
            logger.debug("Factor %d skipped - no data", idx)
            continue

        # 음의 스프레드 섹터 식별 및 제거 (양끝 분위 = Q1 vs Q5)
        q_bot = sector_return_df.index[-1]
        tmp = sector_return_df.T.reset_index()
        tmp["spread"] = tmp["Q1"] - tmp[q_bot]
        to_drop = tmp.loc[tmp["spread"] < 0, "sec"].tolist()
        raw_clean = raw_df[~raw_df["sec"].isin(to_drop)].reset_index(drop=True)

        if raw_clean.empty:
            logger.debug("Factor %d discarded - all sectors dropped", idx)
            continue

        # 남은 데이터로 분위별 기하평균 수익률 재계산
        # 기하평균 = 변동성 드래그 내장 (AM - GM ~= sigma^2/2)
        q_ret = raw_clean.groupby(["ddt", "quantile"], observed=False)["M_RETURN"].mean().unstack(fill_value=0)
        q_mean = (np.exp(np.log(1 + q_ret).mean(axis=0)) - 1).to_frame("mean")

        # 유효성 가드: 섹터 필터 후 재계산 스프레드가 양수가 아니면(<=0 또는 NaN)
        # "Q1이 Q5보다 좋다"는 롱-숏 전제 자체가 IS 에서 깨진 것 -> 팩터 탈락.
        # (스프레드 ~= 0 이면 thresh ~= 0 이 되어 롱 밴드가 전 분위를 삼키는
        # degenerate 라벨(예: 2026-06 EPSEstDispFY1C 롱-only)이 나오는 근본 원인.)
        # spread > 0 이면 Q1=롱, Q5=숏이 수학적으로 보장되므로 별도의 한쪽 라벨
        # 검사는 불필요하다.
        spread = q_mean.loc["Q1", "mean"] - q_mean.loc[q_bot, "mean"]
        if not spread > 0:  # NaN 포함 탈락
            logger.info("Factor %s discarded - non-positive Q1-Q5 spread after sector filter (%.5f)",
                        factor_abbr_list[idx], spread)
            continue

        # 임계값 기반 L/N/S 라벨 결정
        thresh = spread * spread_threshold_pct

        # 롱: Q1부터 내려가며 수익률 > (Q1 - threshold)인 분위
        q_mean["long"] = (q_mean["mean"] > q_mean.loc["Q1", "mean"] - thresh).astype(int).cumprod()
        # 숏: 마지막 분위부터 올라가며 수익률 < (최하위 + threshold)인 분위
        q_mean["short"] = (q_mean["mean"] < q_mean.loc[q_bot, "mean"] + thresh).astype(int) * -1
        q_mean["short"] = q_mean["short"].abs()[::-1].cumprod()[::-1] * -1
        q_mean["label"] = q_mean["long"] + q_mean["short"]

        # 라벨을 종목 데이터에 매핑
        label_map = q_mean["label"].to_dict()
        raw_clean["label"] = raw_clean["quantile"].map(label_map)
        merged = raw_clean.dropna(subset=["label"])

        kept_factor_abbrs.append(factor_abbr_list[idx])
        kept_names.append(factor_name_list[idx])
        kept_styles.append(style_name_list[idx])
        kept_idx.append(idx)
        dropped_sec.append(to_drop)
        filtered_raw_data_list.append(merged)

    logger.info("Sector filter retained %d / %d factors", len(kept_idx), len(factor_abbr_list))
    return kept_factor_abbrs, kept_names, kept_styles, kept_idx, dropped_sec, filtered_raw_data_list


def slice_recent_months(df: pd.DataFrame, window_months: int | None) -> pd.DataFrame:
    """마지막 window_months개월 + lag 기저 1개월만 남긴다 (롤링 IS, 2026-07-28 채택).

    기저 1개월은 calculate_factor_stats_batch 의 shift(1) lag 원천으로만 쓰이고
    (자기 자신은 val_lagged NaN 으로 탈락) 윈도우 첫 달의 관측을 보존한다.
    window 미지정(None/0)이거나 이력이 window 이하면 그대로 반환 (expanding).
    """
    if not window_months:
        return df
    dates = df["ddt"].drop_duplicates().sort_values()
    if len(dates) <= window_months + 1:
        return df
    cutoff = dates.iloc[-(window_months + 1)]
    return df[df["ddt"] >= cutoff]


def calculate_factor_stats_batch(
    merged_data: pd.DataFrame,
    factor_abbr_list: list[str],
    orders: list[int],
    test_mode: bool = False,
    min_sector_stocks: int = 10,
    min_coverage_pct: float = 0.0,
) -> list[tuple]:
    """모든 팩터의 5분위 분석을 하이브리드 방식으로 처리한다.

    lag는 전체 DataFrame에서 배치로 수행하고 (배치가 유리),
    rank/quantile/집계는 팩터별 루프로 수행한다 (2키 groupby가 3키보다 2.8x 빠름).

    Args:
        merged_data: 전체 팩터 데이터 (factorAbbreviation, val, M_RETURN 컬럼 필수)
        factor_abbr_list: 팩터 약어 리스트
        orders: 팩터별 정렬 방향 (factorOrder. 0=높을수록 좋음 -> 부호 플립,
            1=낮을수록 좋음 -> 그대로. 통일 후 ascending rank 1 = Q1 = 좋은 종목)
        test_mode: True이면 최소 종목수 검증 생략

    Returns:
        팩터별 5분위 분석 결과 리스트
        각 원소: (sector_return_df, quantile_return_df, spread_series, merged_df) 또는 (None,)*4
    """
    # [1] 팩터 메타 준비
    order_map = dict(zip(factor_abbr_list, orders))
    valid_factors = set(merged_data["factorAbbreviation"].unique()) & set(factor_abbr_list)

    # [2] NaN 제거 + batch lag (전체에서 한번에 — 팩터별보다 빠름)
    df = merged_data.dropna(subset=["val", "M_RETURN"]).copy()
    df["val_lagged"] = df.groupby(["gvkeyiid", "factorAbbreviation"])["val"].shift(1)
    df = df.dropna(subset=["val_lagged"]).drop(columns=["val"]).reset_index(drop=True)

    # [3] History 체크 (배치)
    date_counts = df.groupby("factorAbbreviation")["ddt"].nunique()
    sufficient_factors = set(date_counts[date_counts > 2].index)

    # [3.5] 단면 커버리지 필터: 월별 (유효 관측 종목수 / 유니버스 종목수)의 기간
    # 평균이 min_coverage_pct 미만인 팩터 제외. 은행 전용 팩터처럼 구조적으로
    # 희소한 팩터는 L/S 폭이 좁아 노이즈가 큰데도 선정 슬롯·스타일 예산을 차지
    # 하는 문제 방지 (2026-07-27 MXWO A/B 근거로 채택. IS 데이터로만 계산되어
    # walk-forward 에서 look-ahead 없음 — full-data 사전계산 경로에는 미적용).
    if min_coverage_pct > 0:
        uni = merged_data[["ddt", "gvkeyiid"]].drop_duplicates().groupby("ddt").size()
        obs = df.groupby(["factorAbbreviation", "ddt"], observed=True).size()
        cov = obs.div(uni, level="ddt").groupby(level="factorAbbreviation", observed=True).mean()
        low_cov = {fa for fa in valid_factors if cov.get(fa, 0.0) < min_coverage_pct}
        if low_cov:
            logger.info("Coverage filter: %d factor(s) below %.0f%% excluded: %s",
                        len(low_cov), min_coverage_pct * 100, sorted(low_cov))
            valid_factors -= low_cov

    # [4] Sort order 통일: factorOrder=0(높을수록 좋음) 팩터의 val_lagged 에 -1
    #     -> 전 팩터가 "낮을수록 좋음"이 되어 ascending rank 1 = Q1 = 좋은 종목
    desc_factors = {fa for fa in valid_factors if not bool(order_map.get(fa, 1))}
    if desc_factors:
        desc_mask = df["factorAbbreviation"].isin(desc_factors)
        df.loc[desc_mask, "val_lagged"] *= -1

    # [5] 팩터별 통합 루프: rank + quantile + 집계
    #     (2키 groupby가 3키보다 2.8x 빠르므로 per-factor 루프가 최적)
    labels = ["Q1", "Q2", "Q3", "Q4", "Q5"]
    q_bot = "Q5"
    bins = [0.0, 20.0, 40.0, 60.0, 80.0, 105.0]  # percentile 경계 (include_lowest)
    grouped = df.groupby("factorAbbreviation")

    results = []
    for factor_abbr in factor_abbr_list:
        if factor_abbr not in valid_factors or factor_abbr not in sufficient_factors:
            if factor_abbr in valid_factors and factor_abbr not in sufficient_factors:
                logger.warning("Skipping %s - insufficient history", factor_abbr)
            results.append((None, None, None, None))
            continue

        if factor_abbr not in grouped.groups:
            results.append((None, None, None, None))
            continue

        fdf = grouped.get_group(factor_abbr).copy()

        # rank + count (2키 groupby — 팩터당 ~53K행, ~900그룹)
        grp = fdf.groupby(["ddt", "sec"])["val_lagged"]
        fdf["rank"] = grp.rank(method="average", ascending=True)
        count_series = grp.transform("count")

        # percentile (count=1이면 분모=0이므로 NaN 처리)
        fdf["percentile"] = np.where(
            count_series > 1,
            (fdf["rank"] - 1) / (count_series - 1) * 100,
            np.nan,
        )
        if not test_mode:
            fdf.loc[count_series <= min_sector_stocks, "percentile"] = np.nan

        # quantile
        fdf["quantile"] = pd.cut(
            fdf["percentile"], bins=bins,
            labels=labels, include_lowest=True, right=True,
        )
        fdf = fdf.dropna(subset=["quantile"])
        fdf = fdf.drop(columns=["rank", "percentile", "val_lagged"])

        # 섹터 × 분위별 평균 수익률 (시간축 산술평균)
        sector_return_df = (
            fdf.groupby(["ddt", "sec", "quantile"], observed=False)["M_RETURN"]
            .mean().unstack(fill_value=0)
            .groupby("sec").mean().T
        )

        # 상-하위 분위 스프레드 (중간 분위는 불필요 — unstack 없이 양끝만 추출)
        q_mean = fdf.groupby(["ddt", "quantile"], observed=False)["M_RETURN"].mean()
        quantile_levels = q_mean.index.get_level_values("quantile").unique()
        if "Q1" not in quantile_levels or q_bot not in quantile_levels:
            logger.warning("Skipping %s - insufficient quintile coverage", factor_abbr)
            results.append((None, None, None, None))
            continue
        q1 = q_mean.xs("Q1", level="quantile")
        q5 = q_mean.xs(q_bot, level="quantile")
        spread_series = pd.DataFrame({factor_abbr: q1 - q5})
        spread_series = prepend_start_zero(spread_series)

        # quantile_return_df는 downstream에서 미사용 (filter_and_label에서 재계산)
        results.append((sector_return_df, None, spread_series, fdf))

    logger.info("Batch factor analysis: %d valid / %d total", sum(1 for r in results if r[0] is not None), len(factor_abbr_list))
    return results
