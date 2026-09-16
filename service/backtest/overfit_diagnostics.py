# -*- coding: utf-8 -*-
"""과적합 진단 모듈 (Step 2).

Walk-Forward 결과를 분석하여 파이프라인의 과적합 정도를 정량화한다.

3단계 핵심 테스트:
  1순위: Funnel Value-Add Test (파이프라인 단계별 가치 창출 검증)
  2순위: OOS Percentile Tracking (최종 팩터 OOS 백분위 생존율)
  3순위: Strict Jaccard Index (weight>0 팩터 안정성)

보조 지표:
  4순위: IS-OOS Rank Correlation (팩터 순위 유지력)
  5순위: Deflation Ratio (보조, 단독 판단 금지)
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from service.backtest.result_stitcher import WalkForwardResult
from service.report.diagnostics_keys import (
    CAT_CAUTION,
    CAT_CMP,
    CAT_DEFLATION,
    CAT_FUNNEL,
    CAT_OOS_CEW,
    CAT_OOS_EW,
    CAT_OOS_PERCENTILE,
    CAT_RANK_CORR,
    CAT_STRICT_JACCARD,
    METRIC_PATTERN,
)

logger = logging.getLogger(__name__)


def _weight_rebal_indices(rebal_log: pd.DataFrame) -> list[int]:
    """Tier 2 리밸런싱이 일어난 OOS 인덱스 리스트."""
    if "is_weight_rebal" not in rebal_log.columns:
        return []
    return np.flatnonzero(rebal_log["is_weight_rebal"].to_numpy(dtype=bool)).tolist()


def _cum_returns(history: list[dict], start: int, end: int) -> dict[str, float]:
    """월별 {팩터: 수익률} dict 리스트의 [start:end) 구간 팩터별 기하 누적 수익률."""
    cum: dict[str, float] = {}
    for month_dict in history[start:end]:
        for f, r in month_dict.items():
            cum[f] = (1 + cum.get(f, 0.0)) * (1 + r) - 1
    return cum


# ── 1순위: Funnel Value-Add Test ─────────────────────────────────────────


def calc_funnel_value_add(walk_forward_result: WalkForwardResult) -> dict[str, Any]:
    """파이프라인 단계별 가치 창출 검증.

    A. EW_All:       전체 유효 팩터 동일가중 (시장/팩터 베타)
    B. EW_Top50:     1차 필터링 후 동일가중 (필터링 실력)
    C. Constrained EW: 선정 팩터에 config 가중(기본 erc + TS 틸트) + style_cap(25%) 재분배
       (= 본전략 MP. "EW" 라벨은 1/N 시절 관례 — CSV 키 호환을 위해 유지)

    C 의 가중은 IS cov 기반 ERC(학습 파라미터 없음) + deterministic 재분배다
    (종목단 공분산/MC 최적화는 커밋 8dfb64e에서 제거됨).

    판별 기준 (CAGR 기준):
      C > B > A -> 정상 (필터링 + style_cap 제약 모두 가치 창출)
      B > C > A -> CONSTRAINT_DRAG: style_cap 제약이 OOS CAGR을 깎음
                   (과적합 아님; 수익 일부를 내주고 변동성/MDD를 낮추는 트레이드오프)
      A > B     -> FILTER_OVERFIT: rank_score 기반 Top-N 선정 자체가 과거 우연
    """
    # OOS 데이터가 없으면 판별 불가
    if len(walk_forward_result.oos_returns) == 0:
        return {
            "pattern": "INSUFFICIENT_DATA",
            "ew_all_cagr": 0.0, "ew_top50_cagr": 0.0, "cew_cagr": 0.0,
            "ew_all_mdd": 0.0, "ew_top50_mdd": 0.0, "cew_mdd": 0.0,
            "ew_all_sharpe": 0.0, "ew_top50_sharpe": 0.0, "cew_sharpe": 0.0,
            "interpretation": "OOS 데이터 부족 - Funnel Value-Add 판별 불가",
        }

    cew_perf = walk_forward_result.calc_performance()
    ew_all_perf = walk_forward_result.calc_ew_all_performance()
    ew_top50_perf = walk_forward_result.calc_ew_top50_performance()

    cagr_a = ew_all_perf["cagr"]
    cagr_b = ew_top50_perf["cagr"]
    cagr_cew = cew_perf["cagr"]

    mdd_a = ew_all_perf["mdd"]
    mdd_b = ew_top50_perf["mdd"]
    mdd_cew = cew_perf["mdd"]

    # 판별
    if cagr_a > cagr_b:
        pattern = "FILTER_OVERFIT"
        interpretation = (
            f"A(EW_All)={cagr_a:.4%} > B(EW_Top50)={cagr_b:.4%} "
            f"-> 1차 필터 과적합: rank_score(tstat/shrunk_tstat 등) 기반 Top-50 선정 "
            f"자체가 과거 우연. 하위 팩터 평균보다도 못함."
        )
    elif cagr_b > cagr_cew:
        pattern = "CONSTRAINT_DRAG"
        # C vs A 는 이 분기에서 보장되지 않으므로 실제 값으로 검증해 표기한다
        # (B > A 는 첫 분기 탈락으로 보장됨).
        if cagr_cew > cagr_a:
            cew_vs_all = f"> A(EW_All)={cagr_a:.4%}"
            tail = (
                f"과적합이 아니라 제약-리스크 트레이드오프: 수익 일부를 내주는 대신 "
                f"스타일 집중도/변동성/MDD를 낮춘다. 규제 요건이면 의도된 비용."
            )
        else:
            cew_vs_all = f"<= A(EW_All)={cagr_a:.4%}"
            tail = (
                f"주의: 제약 비용이 필터링 가치를 전부 잠식해 전체 funnel 의 "
                f"CAGR 순가치가 없음 (EW_All 이하). style_cap 수준 재검토 필요."
            )
        interpretation = (
            f"B(EW_Top50)={cagr_b:.4%} > C(Constrained EW)={cagr_cew:.4%} {cew_vs_all} "
            f"-> style_cap(결정론적 제약, 학습 가중치 아님)이 OOS CAGR을 깎음. {tail}"
        )
    else:
        pattern = "NORMAL"
        interpretation = (
            f"C(Constrained EW)={cagr_cew:.4%} > B(EW_Top50)={cagr_b:.4%} > A(EW_All)={cagr_a:.4%} "
            f"-> 정상: 필터링과 style_cap 제약 모두 가치 창출."
        )

    return {
        "pattern": pattern,
        "ew_all_cagr": cagr_a,
        "ew_top50_cagr": cagr_b,
        "cew_cagr": cagr_cew,
        "ew_all_mdd": mdd_a,
        "ew_top50_mdd": mdd_b,
        "cew_mdd": mdd_cew,
        "ew_all_sharpe": ew_all_perf["sharpe"],
        "ew_top50_sharpe": ew_top50_perf["sharpe"],
        "cew_sharpe": cew_perf["sharpe"],
        "interpretation": interpretation,
    }


# ── 2순위: OOS Percentile Tracking ──────────────────────────────────────


def calc_oos_percentile_tracking(walk_forward_result: WalkForwardResult) -> dict[str, Any]:
    """최종 선정 팩터(weight>0)의 OOS 구간 백분위 생존율.

    각 Tier 2 리밸런싱 구간에서:
    1. 전체 유효 팩터의 OOS 실현 수익률 줄세우기
    2. weight>0 팩터들의 평균 백분위 계산

    해석:
      평균 백분위 상위 40% 이내(값 <= 0.40) -> 견고
      평균 백분위 40~60%                      -> 보통
      평균 백분위 60% 이상(값 >= 0.60)          -> 과적합 의심
    """
    rebal_log = walk_forward_result.rebalance_log
    all_fr_history = walk_forward_result.oos_all_factor_returns_history
    active_history = walk_forward_result.active_factors_history

    if not all_fr_history or not active_history:
        return {
            "avg_percentile": np.nan,
            "period_percentiles": [],
            "interpretation": "데이터 부족 - Percentile Tracking 계산 불가",
        }

    weight_rebal_indices = _weight_rebal_indices(rebal_log)

    if not weight_rebal_indices:
        return {
            "avg_percentile": np.nan,
            "period_percentiles": [],
            "interpretation": "Tier 2 리밸런싱 없음 - Percentile Tracking 계산 불가",
        }

    # 방어적 검증: active_factors_history와 weight_rebal_indices 길이 일치 확인
    if len(active_history) != len(weight_rebal_indices):
        logger.warning(
            "active_factors_history(%d) != weight_rebal_indices(%d) - 인덱스 불일치 가능",
            len(active_history), len(weight_rebal_indices),
        )

    period_percentiles = []
    active_idx = 0

    for k, rebal_i in enumerate(weight_rebal_indices):
        if active_idx >= len(active_history):
            break

        active_set = active_history[active_idx]
        active_idx += 1

        if not active_set:
            continue

        # OOS 구간: 현재 Tier 2 ~ 다음 Tier 2 (또는 끝) 팩터별 누적 수익률 (기하 복리)
        next_rebal_i = weight_rebal_indices[k + 1] if k + 1 < len(weight_rebal_indices) else len(all_fr_history)
        cum_returns = _cum_returns(all_fr_history, rebal_i, next_rebal_i)
        if len(cum_returns) < 3:
            continue

        # 전체 팩터 순위 (높은 수익률 = 낮은 백분위 = 좋음, 동률 평균 처리)
        cum_series = pd.Series(cum_returns)
        rank_map = (cum_series.rank(ascending=False, method="average") / len(cum_series)).to_dict()

        # 선정 팩터의 평균 백분위 (결정적 출력: set 순회 순서가 PYTHONHASHSEED 에 따라
        # 달라져 합산 순서/말단 자릿수가 흔들리고 0.40/0.60 경계 판정이 뒤집혔음 -> 정렬 고정)
        active_percentiles = [rank_map[f] for f in sorted(active_set) if f in rank_map]
        if active_percentiles:
            period_percentiles.append(np.mean(active_percentiles))

    if not period_percentiles:
        return {
            "avg_percentile": np.nan,
            "period_percentiles": [],
            "interpretation": "유효 구간 없음 - Percentile Tracking 계산 불가",
        }

    avg_pct = np.mean(period_percentiles)

    if avg_pct <= 0.40:
        interp = f"평균 백분위 = {avg_pct:.2%} (상위 {avg_pct:.0%}) -> 견고한 팩터 선정"
    elif avg_pct <= 0.60:
        interp = f"평균 백분위 = {avg_pct:.2%} (상위 {avg_pct:.0%}) -> 보통 (랜덤과 차이 미미)"
    else:
        interp = f"평균 백분위 = {avg_pct:.2%} (상위 {avg_pct:.0%}) -> 과적합 의심 (IS 상위 팩터가 OOS에서 추락)"

    return {
        "avg_percentile": avg_pct,
        "period_percentiles": period_percentiles,
        "interpretation": interp,
    }


# ── 3순위: Strict Jaccard Index ──────────────────────────────────────────


def calc_strict_jaccard(active_factors_history: list[set[str]]) -> dict[str, Any]:
    """비중>0 팩터에 대한 엄격한 Jaccard 안정성 검사.

    Top-50이 아닌, 가중치 최적화를 거쳐 실제로 비중이 할당된
    최종 팩터 집합(스타일 수에 따라 5~14개)에만 적용한다.

    해석:
      > 0.5 -> 안정적 (핵심 팩터 유지)
      0.3~0.5 -> 보통
      < 0.3 -> 불안정 (매 리밸런싱마다 완전히 다른 조합 -> 과적합 의심)

    Note: 집합 크기가 10개 내외로 작아 Jaccard가 예민하게 반응.
    """
    if len(active_factors_history) < 2:
        return {
            "avg_jaccard": np.nan,
            "jaccard_values": [],
            "interpretation": "Tier 2 리밸런싱 2회 미만 - Strict Jaccard 계산 불가",
        }

    jaccard_values = []
    for i in range(1, len(active_factors_history)):
        prev_set = active_factors_history[i - 1]
        curr_set = active_factors_history[i]
        if not prev_set and not curr_set:
            continue
        intersection = len(prev_set & curr_set)
        union = len(prev_set | curr_set)
        jaccard = intersection / union if union > 0 else 0.0
        jaccard_values.append(jaccard)

    if not jaccard_values:
        return {
            "avg_jaccard": np.nan,
            "jaccard_values": [],
            "interpretation": "유효한 비교 구간 없음 - Strict Jaccard 계산 불가",
        }

    avg_jaccard = np.mean(jaccard_values)

    if avg_jaccard > 0.5:
        interp = f"Strict Jaccard = {avg_jaccard:.2f} > 0.5 -> 안정적 (핵심 팩터 유지)"
    elif avg_jaccard > 0.3:
        interp = f"Strict Jaccard = {avg_jaccard:.2f} (0.3~0.5) -> 보통"
    else:
        interp = f"Strict Jaccard = {avg_jaccard:.2f} < 0.3 -> 불안정 (과적합 의심: 매번 다른 조합)"

    return {
        "avg_jaccard": avg_jaccard,
        "jaccard_values": jaccard_values,
        "interpretation": interp,
    }


# ── 4순위 (보조): IS-OOS Rank Correlation ────────────────────────────────


def calc_is_oos_rank_correlation(walk_forward_result: WalkForwardResult) -> dict[str, Any]:
    """IS-OOS 팩터 순위 상관(Spearman)을 계산한다.

    각 Tier 2 리밸런싱 시점의 IS 선정 점수(rank_score=t-stat) 순위와
    해당 OOS 기간의 실현 수익률 순위를 비교한다. 실제 선정 기준과 진단을
    일치시키기 위함 (rank_score 없는 구 meta 는 cagr 로 폴백).

    Returns:
        avg_spearman, p_value, spearman_values, interpretation을 포함하는 dict.
    """
    meta_history = walk_forward_result.is_meta_history
    oos_history = walk_forward_result.oos_factor_returns_history

    if len(meta_history) < 1 or len(oos_history) < 1:
        return {
            "avg_spearman": np.nan,
            "p_value": np.nan,
            "spearman_values": [],
            "interpretation": "데이터 부족 - Rank Correlation 계산 불가",
        }

    # 각 Tier 2 리밸런싱 구간에 대해 Spearman 계산
    spearman_values = []
    p_values = []

    weight_rebal_indices = _weight_rebal_indices(walk_forward_result.rebalance_log)

    meta_idx = 0
    for k, rebal_i in enumerate(weight_rebal_indices):
        if meta_idx >= len(meta_history):
            break

        meta = meta_history[meta_idx]
        meta_idx += 1

        if meta is None or "factorAbbreviation" not in meta.columns:
            continue
        # 실제 선정 기준(rank_score=t-stat)으로 IS 순위를 매겨 선정과 진단을 일치시킨다.
        # rank_score 없는 (구) meta 는 cagr 로 폴백.
        score_col = "rank_score" if "rank_score" in meta.columns else "cagr"
        if score_col not in meta.columns:
            continue

        is_factors = meta["factorAbbreviation"].tolist()
        is_scores = meta[score_col].tolist()

        # OOS 기간: 현재 리밸런싱 ~ 다음 리밸런싱 (또는 끝) 팩터별 누적 수익률 (기하 복리)
        next_rebal_i = weight_rebal_indices[k + 1] if k + 1 < len(weight_rebal_indices) else len(oos_history)
        oos_returns_by_factor = _cum_returns(oos_history, rebal_i, next_rebal_i)

        # IS와 OOS에 공통으로 존재하는 팩터만
        common_factors = [f for f in is_factors if f in oos_returns_by_factor]
        if len(common_factors) < 3:
            continue

        is_rank = pd.Series({f: s for f, s in zip(is_factors, is_scores) if f in common_factors}).rank(ascending=False)
        oos_rank = pd.Series({f: oos_returns_by_factor[f] for f in common_factors}).rank(ascending=False)

        # Spearman 계산
        aligned = pd.DataFrame({"is_rank": is_rank, "oos_rank": oos_rank}).dropna()
        if len(aligned) < 3:
            continue

        corr, pval = stats.spearmanr(aligned["is_rank"], aligned["oos_rank"])
        spearman_values.append(corr)
        p_values.append(pval)

    if not spearman_values:
        return {
            "avg_spearman": np.nan,
            "p_value": np.nan,
            "spearman_values": [],
            "interpretation": "Rank Correlation 계산 가능한 구간 없음",
        }

    avg_spearman = np.mean(spearman_values)
    avg_p_value = np.mean(p_values)

    if avg_spearman > 0.3:
        interp = f"Rank Corr = {avg_spearman:.2f} > 0.3 -> 양의 상관 (팩터 예측력 있음)"
    elif avg_spearman > -0.1:
        interp = f"Rank Corr = {avg_spearman:.2f} ~= 0 -> IS 순위와 OOS 순위 무관"
    else:
        interp = f"Rank Corr = {avg_spearman:.2f} < 0 -> 음의 상관 (과적합 의심)"

    return {
        "avg_spearman": avg_spearman,
        "p_value": avg_p_value,
        "spearman_values": spearman_values,
        "interpretation": interp,
    }


# ── 5순위 (보조): Deflation Ratio ────────────────────────────────────────


def calc_deflation_ratio(walk_forward_result: WalkForwardResult, full_period_cagr: float) -> dict[str, Any]:
    """IS 전체 기간 CAGR 대비 OOS CAGR의 비율을 계산한다.

    Args:
        walk_forward_result: Walk-Forward 결과.
        full_period_cagr: 전체 기간 IS simulation 모드 CAGR.

    Returns:
        deflation_ratio, oos_cagr, full_period_cagr, interpretation을 포함하는 dict.
    """
    oos_perf = walk_forward_result.calc_performance()
    oos_cagr = oos_perf["cagr"]

    result: dict[str, Any] = {
        "oos_cagr": oos_cagr,
        "full_period_cagr": full_period_cagr,
    }

    if full_period_cagr == 0:
        result["deflation_ratio"] = np.nan
        result["interpretation"] = "IS CAGR = 0 - 비율 해석 불가"
    elif full_period_cagr < 0:
        result["deflation_ratio"] = np.nan
        result["interpretation"] = f"IS CAGR 음수({full_period_cagr:.4%}), OOS CAGR({oos_cagr:.4%}) - 비율 해석 스킵"
    else:
        ratio = oos_cagr / full_period_cagr
        result["deflation_ratio"] = ratio
        if ratio > 0.6:
            result["interpretation"] = f"Deflation Ratio = {ratio:.2f} > 0.6 -> 양호 (참고용)"
        elif ratio > 0.3:
            result["interpretation"] = f"Deflation Ratio = {ratio:.2f} (0.3~0.6) -> 주의 (참고용)"
        else:
            result["interpretation"] = f"Deflation Ratio = {ratio:.2f} < 0.3 -> 심각 (참고용)"

    return result


# ── 종합 리포트 ──────────────────────────────────────────────────────────


def generate_overfit_report(walk_forward_result: WalkForwardResult, full_period_cagr: float) -> dict[str, Any]:
    """과적합 종합 진단 리포트를 생성한다.

    Args:
        walk_forward_result: Walk-Forward 결과.
        full_period_cagr: 전체 기간 IS simulation 모드 CAGR.

    Returns:
        종합 진단 리포트 dict.
    """
    # 1순위: Funnel Value-Add Test
    funnel = calc_funnel_value_add(walk_forward_result)

    # 2순위: OOS Percentile Tracking
    percentile = calc_oos_percentile_tracking(walk_forward_result)

    # 3순위: Strict Jaccard
    strict_jaccard = calc_strict_jaccard(walk_forward_result.active_factors_history)

    # 4순위 (보조): IS-OOS Rank Correlation
    rank_corr = calc_is_oos_rank_correlation(walk_forward_result)

    # 5순위 (보조): Deflation Ratio
    deflation = calc_deflation_ratio(walk_forward_result, full_period_cagr)

    # OOS 성과
    cew_perf = walk_forward_result.calc_performance()
    ew_perf = walk_forward_result.calc_ew_performance()
    comparison = walk_forward_result.compare_cew_vs_ew_oos()

    n_oos = len(walk_forward_result.oos_returns)

    report = {
        # 1순위: Funnel Value-Add
        "funnel_pattern": funnel["pattern"],
        "funnel_ew_all_cagr": funnel["ew_all_cagr"],
        "funnel_ew_top50_cagr": funnel["ew_top50_cagr"],
        "funnel_cew_cagr": funnel["cew_cagr"],
        "funnel_ew_all_mdd": funnel["ew_all_mdd"],
        "funnel_ew_top50_mdd": funnel["ew_top50_mdd"],
        "funnel_cew_mdd": funnel["cew_mdd"],
        "funnel_interpretation": funnel["interpretation"],
        # 2순위: OOS Percentile Tracking
        "oos_avg_percentile": percentile["avg_percentile"],
        "oos_percentile_interpretation": percentile["interpretation"],
        # 3순위: Strict Jaccard
        "strict_jaccard": strict_jaccard["avg_jaccard"],
        "strict_jaccard_interpretation": strict_jaccard["interpretation"],
        # 4순위 (보조): IS-OOS Rank Correlation
        "is_oos_rank_spearman": rank_corr["avg_spearman"],
        "rank_corr_p_value": rank_corr["p_value"],
        "rank_corr_interpretation": rank_corr["interpretation"],
        # 5순위 (보조): Deflation Ratio
        "deflation_ratio": deflation.get("deflation_ratio", np.nan),
        "deflation_interpretation": deflation["interpretation"],
        # OOS 성과
        "oos_cagr": cew_perf["cagr"],
        "oos_mdd": cew_perf["mdd"],
        "oos_sharpe": cew_perf["sharpe"],
        "oos_calmar": cew_perf["calmar"],
        # OOS EW 성과
        "oos_ew_cagr": ew_perf["cagr"],
        "oos_ew_mdd": ew_perf["mdd"],
        "oos_ew_sharpe": ew_perf["sharpe"],
        # CEW vs EW_Top50 비교
        "cew_vs_ew_excess_cagr": comparison["excess_cagr"],
        "cew_vs_ew_win_rate": comparison["win_rate"],
        # 경고
        "warning": (
            f"OOS 기간이 {n_oos}개월({n_oos / 12:.1f}년)이므로 특정 매크로 환경에 편향되었을 수 있음. "
            f"Funnel Value-Add Test(단계별 가치 창출)를 가장 먼저 확인할 것."
        ),
        "limitation": (
            "본 백테스트는 factor-level('팩터수익 x 비중')에서 수행된다. 팩터별 전액 "
            "계상은 교차 팩터 netting 을 무시해 비용 과대 -> MP-level 실측 "
            "(실제 종목매매비용/팩터별 전액계상 = 0.574) 근거로 배수 0.6 적용 "
            "(config backtest_cost_multiplier, docs/experiments/mp_level_cost_20260703.md)."
        ),
    }

    return report


def serialize_diagnostics_csv(oos_report: dict[str, Any], path) -> None:
    """과적합 진단 리포트를 세로형(Category/Metric/Value/Interpretation) CSV로 저장.

    main.py CLI 표현 로직에서 글자보존 추출(restructure Phase 2). dashboard_data.
    parse_diagnostics/build_kpis 가 파싱하는 한국어 Category/Metric 키 계약을 그대로
    유지한다(예: ('OOS 성과 - Constrained EW','CAGR'), ('1순위 - Funnel Value-Add','패턴')).
    """
    def _pct(v):
        """비율 값을 % 포맷으로."""
        return f"{v:.4%}" if isinstance(v, float) and not np.isnan(v) else "N/A"

    def _dec(v):
        """소수 값을 그대로 표시."""
        return f"{v:.4f}" if isinstance(v, float) and not np.isnan(v) else "N/A"

    rows = [
        # 1순위: Funnel Value-Add Test
        (CAT_FUNNEL, METRIC_PATTERN, oos_report["funnel_pattern"], oos_report["funnel_interpretation"]),
        (CAT_FUNNEL, "EW_All CAGR", _pct(oos_report["funnel_ew_all_cagr"]), "전체 유효 팩터 동일가중"),
        (CAT_FUNNEL, "EW_Top50 CAGR", _pct(oos_report["funnel_ew_top50_cagr"]), "Top-50 후보군 동일가중"),
        (CAT_FUNNEL, "Constrained EW CAGR", _pct(oos_report["funnel_cew_cagr"]), "Constrained EW (Top-N + style_cap)"),
        (CAT_FUNNEL, "EW_All MDD", _pct(oos_report["funnel_ew_all_mdd"]), ""),
        (CAT_FUNNEL, "EW_Top50 MDD", _pct(oos_report["funnel_ew_top50_mdd"]), ""),
        (CAT_FUNNEL, "Constrained EW MDD", _pct(oos_report["funnel_cew_mdd"]), ""),
        # 2순위: OOS Percentile Tracking
        (CAT_OOS_PERCENTILE, "평균 백분위", _pct(oos_report["oos_avg_percentile"]), oos_report["oos_percentile_interpretation"]),
        # 3순위: Strict Jaccard
        (CAT_STRICT_JACCARD, "Strict Jaccard", _dec(oos_report["strict_jaccard"]), oos_report["strict_jaccard_interpretation"]),
        # 4순위 (보조): IS-OOS Rank Correlation
        (CAT_RANK_CORR, "IS-OOS Rank Correlation", _dec(oos_report["is_oos_rank_spearman"]), oos_report["rank_corr_interpretation"]),
        (CAT_RANK_CORR, "Rank Corr p-value", _dec(oos_report["rank_corr_p_value"]), ""),
        # 5순위 (보조): Deflation Ratio
        (CAT_DEFLATION, "Deflation Ratio", _dec(oos_report["deflation_ratio"]), oos_report["deflation_interpretation"]),
        # OOS 성과
        (CAT_OOS_CEW, "CAGR", _pct(oos_report["oos_cagr"]), ""),
        (CAT_OOS_CEW, "MDD", _pct(oos_report["oos_mdd"]), ""),
        (CAT_OOS_CEW, "Sharpe", _dec(oos_report["oos_sharpe"]), ""),
        (CAT_OOS_CEW, "Calmar", _dec(oos_report["oos_calmar"]), ""),
        (CAT_OOS_EW, "CAGR", _pct(oos_report["oos_ew_cagr"]), ""),
        (CAT_OOS_EW, "MDD", _pct(oos_report["oos_ew_mdd"]), ""),
        (CAT_OOS_EW, "Sharpe", _dec(oos_report["oos_ew_sharpe"]), ""),
        (CAT_CMP, "Excess CAGR", _pct(oos_report["cew_vs_ew_excess_cagr"]), ""),
        (CAT_CMP, "Win Rate", _pct(oos_report["cew_vs_ew_win_rate"]), ""),
        (CAT_CAUTION, "경고", "", oos_report["warning"]),
        (CAT_CAUTION, "한계점", "", oos_report["limitation"]),
    ]
    diag_df = pd.DataFrame(rows, columns=["Category", "Metric", "Value", "Interpretation"])
    diag_df.to_csv(path, index=False, encoding="utf-8-sig")
