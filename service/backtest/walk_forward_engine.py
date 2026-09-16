# -*- coding: utf-8 -*-
"""Walk-Forward 백테스트 엔진 (IS 창 = config is_window_months: 롤링 N개월 / None=expanding).

ModelPortfolioPipeline을 감싸는 Walk-Forward 오케스트레이터.
파이프라인 모듈의 내부 코드를 수정하지 않고, 순수 함수를 호출하여
IS/OOS 분할 → 규칙 학습 → 팩터 수익률 사전 계산 → OOS 적용을 수행한다.

계층적 리밸런싱 (Tiered Rebalancing):
  Tier 1 (factor_rebal_months): [2]~[3] 규칙 학습 + 전기간 팩터 수익률 사전 계산
  Tier 2 (weight_rebal_months): [4]~[6] 팩터 선정 + 가중치 최적화
  Tier 3 (매월): OOS 팩터 수익률 조회
"""
from __future__ import annotations

import logging
import time
from typing import Any

import pandas as pd
from rich.progress import track

from config import PARAM, PIPELINE_PARAMS
from service.backtest.data_slicer import get_oos_dates
from service.backtest.result_stitcher import WalkForwardResult
from service.backtest.stock_level import (
    build_stock_series,
    series_metrics,
    stock_weights_at,
)
from service.factor.factor_returns import aggregate_factor_returns
from service.factor.selection import compute_rank_score, select_factors
from service.paths import DATA_DIR, OUTPUT_DIR, UNIVERSE_DATA_DIR, dated
from service.pipeline.factor_analysis import (
    ANALYZE_COLS,
    calculate_factor_stats_batch,
    filter_and_label_factors,
)
from service.pipeline.model_portfolio import ModelPortfolioPipeline
from service.pipeline.optimization import (
    blend_deploy_weights,
    optimize_constrained_weights,
    weight_kwargs_from,
)
from service.pipeline.weight_construction import resolve_target_gross

logger = logging.getLogger(__name__)


def deploy_weights(
    weights: dict[str, float],
    factors: list[str] | set[str],
) -> dict[str, float]:
    """weights 를 factors 로 제한한 뒤 100% 재정규화 (합 1.0).

    백테스트에서 OOS 가용 factor 로 배포를 제한할 때 사용.
    대상이 없거나 합 0이면 빈 dict.
    """
    sub = {f: weights[f] for f in factors if f in weights}
    total = sum(sub.values())
    if total <= 0:
        return {}
    return {f: w / total for f, w in sub.items()}

# 최소 유효 팩터 수 — 이 미만이면 Tier 2 스킵
MIN_REQUIRED_FACTORS = 5


def rolling_is_start(all_dates: list, is_end_idx: int, window_months: int | None):
    """롤링 IS 시작 날짜를 반환한다. window 미지정(None/0)이면 None(=expanding).

    윈도우가 가용 이력보다 길면 처음부터 사용한다 (expanding 과 동일).
    """
    if not window_months:
        return None
    return all_dates[max(0, is_end_idx - window_months + 1)]


