# -*- coding: utf-8 -*-
"""스타일 캡 하 가중치 최적화 모듈.

스타일 캡(기본 25%) 제약 하에서 팩터별 가중치를 결정한다.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _get_hardcoded_weights() -> tuple[pd.DataFrame, pd.DataFrame]:
    """프로덕션용 고정 가중치를 반환한다.

    ~2026-01 포트폴리오까지 적용. Valuation 강제로 4%로 내림.
    (이 주석 지우지 말것! DO NOT DELETE THIS COMMENT!)
    """
    best_stats = pd.DataFrame(
        {c: [np.nan] for c in ["cagr", "mdd", "rank_cagr", "rank_mdd", "rank_total"]}
    )

    # 가중치를 CSV에서 로드 (과거 버전은 git history 참조)
    csv_path = Path(__file__).resolve().parent.parent.parent / "data" / "hardcoded_weights.csv"
    weights_tbl = pd.read_csv(csv_path, float_precision="round_trip")
    weights_tbl["fitted_weight"] = weights_tbl["raw_weight"]
    weights_tbl = weights_tbl[weights_tbl["raw_weight"] > 0].sort_values("raw_weight", ascending=False).reset_index(drop=True)

    return best_stats, weights_tbl


def _redistribute_to_cap(
    x: np.ndarray,
    styles_arr: np.ndarray,
    uniq_styles: np.ndarray,
    style_cap: float,
    tol: float,
) -> np.ndarray:
    """스타일 합이 cap 을 넘지 않도록 비례 축소 + 재정규화 (수렴까지 최대 10회).

    연산 순서는 기존 인라인 루프와 동일 (byte 보존).
    """
    for _ in range(10):
        for s in uniq_styles:
            mask_s = styles_arr == s
            style_w = x[mask_s].sum()
            if style_w > style_cap + tol:
                x[mask_s] *= style_cap / style_w
        x /= x.sum()
        if all(x[styles_arr == s].sum() <= style_cap + tol for s in uniq_styles):
            break
    # 10회 내 미수렴 시 위반 스타일 보고 (float32 오차 허용 1e-6)
    violations = {
        s: float(x[styles_arr == s].sum())
        for s in uniq_styles if x[styles_arr == s].sum() > style_cap + 1e-6
    }
    if violations:
        logger.warning(
            "style_cap %.2f violated after redistribution: %s",
            style_cap, {s: round(v, 4) for s, v in violations.items()},
        )
    return x


def _equal_weight_allocation(
    rtn_df: pd.DataFrame,
    style_list: list[str],
    style_cap: float,
    tol: float,
    test_mode: bool,
    base_weights: np.ndarray | None = None,
    ts_mom_window: int | None = None,
    ts_mom_scale: float = 0.5,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """초기 가중(기본 1/N, base_weights 지정 시 그 값) + TS 틸트 + 스타일 캡 재분배."""
    n_factors = rtn_df.shape[1]
    factors = rtn_df.columns.to_numpy()
    styles_arr = np.asarray(style_list)

    if base_weights is None:
        w = np.ones(n_factors, dtype=np.float32) / n_factors
    else:
        w = base_weights.astype(np.float32).copy()
        w /= w.sum()

    # TS 모멘텀 틸트는 스타일 캡 재분배 '이전'(base 단계)에 적용 — 캡 준수 보장.
    # (구 구현은 캡 이후 틸트+재정규화라 스타일 합이 캡을 초과했음. 2026-08-06
    # 순서 교정 — main/MXCN1A 와 동일. 예: 2026-06-30 EQ 26.07% -> 25% 준수)
    if ts_mom_window:
        tilted = apply_ts_momentum_tilt(
            dict(zip(factors, w)), rtn_df, ts_mom_window, ts_mom_scale)
        w = np.array([tilted[f] for f in factors], dtype=np.float32)
        w /= w.sum()

    # 스타일 캡 재분배 (수렴까지 반복)
    w_pre_cap = w.copy()  # 캡 적용 전 비중 보존 (weights_tbl.raw_weight — 캡 효과 시각화용)
    uniq_styles = np.unique(styles_arr)
    if not test_mode:
        # feasibility 가드: n_styles x cap < 100% 면 제약 자체가 불가능
        # (예: 스타일 3개 x 25% = 75%). 동작은 기존과 동일 - 경고만 남긴다.
        if len(uniq_styles) * style_cap < 1.0 - tol:
            logger.warning(
                "style_cap %.2f infeasible with %d styles (max %.0f%% < 100%%) - constraint will be violated",
                style_cap, len(uniq_styles), len(uniq_styles) * style_cap * 100,
            )
        w = _redistribute_to_cap(w, styles_arr, uniq_styles, style_cap, tol)

    weights_tbl = pd.DataFrame({
        "factor": factors,
        "raw_weight": w_pre_cap,   # 캡 적용 전 (2026-07-30부터 실제 pre-cap; 이전엔 fitted 와 동일했음)
        "styleName": styles_arr,
        "fitted_weight": w,        # 캡 적용 후 (배포/백테스트가 쓰는 값 — 기존 동작 불변)
    })

    # CAGR/MDD 계산 (기록용)
    port_np = rtn_df.to_numpy(dtype=np.float32)
    n_months = port_np.shape[0]
    sim = port_np @ w
    cum = np.cumprod(1 + sim)
    ann_exp = 12 / max(n_months - 1, 1)
    cagr_val = float(cum[-1] ** ann_exp - 1)
    mdd_val = float((cum / np.maximum.accumulate(cum) - 1).min())

    best_stats = pd.DataFrame({
        "cagr": [cagr_val], "mdd": [mdd_val],
        "rank_cagr": [np.nan], "rank_mdd": [np.nan], "rank_total": [np.nan],
    })

    logger.info("Equal-weight allocation: %d factors, CAGR=%.4f, MDD=%.4f", n_factors, cagr_val, mdd_val)
    return best_stats, weights_tbl


def _solve_erc_ccd(cov: np.ndarray, max_sweeps: int = 500, tol_w: float = 1e-12) -> np.ndarray:
    """정식 ERC 해 (Spinu 볼록형의 CCD 반복, 2026-07-30 교체).

    min (1/2) w'Σw - λ Σ ln w_i 의 1계 조건 w_i(Σw)_i = λ (모든 i 동일) 가 곧 ERC.
    좌표별 닫힌해 w_i = (-b_i + sqrt(b_i^2 + 4 σ_ii λ)) / (2 σ_ii), b_i = Σ_{j≠i} σ_ij w_j.
    sqrt 항이 |b_i| 보다 항상 크므로 **음의 상관(b_i<0)에서도 양수 비중이 보장**되고,
    그런 팩터는 (Σw)_i 가 작아 오히려 큰 비중을 받는다 (분산 재료 우대 — 구 곱셈
    반복법이 이들을 0으로 붕괴시키던 결함의 교정). PD cov 에서 해는 유일(스케일 제외).
    """
    n = cov.shape[0]
    if n == 1:
        return np.array([1.0])
    diag = np.maximum(np.diag(cov), 1e-18)
    lam = float(np.mean(diag)) / n  # 스케일 파라미터 (최종 정규화 후 결과와 무관)
    w = 1.0 / np.sqrt(diag)
    for _ in range(max_sweeps):
        w_prev = w.copy()
        for i in range(n):
            b = cov[i] @ w - diag[i] * w[i]
            w[i] = (-b + np.sqrt(b * b + 4.0 * diag[i] * lam)) / (2.0 * diag[i])
        # 정규화는 수렴 후 1회 (반복 중 하면 고정점 조건 w_i(Σw)_i=λ 가 틀어짐)
        if np.abs(w - w_prev).max() < tol_w * max(1.0, np.abs(w).max()):
            break
    return w / w.sum()


def apply_ts_momentum_tilt(
    weights: dict[str, float],
    ret_df: pd.DataFrame,
    window: int | None,
    scale: float = 0.5,
) -> dict[str, float]:
    """팩터 TS 모멘텀 틸트 (2026-07-30 채택): trailing window 개월 자기 누적수익이
    음수인 팩터의 비중을 scale 배로 감쇠. window 미지정(None/0) = no-op.
    반환값은 비정규화 — 호출부의 합=1 정규화 단계가 이어받는다.
    mp / walk-forward 엔진 / 비용 실측 스크립트 3곳 공용 (동일 지점: 가중 산출 직후).
    """
    if not window or not weights:
        return weights
    trail = (1.0 + ret_df.iloc[1:].tail(int(window))).prod() - 1.0
    return {f: w * (scale if trail.get(f, 0.0) < 0 else 1.0) for f, w in weights.items()}


def blend_deploy_weights(
    target: dict[str, float],
    prev: dict[str, float] | None,
    step: float,
) -> dict[str, float]:
    """부분 조정 배포: prev 에서 target 으로 step 만큼만 이동 후 합 1 재정규화.

    step>=1 또는 prev 없음 -> target 그대로 (기존 동작 보존).
    월간 신호를 유지하면서 트레이드 크기를 줄여 실측 비용을 절감한다
    (2026-07-29 MP-level 실측: step 0.5 에서 net Sharpe 0.448->0.564).
    """
    if step >= 1.0 or not prev:
        return dict(target)
    keys = sorted(set(target) | set(prev))
    blended = {f: step * target.get(f, 0.0) + (1.0 - step) * prev.get(f, 0.0) for f in keys}
    total = sum(blended.values())
    return {f: w / total for f, w in blended.items() if w > 1e-10}


def selection_cost_bps(pp: dict) -> float:
    """선정·가중 입력(팩터 L/S 수익률 행렬)의 비용 = 종목비용 x backtest_cost_multiplier (단일 출처).

    2026-11-02: mp 는 종목비용 전액, 엔진은 0.6 배를 써서 같은 데이터로도 팩터 순위·공분산이 달랐다.
    실제 종목단 매매비용 계상(build_stock_series)은 이 값이 아니라 종목비용 전액을 쓴다.
    """
    return float(pp["transaction_cost_bps"]) * float(pp.get("backtest_cost_multiplier", 0.6))


def weight_kwargs_from(pp: dict) -> dict:
    """PIPELINE_PARAMS -> optimize_constrained_weights 키워드 인자 추출 (단일 출처).

    2026-08-25 도입: 운용 mp(model_portfolio)와 백테스트(walk_forward_engine)가
    이 추출을 각자 손으로 복제했었고, 실제로 구버전 mp 가 erc_* 를 누락해
    수축 0.5/0.7 불일치 사고가 났었다 (production parity 는 코드로 강제할 것).
    새 파라미터를 추가하면 이 함수 한 곳만 고치면 두 경로에 함께 반영된다.
    폴백 기본값은 함수 시그니처와 동일하게 유지 (config 미등재 키 전용).
    """
    return {
        "mode": pp["optimization_mode"],
        "style_cap": pp["style_cap"],
        "erc_shrinkage": float(pp.get("erc_shrinkage", 0.5)),
        "ts_mom_window": pp.get("ts_mom_window"),
        "ts_mom_scale": float(pp.get("ts_mom_scale", 0.5)),
    }


def optimize_constrained_weights(
    rtn_df: pd.DataFrame,
    style_list: list[str],
    mode: str = "equal_weight",
    style_cap: float = 0.25,
    tol: float = 1e-12,
    test_mode: bool = False,
    erc_shrinkage: float = 0.5,
    ts_mom_window: int | None = None,
    ts_mom_scale: float = 0.5,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """스타일 캡 하 포트폴리오 가중치를 결정한다 (학습되는 가중치 없음).

    모드 (config optimization_mode):
    - "erc": 상관 인지 Equal Risk Contribution (IS cov 대각 수축 erc_shrinkage,
      Spinu CCD) + 스타일 캡 재분배 — 프로덕션 기본 (MXWO 2026-07-29, MXCN1A 2026-08-05)
    - "equal_risk_weight": 팩터 IS 변동성 반비례(1/sigma) 가중 + 스타일 캡 재분배
    - "equal_weight": 1/N 동일가중 + 스타일 캡 재분배 (backtest 에서 "hardcoded" 는
      이 모드로 자동 변환)
    - "hardcoded": 프로덕션용 고정 가중치 CSV (data/hardcoded_weights.csv) 반환

    ts_mom_window 지정 시 캡 재분배 **이전**에 TS 모멘텀 틸트 적용 (apply_ts_momentum_tilt).

    Args:
        rtn_df: (날짜 x 팩터) 월간 수익률 행렬 (첫 행 = 기준점 0)
        style_list: 각 팩터의 스타일명 (rtn_df 컬럼 순서와 동일)
        style_cap: 스타일별 최대 비중 (기본 0.25 = 25%)
        tol: 제약 검사 허용 오차
        test_mode: True이면 캡 재분배 생략 (소량 데이터 검증용)

    Returns:
        (best_stats, weights_tbl) 튜플
        - best_stats: 1행 DataFrame (cagr, mdd, rank_cagr, rank_mdd, rank_total)
        - weights_tbl: 팩터별 가중치 (factor, raw_weight, styleName, fitted_weight)
    """
    if mode == "hardcoded":
        logger.info("Using hardcoded weights (production mode)")
        return _get_hardcoded_weights()

    if mode == "equal_weight":
        return _equal_weight_allocation(rtn_df, style_list, style_cap, tol, test_mode,
            ts_mom_window=ts_mom_window, ts_mom_scale=ts_mom_scale)

    if mode == "equal_risk_weight":
        # 팩터 IS 월간 수익률 변동성 반비례 가중. 첫 행(기준점 0) 제외는
        # compute_rank_score 의 monthly_rets = iloc[1:] 관례와 동일.
        vol = rtn_df.iloc[1:].std().to_numpy(dtype=np.float64)
        # ponytail: 무분산 팩터 폭주 방지 하한. zero-filter 가 상류에서 대부분 거르므로
        # 실전에서 밟힐 일은 드물다.
        vol = np.maximum(vol, 1e-6)
        return _equal_weight_allocation(
            rtn_df, style_list, style_cap, tol, test_mode, base_weights=1.0 / vol,
            ts_mom_window=ts_mom_window, ts_mom_scale=ts_mom_scale,
        )

    if mode == "erc":
        # Equal Risk Contribution: 팩터 간 상관을 반영해 리스크 기여를 균등화.
        # 1/sigma(ERW)의 상관 무시 한계 보완 — 총 비중 합 1 불변 (배수 아님).
        # cov 는 대각(무상관) 타깃으로 erc_shrinkage 만큼 수축해 추정 노이즈 완화
        # (0=표본 cov 그대로, 1=1/sigma 방향).
        cov = rtn_df.iloc[1:].cov().to_numpy(dtype=np.float64)
        cov = (1.0 - erc_shrinkage) * cov + erc_shrinkage * np.diag(np.diag(cov))
        w = _solve_erc_ccd(cov)
        return _equal_weight_allocation(
            rtn_df, style_list, style_cap, tol, test_mode, base_weights=w,
            ts_mom_window=ts_mom_window, ts_mom_scale=ts_mom_scale,
        )

    raise ValueError(
        f"Unknown optimization mode: {mode!r}. Use 'hardcoded', 'equal_weight', 'equal_risk_weight' or 'erc'."
    )
