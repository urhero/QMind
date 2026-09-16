# -*- coding: utf-8 -*-
"""팩터 유니버스 평가 + 선정 (README [4]).

롱-숏 수익률 행렬을 만들고 rank_score(factor_ranking_method) 로 팩터를 선정한다.
선정 규칙(절단 / cluster dedup / hysteresis)은 service.factor.selection.select_factors()
로 walk-forward 엔진과 공유된다.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from service.factor.factor_returns import aggregate_factor_returns
from service.factor.selection import (
    compute_newey_west_tstat,
    compute_rank_score,
    compute_tstat,
    select_factors,
)
from service.paths import HISTORY_DIR, OUTPUT_DIR, TEST_OUTPUT_DIR, dated
from service.pipeline.weight_history import load_prev_selection
from utils.validation import validate_return_matrix

logger = logging.getLogger(__name__)


def evaluate_universe(kept_abbrs, kept_names, kept_styles, filtered_data, end_date, test_file, pipeline_params):
    """팩터 유니버스를 평가하고 rank_score 랭킹 + 클러스터 dedup 으로 팩터를 선정한다.

    cluster_method="winner_median"(기본)이면 고정 Top-N 없이 가변 개수,
    "topn"이면 rank_score 상위 top_factor_count(50) 절단.

    selection_hysteresis > 0 이면 직전 회차 선정 팩터(weight history 의
    factor_styles raw_weight > 0)를 incumbent 로 보호 — 챌린저가 margin
    이상 이겨야 교체된다 (walk-forward 엔진과 동일 로직 공유).
    """
    logger.info("Building monthly return matrix")
    ret_df = aggregate_factor_returns(
        filtered_data, kept_abbrs,
        backtest_start=pipeline_params["backtest_start"],
        cost_bps=pipeline_params["transaction_cost_bps"],
    )
    if ret_df.empty:
        raise ValueError(
            f"No valid factor returns after aggregation. "
            f"Input: {len(filtered_data)} factors, {len(kept_abbrs)} abbreviations"
        )
    ret_df.loc[ret_df.index[0]] = 0.0
    ret_df = ret_df.sort_index()
    validate_return_matrix(ret_df, "factor_return_matrix")

    if ret_df.columns.duplicated().any():
        logger.warning("Duplicate factor columns detected, removing duplicates")
        ret_df = ret_df.loc[:, ~ret_df.columns.duplicated(keep="first")]

    # 0 수익률 월 필터 — 첫 행(강제 기준점 0)은 제외하고 실제 월만 센다 (2026-09-16;
    # 구 코드는 기준점 행까지 세서 설정값 10 이 실질 9 였음). 엔진 Tier 2 와 동일 규칙.
    valid = ret_df.columns[(ret_df.iloc[1:] == 0).sum() <= pipeline_params["max_zero_return_months"]]
    ret_df = ret_df[valid]

    meta_all = pd.DataFrame({"factorAbbreviation": kept_abbrs, "factorName": kept_names, "styleName": kept_styles})
    meta = meta_all[meta_all["factorAbbreviation"].isin(valid)].reset_index(drop=True)

    months = len(ret_df) - 1
    # 명시 정렬 (다른 지표 3개와 동일한 reindex 관례): 위치 대입은 중복 팩터명으로
    # 컬럼 dedup 이 발동하면 meta(중복 행 유지)와 길이가 어긋나 크래시했음
    meta["cagr"] = ((1 + ret_df).cumprod().iloc[-1] ** (12 / months) - 1).reindex(
        meta["factorAbbreviation"]).values

    # Newey-West 보정 t-stat 진단 컬럼 (관찰용, 랭킹 미사용)
    monthly_rets = ret_df.iloc[1:][meta["factorAbbreviation"].tolist()]
    nw_lag = int(pipeline_params.get("newey_west_lag", 3))
    meta["tstat"] = compute_tstat(monthly_rets).reindex(meta["factorAbbreviation"]).values
    meta["newey_west_tstat"] = (
        compute_newey_west_tstat(monthly_rets, lag=nw_lag)
        .reindex(meta["factorAbbreviation"]).values
    )

    # 팩터 선정 점수: factor_ranking_method (walk-forward 와 동일 로직 공유,
    # 백테스트로 검증된 config 와 production 선정 기준을 일치시킴)
    ranking_method = pipeline_params.get("factor_ranking_method", "cagr")
    style_map_full = dict(zip(meta["factorAbbreviation"], meta["styleName"]))
    meta["rank_score"] = (
        compute_rank_score(monthly_rets, ranking_method, style_map_full)
        .reindex(meta["factorAbbreviation"]).values
    )
    meta["rank_style"] = meta.groupby("styleName")["rank_score"].rank(ascending=False)
    meta["rank_total"] = meta["rank_score"].rank(ascending=False)

    meta = meta.sort_values("rank_score", ascending=False).reset_index(drop=True)

    # 메타 저장 (clustering 적용 전 전체 universe 메타)
    if test_file:
        suffix = f"_{Path(test_file).stem}"
        TEST_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        meta.to_csv(TEST_OUTPUT_DIR / f"meta_data_test{suffix}.csv", index=False)
    else:
        # 기준일 = 수익률 행렬의 마지막 월
        meta.to_csv(dated(OUTPUT_DIR / "meta_data.csv", ret_df.index.max()), index=False)

    top_n = min(pipeline_params["top_factor_count"], len(meta))

    # 선정(절단 / 클러스터 dedup) + 선정 히스테리시스 — walk-forward Tier 2 와
    # select_factors() 공유 (선정 규칙 단일 출처). incumbents = 직전 회차 선정 집합
    # (factor_styles raw_weight>0). test 모드는 prod history 오염 방지를 위해 skip.
    margin = float(pipeline_params.get("selection_hysteresis", 0.0))
    prev_selected = None
    if margin > 0 and not test_file:
        prev_selected, _prev_sel_date = load_prev_selection(HISTORY_DIR, end_date)
    selected = select_factors(
        monthly_rets, meta.set_index("factorAbbreviation")["rank_score"],
        pipeline_params, top_n, incumbents=prev_selected, margin=margin,
    )

    meta = meta.set_index("factorAbbreviation").loc[selected].reset_index()

    order = meta["factorAbbreviation"].tolist()
    ret_df = ret_df[order]

    logger.info("Return matrix built (%d factors)", len(order))
    return ret_df, meta