def _run_rule_learning(
    is_raw: pd.DataFrame | None,
    is_mret: pd.DataFrame | None,
    pipeline: ModelPortfolioPipeline,
    test_file: str | None = None,
    prepared: tuple | None = None,
) -> dict[str, Any]:
    """IS 데이터에서 [2]~[3] 규칙을 학습한다.

    Args:
        prepared: (factor_metadata, merged_data, factor_abbr_list, orders) 튜플.
            제공되면 _prepare_metadata 재실행을 생략하고 그대로 사용한다.
            run()에서 전체 merged 를 1회 계산 후 IS 날짜로 슬라이스해 전달하는
            용도(Tier 1 마다의 재-merge 제거). 미제공 시 is_raw/is_mret 로 병합.

    Returns:
        rule_bundle: kept_abbrs, kept_styles, dropped_sectors, label_rules
    """
    pp = pipeline.pipeline_params

    # [1] 메타데이터 병합 (IS 데이터에 대해)
    if prepared is None:
        factor_metadata, merged_data, factor_abbr_list, orders = pipeline._prepare_metadata(
            is_raw, is_mret
        )
    else:
        factor_metadata, merged_data, factor_abbr_list, orders = prepared

    # [2] 5분위 분석
    slim_data = merged_data[[c for c in ANALYZE_COLS if c in merged_data.columns]]
    factor_stats = calculate_factor_stats_batch(
        slim_data, factor_abbr_list, orders,
        test_mode=bool(test_file),
        min_sector_stocks=pp["min_sector_stocks"],
        # 커버리지 필터는 IS 학습에만 적용 (규칙으로서 kept_abbrs 에 반영됨).
        # 전체 데이터 사전계산(factor_stats_full)에는 미적용 — IS/full 커버리지가
        # 임계 근처에서 엇갈릴 때 kept 팩터의 stats 가 사라지는 불일치 방지.
        min_coverage_pct=float(pp.get("min_coverage_pct", 0.0)),
    )

    # [3] 섹터 필터링 + L/N/S 라벨링
    factor_name_list = factor_metadata.factorName.tolist()
    style_name_list = factor_metadata.styleName.tolist()
    kept_abbrs, _kept_names, kept_styles, _kept_idx, dropped_sec, filtered_data = filter_and_label_factors(
        factor_abbr_list, factor_name_list, style_name_list, factor_stats,
        spread_threshold_pct=pp["spread_threshold_pct"],
    )

    # IS 전용 규칙만 보존 (제거 섹터 + 분위별 L/N/S 라벨). IS 프레임(factor_stats /
    # filtered_data)은 여기서 버려져 Tier 1 주기 동안 메모리를 점유하지 않는다.
    return {
        "kept_abbrs": kept_abbrs,
        "kept_styles": kept_styles,
        "dropped_sectors": {abbr: secs for abbr, secs in zip(kept_abbrs, dropped_sec) if secs},
        "label_rules": {
            abbr: fd.groupby("quantile", observed=False)["label"].first().to_dict()
            for abbr, fd in zip(kept_abbrs, filtered_data)
        },
    }


def _apply_rules_and_aggregate(
    factor_stats_full: list,
    factor_abbr_list: list[str],
    rule_bundle: dict[str, Any],
    pipeline: ModelPortfolioPipeline,
    out_frames: dict | None = None,
    horizon=None,
) -> pd.DataFrame:
    """IS에서 학습한 규칙을 (사전 계산된) 전체 데이터 5분위 통계에 적용하고 팩터 수익률을 사전 계산한다.

    horizon: 이 Tier 1 이 실제로 소비하는 마지막 월 (+1, 턴오버 lookahead 분). 지정 시
    라벨 프레임을 ddt <= horizon 으로 잘라 미래 구간 계산을 생략한다 (2026-09-16).
    부수 효과로 "미래 월의 롱/숏 소실 -> 팩터 전체 탈락"(aggregate 의 NaN 열 제거)이
    현재 시점 선정에 영향을 주던 look-ahead 도 사라진다.

    Tier 1 핵심: 전체 데이터의 횡단면 5분위 랭킹(안전)에 IS 전용 규칙
    (dropped_sectors, label_rules)을 적용하여 aggregate_factor_returns를
    1회만 실행, 전기간 팩터 수익률 행렬을 생성한다.

    factor_stats_full 은 윈도우 불변(횡단면 랭킹)이므로 run()에서 1회만 계산해
    Tier 1 마다 재사용한다 (기존엔 Tier 1 마다 동일 raw_data 로 재계산 -> 결과 동일).
    여기서는 IS 전용 규칙 적용 + aggregate_factor_returns 만 수행한다.

    OOS look-ahead bias 방지:
      - 5분위 랭킹: 횡단면(같은 날짜·섹터 내 순위) → 시계열 오염 없음, 전체 데이터 안전
      - 섹터 제거: IS에서 학습한 dropped_sectors 직접 적용 (재계산 아님)
      - L/N/S 라벨: IS에서 학습한 label_rules 직접 매핑 (재계산 아님)

    Args:
        factor_stats_full: run()에서 사전 계산된 전체 데이터 5분위 통계
            (calculate_factor_stats_batch 결과; factor_abbr_list 와 인덱스 정렬).
        factor_abbr_list: factor_stats_full 인덱스에 대응하는 팩터 약어 리스트.

    Returns:
        precomputed_ret_df: (전체 월 × 유효 팩터) 수익률 행렬
    """
    pp = pipeline.pipeline_params

    # [3] IS 규칙을 (사전 계산된) 전체 데이터 5분위 통계에 적용 (재학습 아님)
    # factor_abbr_list → factor_stats_full 인덱스 매핑
    abbr_to_stats_idx = {a: i for i, a in enumerate(factor_abbr_list)}

    kept_abbrs = rule_bundle["kept_abbrs"]
    dropped_sectors = rule_bundle["dropped_sectors"]
    label_rules = rule_bundle["label_rules"]

    valid_abbrs: list[str] = []
    valid_filtered: list[pd.DataFrame] = []

    for abbr in kept_abbrs:
        if abbr not in abbr_to_stats_idx:
            continue

        stats_idx = abbr_to_stats_idx[abbr]
        stats = factor_stats_full[stats_idx]
        if stats[0] is None:
            continue

        raw_df = stats[3]  # merged_df (quantile 컬럼 포함, 횡단면 안전)

        # IS에서 학습한 dropped_sectors 적용
        dropped = dropped_sectors.get(abbr, [])
        if dropped:
            raw_clean = raw_df[~raw_df["sec"].isin(dropped)].copy()
        else:
            raw_clean = raw_df.copy()

        if raw_clean.empty:
            continue

        # IS에서 학습한 label_rules 적용 (quintile -> L/N/S 매핑)
        labels = label_rules.get(abbr, {})
        if not labels:
            continue

        raw_clean["label"] = raw_clean["quantile"].map(labels)
        merged = raw_clean.dropna(subset=["label"])
        if horizon is not None:
            merged = merged[merged["ddt"] <= horizon]

        if merged.empty:
            continue

        # L/S 양쪽이 모두 존재해야 롱-숏 포트폴리오 구성 가능
        has_long = (merged["label"] == 1).any()
        has_short = (merged["label"] == -1).any()
        if not (has_long and has_short):
            logger.debug("Factor %s skipped - missing long or short after IS rule application", abbr)
            continue

        valid_abbrs.append(abbr)
        valid_filtered.append(merged)

    if not valid_abbrs:
        logger.warning("No valid factors after applying IS rules to full data")
        return pd.DataFrame()

    # 종목단 재구성용 프레임 보존 (2026-08-19): 노출/배수/TE 시계열 산출에 필요.
    # 호출부가 out_frames 를 넘기면 abbr -> 라벨 프레임 매핑을 채워준다 (반환 시그니처
    # 불변 -> 기존 호출부/테스트 영향 없음).
    if out_frames is not None:
        out_frames.clear()
        out_frames.update(dict(zip(valid_abbrs, valid_filtered)))

    # aggregate_factor_returns 1회 실행 (전기간)
    precomputed_ret_df = aggregate_factor_returns(
        valid_filtered, valid_abbrs,
        backtest_start=pp["backtest_start"],
        cost_bps=pp["transaction_cost_bps"],
    )

    return precomputed_ret_df


def _resolve_backtest_cost_bps(pp: dict) -> float:
    """factor-level 백테스트의 유효 매매비용(bps)을 계산한다.

    팩터별 전액 계상은 교차 팩터 netting(실거래는 MP 합산 후 월 1회 매매)을 무시해
    비용을 과대평가한다. MP-level 실측 netting ratio = 0.574 (실제 종목매매비용 /
    팩터별 전액계상 비용, docs/experiments/mp_level_cost_20260703.md) -> 기본 배수
    0.6 으로 근사 (현 MXWO 기본 10bp x 0.6 = 6bp). 상세는 config.py 주석 참조.

    - 배수는 config PIPELINE_PARAMS['backtest_cost_multiplier'] 로 조정한다 (figure 관리).
    - 운영 mp 파이프라인에는 적용되지 않는다 (이 함수는 백테스트 엔진 전용).
    """
    # 키 폴백 없음 (2026-08-25): 구 fallback 20.0 은 config(10bp)와 어긋나 키 누락 시
    # 조용히 2배 비용으로 돌았음. 호출자는 모두 PIPELINE_PARAMS 기반 -> fail-loud.
    base = float(pp["transaction_cost_bps"])
    mult = float(pp.get("backtest_cost_multiplier", 0.6))
    return base * mult


def _run_weight_optimization(
    ret_df_is: pd.DataFrame,
    meta: pd.DataFrame,
    pp: dict,
) -> tuple[dict[str, float], pd.DataFrame]:
    """[6] 가중치 계산.

    Returns:
        (weights_dict, meta) -- weights_dict: {factor_abbr: weight}
    """
    style_map = meta.set_index("factorAbbreviation")["styleName"]
    factor_list = meta["factorAbbreviation"].tolist()
    style_list = [style_map[f] for f in factor_list]
    ret_subset = ret_df_is[factor_list]

    # 파라미터 추출은 weight_kwargs_from 단일 출처 (2026-08-25 — mp 와 공유)
    _best_stats, weights_tbl = optimize_constrained_weights(
        ret_subset, style_list, **weight_kwargs_from(pp),
    )

    weights_dict = dict(zip(weights_tbl["factor"], weights_tbl["fitted_weight"]))
    return weights_dict, meta


class WalkForwardEngine:
    """Walk-Forward 백테스트 오케스트레이터.

    기존 파이프라인 모듈의 내부 코드를 수정하지 않고,
    데이터를 메모리에 1회만 로드하고 날짜 필터로 IS 범위를 제어한다.

    Args:
        min_is_months: 최소 IS 기간 (기본 36).
        factor_rebal_months: Tier 1 리밸런싱 주기 (기본 6).
        weight_rebal_months: Tier 2 리밸런싱 주기 (기본 3).
        top_factors: 상위 팩터 수. None(기본)이면 config top_factor_count (production parity).
        selection_hysteresis: 선정 히스테리시스 margin (rank_score 단위,
            기본 0.0=off). 챌린저가 기존 보유 팩터를 이 격차 이상 이겨야 교체.
    """

    def __init__(
        self,
        min_is_months: int = 36,
        factor_rebal_months: int = 6,
        weight_rebal_months: int = 3,
        top_factors: int | None = None,
        selection_hysteresis: float = 0.0,
        pipeline_params_override: dict | None = None,
        is_window_months: int | None = None,
    ):
        self.min_is_months = min_is_months
        self.factor_rebal_months = factor_rebal_months
        self.weight_rebal_months = weight_rebal_months
        self.top_factors = top_factors
        self.selection_hysteresis = selection_hysteresis
        self.pipeline_params_override = pipeline_params_override
        # 롤링 IS 윈도우 (개월). None=expanding(기존). 지정 시 Tier 1 규칙 학습과
        # Tier 2 선정/가중 IS 를 모두 최근 N개월로 제한 — 레짐 적응용 (2026-07-28).
        self.is_window_months = is_window_months

    def run(
        self,
        start_date: str,
        end_date: str,
        test_file: str | None = None,
    ) -> WalkForwardResult:
        """Walk-Forward 백테스트를 실행한다.

        Args:
            start_date: 데이터 시작 날짜.
            end_date: 데이터 종료 날짜.
            test_file: 테스트 모드 파일 (소량 CSV).

        Returns:
            WalkForwardResult: OOS 결과를 담은 컨테이너.
        """
        t0 = time.time()
        logger.info("Walk-Forward backtest starting: %s ~ %s", start_date, end_date)

        # pipeline_params 커스텀 (config의 optimization_mode 유지)
        # 순서: PIPELINE_PARAMS 기본 -> CLI top_factors(지정 시)로 top_factor_count 덮어씀
        # -> override 적용 (override 가 최우선). CLI 미지정(None)이면 config 값 그대로
        # (구 기본 50 하드코딩은 config 를 바꿔도 백테스트가 못 따라가는 parity 구멍이었음).
        pp = dict(PIPELINE_PARAMS)
        if self.top_factors is not None:
            pp["top_factor_count"] = self.top_factors
        if self.pipeline_params_override:
            pp.update(self.pipeline_params_override)
        if pp["optimization_mode"] == "hardcoded":
            pp["optimization_mode"] = "equal_weight"  # hardcoded는 backtest에서 사용 불가

        # factor-level 백테스트 비용 보정: 종목비용 x backtest_cost_multiplier (기본 0.6 -> 10bp x 0.6 = 6bp).
        # 팩터별 전액 계상은 교차 팩터 netting(실거래는 MP 합산 후 월 1회 매매)을 무시해 과대평가
        # -> MP-level 실측 netting ratio 0.574 로 근사 (_resolve_backtest_cost_bps 참조).
        # 이 pp 가 이후 ModelPortfolioPipeline / aggregate_factor_returns 전체에 적용됨.
        pp["transaction_cost_bps"] = _resolve_backtest_cost_bps(pp)

        # 1. 데이터 1회 로딩 — pipeline 인스턴스를 통해 [1] 실행
        pipeline = ModelPortfolioPipeline(
            config=PARAM,
            factor_info_path=DATA_DIR / "factor_info.csv",
            is_test=bool(test_file),
            pipeline_params=pp,
        )
        raw_data, market_return_df, start_date, end_date = pipeline._load_data(
            start_date, end_date, test_file
        )

        all_dates = sorted(raw_data["ddt"].unique())
        oos_dates = get_oos_dates(all_dates, self.min_is_months)
        logger.info(
            "Data loaded: %d months total, %d OOS months (min_is=%d)",
            len(all_dates), len(oos_dates), self.min_is_months,
        )

        # 1-b. 전체 데이터 횡단면 5분위 통계 사전 계산 (Tier 1 루프 불변값)
        #   분위 랭킹은 같은 날짜·섹터 내 횡단면 순위 -> 윈도우와 무관하게 불변(시계열 오염 없음).
        #   기존엔 _apply_rules_and_aggregate 가 Tier 1 마다 동일 raw_data 로 재계산했으나
        #   결과가 매번 동일하므로 1회만 계산해 재사용한다 (출력 byte-identical, Tier1 횟수배 단축).
        _meta_full, merged_full, factor_abbr_list_full, orders_full = pipeline._prepare_metadata(
            raw_data, market_return_df
        )
        slim_full = merged_full[[c for c in ANALYZE_COLS if c in merged_full.columns]]
        factor_stats_full = calculate_factor_stats_batch(
            slim_full, factor_abbr_list_full, orders_full,
            test_mode=bool(test_file),
            min_sector_stocks=pp["min_sector_stocks"],
        )

        # 2. 캐시 초기화
        stock_frames: dict = {}
        stock_monthly: list = []
        cached_rule_bundle: dict | None = None
        precomputed_ret_df: pd.DataFrame | None = None
        cached_weights: dict[str, float] | None = None
        cached_meta: pd.DataFrame | None = None
        cached_selected_factors: list[str] | None = None
        cached_top50_factors: list[str] | None = None
        cached_is_cew_cagr: float = 0.0  # IS 구간 CEW CAGR (Deflation Ratio용)

        results: list[dict[str, Any]] = []

        # 3. OOS 루프
        for i, oos_date in enumerate(track(oos_dates, description="Walk-Forward OOS...")):
            is_end_idx = self.min_is_months + i - 1
            is_end_date = all_dates[is_end_idx]
            is_start_date = rolling_is_start(all_dates, is_end_idx, self.is_window_months)

            is_rule_rebal = False
            is_weight_rebal = False

            # ── Tier 1: 규칙 학습 + 팩터 수익률 사전 계산 ──
            if cached_rule_bundle is None or i % self.factor_rebal_months == 0:
                is_rule_rebal = True
                # IS 는 캐시된 전체 merged 를 날짜 슬라이스해 재사용 (Tier 1 마다의 재-merge/copy 제거).
                #   merged_full[ddt<=cutoff] == _prepare_metadata(is_raw)  (동일 행·순서, inner merge 키에 ddt 포함)
                #   -> byte-identical. factor_metadata/abbr/orders 도 전체와 동일(factor_info.csv 고정).
                # 경계 계약: <= inclusive (data_slicer.slice_data_by_date 와 동일 — < 로 바꾸면
                #   IS 가 1개월 짧아지는 off-by-one. 계약 테스트는 test_oos_purity 참조).
                merged_is = merged_full[merged_full["ddt"] <= pd.Timestamp(is_end_date)]
                if is_start_date is not None:
                    # 롤링 IS: lag(shift 1) 소실 방지를 위해 시작 1개월 이전부터 슬라이스
                    lag_start = pd.Timestamp(is_start_date) - pd.DateOffset(months=1, days=5)
                    merged_is = merged_is[merged_is["ddt"] >= lag_start]
                prepared_is = (_meta_full, merged_is, factor_abbr_list_full, orders_full)
                cached_rule_bundle = _run_rule_learning(
                    None, None, pipeline, test_file, prepared=prepared_is,
                )

                # 사전 계산된 전체 데이터 5분위 통계에 규칙 적용 + aggregate 1회 실행.
                # 이 Tier 1 이 쓰는 마지막 월 = OOS i+factor_rebal_months-1, 턴오버 lookahead 로 +1.
                horizon_idx = min(len(all_dates) - 1,
                                  self.min_is_months + i + self.factor_rebal_months)
                precomputed_ret_df = _apply_rules_and_aggregate(
                    factor_stats_full, factor_abbr_list_full, cached_rule_bundle, pipeline,
                    out_frames=stock_frames, horizon=pd.Timestamp(all_dates[horizon_idx]),
                )

                if precomputed_ret_df.empty:
                    logger.warning("OOS %s: precomputed_ret_df empty, skipping", oos_date)
                    continue

                # 첫 행 기준점 설정
                precomputed_ret_df.loc[precomputed_ret_df.index[0]] = 0.0
                precomputed_ret_df = precomputed_ret_df.sort_index()

                # 새 규칙 -> 메타/Top50 무효화. 가중치/선정은 carry:
                # 실제 포트폴리오는 Tier 1 에서 리셋되지 않으므로 (production parity)
                # 스무딩 prev/히스테리시스 incumbency 상태를 유지한다.
                # Tier 2 는 아래 is_rule_rebal 조건으로 강제 재실행 -> stale weights 없음.
                cached_meta = None
                cached_top50_factors = None

            if precomputed_ret_df is None or precomputed_ret_df.empty:
                continue

            # ── Tier 2: 팩터 선정 + 가중치 최적화 ──
            if cached_weights is None or is_rule_rebal or i % self.weight_rebal_months == 0:
                is_weight_rebal = True

                # IS 구간 슬라이스 (aggregate 재실행 불필요)
                ret_df_is = precomputed_ret_df[precomputed_ret_df.index <= is_end_date]
                if is_start_date is not None:
                    ret_df_is = ret_df_is[ret_df_is.index >= pd.Timestamp(is_start_date)]
                ret_df_is = ret_df_is.copy()

                if len(ret_df_is) < 3:
                    logger.warning("OOS %s: IS 구간 너무 짧음 (%d), 이전 가중치 유지", oos_date, len(ret_df_is))
                    if cached_weights is None:
                        continue
                else:
                    ret_df_is.iloc[0] = 0.0  # 기준점

                    # 0 수익률 월 필터 (첫 행 기준점 0 제외 — mp evaluate_universe 와 동일 규칙, 2026-09-16)
                    valid = ret_df_is.columns[(ret_df_is.iloc[1:] == 0).sum() <= pp["max_zero_return_months"]]
                    ret_df_is = ret_df_is[valid]

                    if len(ret_df_is.columns) < MIN_REQUIRED_FACTORS:
                        logger.warning(
                            "OOS %s: 유효 팩터 %d개 < %d, 이전 가중치 유지",
                            oos_date, len(ret_df_is.columns), MIN_REQUIRED_FACTORS,
                        )
                        if cached_weights is None:
                            continue
                    else:
                        # 스타일 맵 구성 (IS 전용 rule_bundle 기반)
                        style_map_full: dict[str, str] = {}
                        if cached_rule_bundle:
                            kept_abbrs = cached_rule_bundle.get("kept_abbrs", []) or []
                            kept_styles = cached_rule_bundle.get("kept_styles", []) or []
                            for abbr, style in zip(kept_abbrs, kept_styles):
                                style_map_full[abbr] = style

                        # 팩터 랭킹 + Top-N 선정 (production mp 와 동일 로직 공유)
                        selected, meta_top, rank_topn = self._rank_and_select(
                            ret_df_is, style_map_full, pp, cached_selected_factors,
                        )
                        # dedup 이전 순수 Top-N (equal_weight 에선 선정=weight>0 이라
                        # 선정 집합을 넣으면 EW_Top50 곡선이 EW(선정)와 중복 -> funnel
                        # 의 "1차 랭킹 필터" 단계가 퇴화한다. 2026-07 복원.
                        cached_top50_factors = rank_topn
                        ret_df_selected = ret_df_is[selected]

                        try:
                            # TS 틸트는 _run_weight_optimization 내부(캡 이전)에서 적용
                            # (2026-08-06 순서 교정 — 캡 준수 보장)
                            raw_new_weights, cached_meta = _run_weight_optimization(
                                ret_df_selected, meta_top, pp,
                            )
                        except (ValueError, RuntimeError) as e:
                            logger.warning("OOS %s: weight optimization failed: %s — 이전 가중치 유지", oos_date, e)
                            if cached_weights is None:
                                continue
                            raw_new_weights = None

                        if raw_new_weights is not None:
                            # IS 구간 MP CAGR 계산 (Deflation Ratio용)
                            is_months = len(ret_df_selected) - 1
                            if is_months > 0:
                                is_weighted_ret = sum(
                                    ret_df_selected[f] * raw_new_weights.get(f, 0)
                                    for f in ret_df_selected.columns if f in raw_new_weights
                                )
                                is_cum = (1 + is_weighted_ret).cumprod().iloc[-1]
                                cached_is_cew_cagr = is_cum ** (12 / is_months) - 1

                            # 구 step_smooth(step=1.0) 동작 보존(출력 byte 동일): 정렬 + 합 1.0 재정규화.
                            _order = sorted(raw_new_weights)
                            _wscale = 1.0 / sum(raw_new_weights[f] for f in _order)
                            _target = {f: raw_new_weights[f] * _wscale for f in _order}
                            cached_weights = blend_deploy_weights(
                                _target, cached_weights, float(pp.get("deploy_step", 1.0)))
                            cached_selected_factors = list(cached_weights.keys())

            if cached_weights is None or cached_selected_factors is None:
                continue

            # ── Tier 3: OOS 1개월 팩터 수익률 (조회만) ──
            if oos_date not in precomputed_ret_df.index:
                logger.warning("OOS date %s not in precomputed_ret_df, skipping", oos_date)
                continue

            # 종목단 재구성 (2026-08-19): 프레임이 살아있는 이 시점에 월별 종목
            # 순비중만 뽑아 둔다 (프레임 자체는 무거워 보관하지 않음).
            if stock_frames:
                w_t, r_t = stock_weights_at(
                    stock_frames, cached_weights, oos_date,
                    pd.Timestamp(pp["backtest_start"]),
                    sector_short_cap=pp.get("sector_short_cap"),
                )
                if len(w_t):
                    stock_monthly.append((oos_date, w_t, r_t))

            record = self._assemble_oos_record(
                oos_date, precomputed_ret_df, cached_weights, cached_meta,
                cached_top50_factors, cached_is_cew_cagr, is_rule_rebal, is_weight_rebal,
            )
            if record is None:
                continue
            results.append(record)

        elapsed = time.time() - t0
        logger.info("Walk-Forward completed: %d OOS months in %.1fs", len(results), elapsed)

        # 전기간 팩터 수익률 행렬 저장 (마지막 Tier 1 규칙 기준 — 상관 국면 분석/viz용.
        # 성과 산출물이 아니라 상관 구조 참고 데이터. 테스트 모드는 생략)
        if not test_file and precomputed_ret_df is not None and not precomputed_ret_df.empty:
            try:
                precomputed_ret_df.to_csv(dated(
                    OUTPUT_DIR / "factor_returns_matrix.csv",
                    precomputed_ret_df.index.max()))
            except OSError as e:
                logger.warning("factor_returns_matrix 저장 실패 (%s)", e)

        # 배포 기준 시계열 저장 (노출/배수/TE) — 종목단 netting 반영
        if not test_file and stock_monthly:
            try:
                # 목표 노출 일정(Active Risk 조정 이력) 반영 — 시점별로 다른 규모
                _tg_path, _tg_def = UNIVERSE_DATA_DIR / "mp_target_gross.csv", pp.get("mp_target_gross")
                series = build_stock_series(
                    stock_monthly, float(PIPELINE_PARAMS["transaction_cost_bps"]),
                    lambda d: resolve_target_gross(d, _tg_path, _tg_def),
                    benchmark=(PARAM["benchmark"] if pp.get("bm_short_cap") else None),
                )
                series.to_csv(dated(OUTPUT_DIR / "stock_level_series.csv", series.index.max()))
                m = series_metrics(series)
                logger.info(
                    "배포 기준(노출 %.1f%%): Sharpe %.3f / CAGR %.2f%% / MDD %.2f%% / TE %.2f%% / IR %.2f",
                    m["avg_long_exposure"] * 100, m["sharpe"], m["cagr"] * 100,
                    m["mdd"] * 100, m["tracking_error"] * 100, m["info_ratio"])
            except Exception as e:  # 산출물 보존 우선
                logger.warning("stock_level_series 생성 실패 (%s)", e)

        return WalkForwardResult(results)

    def _rank_and_select(self, ret_df_is, style_map_full, pp, incumbents):
        """IS 구간 팩터 랭킹 + Top-N 선정 (cluster dedup + 히스테리시스).

        production mp(evaluate_universe)와 동일 로직을 공유한다. 순수 계산이며
        연산 순서를 run() 의 기존 인라인 블록과 동일하게 보존한다(수치 동일성).

        Returns:
            (selected, meta_top, rank_topn): 선정 팩터 리스트, 선정 메타 DataFrame,
            클러스터 dedup **이전** 순수 rank_score 상위 top_n 리스트
            (Funnel Value-Add 의 B 단계 = 1차 랭킹 필터 곡선용).
        """
        months = len(ret_df_is) - 1
        cum = (1 + ret_df_is).cumprod().iloc[-1]
        cagr_series = cum ** (12 / months) - 1

        ranking_method = pp.get("factor_ranking_method", "cagr")
        monthly_rets = ret_df_is.iloc[1:]  # 첫 행(기준점 0) 제외

        # production mp (_evaluate_universe) 와 동일 로직 공유
        rank_score = compute_rank_score(monthly_rets, ranking_method, style_map_full)

        meta_df = pd.DataFrame({
            "factorAbbreviation": ret_df_is.columns,
            "cagr": cagr_series.values,
            "rank_score": rank_score.reindex(ret_df_is.columns).values,
        })

        meta_df["styleName"] = meta_df["factorAbbreviation"].map(style_map_full).fillna("Unknown")
        meta_df["factorName"] = meta_df["factorAbbreviation"]

        meta_df["rank_style"] = meta_df.groupby("styleName")["rank_score"].rank(ascending=False)
        meta_df["rank_total"] = meta_df["rank_score"].rank(ascending=False)
        meta_df = meta_df.sort_values("rank_score", ascending=False).reset_index(drop=True)

        top_n = min(pp["top_factor_count"], len(meta_df))
        # dedup/히스테리시스 이전의 순수 rank_score Top-N (진단 곡선 EW_Top50 용).
        # meta_df 는 rank_score 내림차순 정렬 상태.
        rank_topn = meta_df["factorAbbreviation"].head(top_n).tolist()

        # 선정(절단/클러스터 dedup) + 히스테리시스 — production mp 와 select_factors() 공유
        selected = select_factors(
            monthly_rets, meta_df.set_index("factorAbbreviation")["rank_score"], pp, top_n,
            incumbents=incumbents, margin=self.selection_hysteresis,
        )
        meta_top = meta_df.set_index("factorAbbreviation").loc[selected].reset_index()
        return selected, meta_top, rank_topn

    def _assemble_oos_record(self, oos_date, precomputed_ret_df, cached_weights, cached_meta,
                             cached_top50_factors, cached_is_cew_cagr, is_rule_rebal, is_weight_rebal):
        """단일 OOS 시점 수익률을 계산하고 결과 레코드 dict 를 만든다.

        가용 팩터가 없으면 None 반환(호출부에서 continue). 연산 순서는 기존
        인라인 블록과 동일하게 보존한다 (walk_forward_results.csv 재현성).
        """
        # 결정적 출력: 합산(OOS 수익률/정규화) 순서를 팩터명으로 고정해 float
        # 말단자릿수까지 안정화한다.
        available_factors = sorted(f for f in cached_weights if f in precomputed_ret_df.columns)
        if not available_factors:
            return None

        oos_factor_returns = precomputed_ret_df.loc[oos_date, available_factors]

        # 전체 팩터 OOS 수익률 (Funnel Value-Add + Percentile Tracking용)
        oos_all_factor_returns = precomputed_ret_df.loc[oos_date]

        # 가용 팩터에 맞춰 가중치 정규화 (production mp 와 공유 로직)
        avail_weights = deploy_weights(cached_weights, available_factors)
        if not avail_weights:
            logger.warning("OOS %s: deploy_weights 빈 결과 (available_factors=%d) - 0%% 수익월로 처리",
                           oos_date, len(available_factors))

        oos_return = sum(oos_factor_returns[f] * avail_weights.get(f, 0) for f in available_factors)
        oos_ew_return = oos_factor_returns.mean()

        # 팩터별 기여도 (비중 x 당월 수익; 합 = oos_return) — 성과 원천 분석의 정식
        # 산출물. 사후 재구성(최종 윈도우 수익 행렬 x 비중)은 look-ahead 로 왜곡되므로
        # (corr 0.68, 2025년 2배 과대 실측) 당시 규칙 기준으로 여기서 기록한다 (2026-08-28).
        contributions = {f: oos_factor_returns[f] * avail_weights.get(f, 0)
                         for f in available_factors}

        return {
            "date": oos_date,
            "oos_return": oos_return,
            "oos_ew_return": oos_ew_return,
            "oos_factor_returns": oos_factor_returns.to_dict(),
            "contributions": contributions,
            "weights": dict(cached_weights),
            "is_meta": cached_meta.copy() if cached_meta is not None else None,
            "is_rule_rebal": is_rule_rebal,
            "is_weight_rebal": is_weight_rebal,
            "oos_all_factor_returns": oos_all_factor_returns.to_dict(),
            "top50_factors": list(cached_top50_factors) if cached_top50_factors else [],
            "active_factors": [f for f, w in cached_weights.items() if w > 0],
            "is_cew_cagr": cached_is_cew_cagr,
        }
