# -*- coding: utf-8 -*-
"""MP-level(종목단) 비용 백테스트 — factor-level 비용 근사의 결정판 검증 (2026-07).

walk_forward_engine.run() 의 Tier 1/2 결정(규칙 학습/선정/가중치)을 그대로 재현하되,
Tier 3 에서 종목 수준 MP 비중을 팩터 합산(교차 팩터 netting 반영)하고
실제 MP 매매 턴오버에 종목비용(config transaction_cost_bps)을 물린 순수익률을 산출한다.

factor-level 백테스트와의 차이:
  - factor-level: 각 팩터가 자기 편입/편출 매매를 전액 부담 (netting 무시 -> 비용 과대)
  - MP-level:     같은 종목의 롱/숏이 팩터 간 상쇄된 뒤의 '실제 트레이드'에만 비용

산출 (scratchpad 또는 --out 경로):
  - mp_level_cost_backtest.csv: 월별 cew(factor-level) / gross / cost_stock /
    net_stock / cost_factor_level / turnover_oneway
  - 콘솔 요약: 성과 비교 + netting ratio (cost_stock / cost_factor_level)

parity: cew_return 은 canonical output/walk_forward_results.csv 와 일치해야 한다
(동일 결정 재현 검증). --test 는 tests/fixtures/test_data.csv 로 엔진과 in-process 비교.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from joblib import Parallel, delayed

from config import PARAM, PIPELINE_PARAMS
from service.backtest.data_slicer import get_oos_dates
from service.backtest.walk_forward_engine import (
    MIN_REQUIRED_FACTORS,
    WalkForwardEngine,
    _resolve_backtest_cost_bps,
    _run_rule_learning,
    _run_weight_optimization,
    deploy_weights,
)
from service.paths import DATA_DIR, OUTPUT_DIR, latest
from service.backtest.stock_level import stock_weights_at
from service.pipeline.transaction_tax import tax_cost
from service.pipeline.factor_analysis import ANALYZE_COLS, calculate_factor_stats_batch
from service.pipeline.model_portfolio import ModelPortfolioPipeline
from service.pipeline.weight_construction import (
    calculate_vectorized_return,
    construct_long_short_df,
)


def _factor_series(data, abbr, backtest_start, cost_bps):
    """단일 팩터 (net, cost) 시리즈 — aggregate_factor_returns 와 동일 절차 + cost 보존."""
    long_df, short_df = construct_long_short_df(data, backtest_start=backtest_start)
    _, net_l, cost_l = calculate_vectorized_return(long_df, abbr, cost_bps=cost_bps)
    _, net_s, cost_s = calculate_vectorized_return(short_df, abbr, cost_bps=cost_bps)
    # 한쪽 비면(롱-only/숏-only) 있는 쪽만 (weight_construction 가드와 동일 처리)
    nets = [s for s in (net_l, net_s) if not s.empty]
    costs = [s for s in (cost_l, cost_s) if not s.empty]
    net = (net_l + net_s) if len(nets) == 2 else (nets[0] if nets else pd.DataFrame(columns=[abbr]))
    cost = (cost_l + cost_s) if len(costs) == 2 else (costs[0] if costs else pd.DataFrame(columns=[abbr]))
    return net[abbr], cost[abbr]


def apply_rules_keep_frames(factor_stats_full, factor_abbr_list, rule_bundle, pp):
    """_apply_rules_and_aggregate 재현 + 종목 프레임과 팩터별 비용 시리즈 보존.

    Returns:
        (net_df, cost_df, frames): net_df 는 엔진의 precomputed_ret_df 와 동일해야 함.
    """
    abbr_to_idx = {a: i for i, a in enumerate(factor_abbr_list)}
    valid_abbrs, valid_frames = [], []
    for abbr in rule_bundle["kept_abbrs"]:
        i = abbr_to_idx.get(abbr)
        if i is None:
            continue
        stats = factor_stats_full[i]
        if stats[0] is None:
            continue
        raw_df = stats[3]
        secs = rule_bundle["dropped_sectors"].get(abbr, [])
        raw_clean = raw_df[~raw_df["sec"].isin(secs)].copy() if secs else raw_df.copy()
        if raw_clean.empty:
            continue
        labels = rule_bundle["label_rules"].get(abbr, {})
        if not labels:
            continue
        raw_clean["label"] = raw_clean["quantile"].map(labels)
        merged = raw_clean.dropna(subset=["label"])
        if merged.empty:
            continue
        if not ((merged["label"] == 1).any() and (merged["label"] == -1).any()):
            continue
        valid_abbrs.append(abbr)
        valid_frames.append(merged)

    if not valid_abbrs:
        return pd.DataFrame(), pd.DataFrame(), {}

    results = Parallel(n_jobs=-1)(
        delayed(_factor_series)(fr, ab, pp["backtest_start"], pp["transaction_cost_bps"])
        for fr, ab in zip(valid_frames, valid_abbrs)
    ) if len(valid_abbrs) > 8 else [
        _factor_series(fr, ab, pp["backtest_start"], pp["transaction_cost_bps"])
        for fr, ab in zip(valid_frames, valid_abbrs)
    ]

    net_df = pd.concat([r[0] for r in results], axis=1)
    cost_df = pd.concat([r[1] for r in results], axis=1)
    keep = net_df.columns[~net_df.isna().any()]
    net_df, cost_df = net_df[keep], cost_df[keep]
    frames = {ab: fr for ab, fr in zip(valid_abbrs, valid_frames) if ab in set(keep)}
    return net_df, cost_df, frames




def run(test_file: str | None, out_dir: Path, selection_cost_bps: float | None = None,
        out_suffix: str = "", optimization_mode: str | None = None,
        weight_rebal_override: int | None = None, hysteresis_override: float | None = None,
        is_window_months: int | None = None, pp_override: dict | None = None) -> pd.DataFrame:
    """selection_cost_bps: factor-level(선정 입력) 비용 오버라이드.
    None 이면 엔진과 동일 (transaction_cost_bps x multiplier).
    MP-level 실비용(cost_stock)은 항상 base transaction_cost_bps 를 쓴다 —
    netted 실거래에는 multiplier(netting 근사)를 다시 곱하면 이중 할인.
    optimization_mode: pp["optimization_mode"] 오버라이드 (예: "equal_risk_weight").
    """
    t0 = time.time()
    min_is = 4 if test_file else 36
    factor_rebal, weight_rebal = 6, 3
    if weight_rebal_override:
        weight_rebal = int(weight_rebal_override)
    hyst = float(PIPELINE_PARAMS.get("selection_hysteresis", 0.0))
    if hysteresis_override is not None:
        hyst = float(hysteresis_override)

    pp = dict(PIPELINE_PARAMS)
    pp["top_factor_count"] = 50
    if pp_override:
        pp.update(pp_override)
    if pp["optimization_mode"] == "hardcoded":
        pp["optimization_mode"] = "equal_weight"
    if optimization_mode:
        pp["optimization_mode"] = optimization_mode
    mp_cost_bps = float(pp["transaction_cost_bps"])  # 실비용: base (multiplier 미적용)
    pp["transaction_cost_bps"] = (
        _resolve_backtest_cost_bps(pp) if selection_cost_bps is None else float(selection_cost_bps)
    )

    engine = WalkForwardEngine(
        min_is_months=min_is, factor_rebal_months=factor_rebal,
        weight_rebal_months=weight_rebal, top_factors=50, selection_hysteresis=hyst,
    )

    pipeline = ModelPortfolioPipeline(
        config=PARAM, factor_info_path=DATA_DIR / "factor_info.csv",
        is_test=bool(test_file), pipeline_params=pp,
    )
    raw, mret, _, _ = pipeline._load_data(None, None, test_file)
    all_dates = sorted(raw["ddt"].unique())
    oos_dates = get_oos_dates(all_dates, min_is)

    meta_full, merged_full, abbrs_full, orders_full = pipeline._prepare_metadata(raw, mret)
    slim = merged_full[[c for c in ANALYZE_COLS if c in merged_full.columns]]
    stats_full = calculate_factor_stats_batch(
        slim, abbrs_full, orders_full, test_mode=bool(test_file),
        min_sector_stocks=pp["min_sector_stocks"],
    )

    backtest_start_ts = pd.Timestamp(pp["backtest_start"])

    cached_rule_bundle = None
    net_full = cost_full = None
    frames: dict = {}
    cached_weights = None
    cached_selected = None
    prev_w = prev_r = None
    records = []

    for i, t in enumerate(oos_dates):
        is_end = all_dates[min_is + i - 1]
        is_rule_rebal = False

        # -- Tier 1 (엔진과 동일) --
        if cached_rule_bundle is None or i % factor_rebal == 0:
            is_rule_rebal = True
            merged_is = merged_full[merged_full["ddt"] <= pd.Timestamp(is_end)]
            if is_window_months:
                # 엔진 rolling IS 와 동일: 시작 1개월 이전부터 (lag 기저)
                from service.backtest.walk_forward_engine import rolling_is_start
                is_start = rolling_is_start(all_dates, min_is + i - 1, is_window_months)
                if is_start is not None:
                    lag_start = pd.Timestamp(is_start) - pd.DateOffset(months=1, days=5)
                    merged_is = merged_is[merged_is["ddt"] >= lag_start]
            prepared = (meta_full, merged_is, abbrs_full, orders_full)
            cached_rule_bundle = _run_rule_learning(None, None, pipeline, test_file, prepared=prepared)
            net_full, cost_full, frames = apply_rules_keep_frames(
                stats_full, abbrs_full, cached_rule_bundle, pp)
            if net_full.empty:
                continue
            net_full.loc[net_full.index[0]] = 0.0
            net_full = net_full.sort_index()
            cost_full = cost_full.sort_index()

        if net_full is None or net_full.empty:
            continue

        # -- Tier 2 (엔진과 동일) --
        if cached_weights is None or is_rule_rebal or i % weight_rebal == 0:
            ret_is = net_full[net_full.index <= is_end]
            if is_window_months:
                from service.backtest.walk_forward_engine import rolling_is_start
                is_start = rolling_is_start(all_dates, min_is + i - 1, is_window_months)
                if is_start is not None:
                    ret_is = ret_is[ret_is.index >= pd.Timestamp(is_start)]
            ret_is = ret_is.copy()
            if len(ret_is) >= 3:
                ret_is.iloc[0] = 0.0
                valid = ret_is.columns[(ret_is == 0).sum() <= pp["max_zero_return_months"]]
                ret_is = ret_is[valid]
                if len(ret_is.columns) >= MIN_REQUIRED_FACTORS:
                    style_map = dict(zip(cached_rule_bundle.get("kept_abbrs", []),
                                         cached_rule_bundle.get("kept_styles", [])))
                    selected, meta_top, _rank_topn = engine._rank_and_select(ret_is, style_map, pp, cached_selected)
                    try:
                        # TS 틸트는 _run_weight_optimization 내부(캡 이전)에서 적용
                        # (2026-08-06 순서 교정 — 엔진과 동일 경로 공유)
                        raw_w, _ = _run_weight_optimization(ret_is[selected], meta_top, pp)
                    except (ValueError, RuntimeError):
                        raw_w = None
                    if raw_w is not None:
                        from service.pipeline.optimization import blend_deploy_weights
                        order = sorted(raw_w)
                        scale = 1.0 / sum(raw_w[f] for f in order)
                        target = {f: raw_w[f] * scale for f in order}
                        # deploy_step: 엔진과 동일한 부분 조정 배포 (2026-07-29)
                        cached_weights = blend_deploy_weights(
                            target, cached_weights, float(pp.get("deploy_step", 1.0)))
                        cached_selected = list(cached_weights.keys())

        if cached_weights is None or t not in net_full.index:
            continue

        # -- Tier 3: factor-level cew (parity) + stock-level --
        avail = sorted(f for f in cached_weights if f in net_full.columns)
        if not avail:
            continue
        w_dep = deploy_weights(cached_weights, avail)
        cew = sum(net_full.loc[t, f] * w_dep.get(f, 0) for f in avail)
        cost_fl = sum(cost_full.loc[t, f] * w_dep.get(f, 0) for f in avail)

        w_t, r_t = stock_weights_at(frames, w_dep, t, backtest_start_ts,
                                    sector_short_cap=pp.get("sector_short_cap"))
        gross = float((w_t * r_t).sum()) if len(w_t) else 0.0

        if prev_w is not None and len(prev_w) and len(w_t):
            pr = prev_r.reindex(prev_w.index).fillna(0.0)
            nav = 1.0 + float((prev_w * pr).sum())
            drift = prev_w * (1.0 + pr) / (nav if nav > 0 else 1.0)
            union = w_t.index.union(drift.index)
            # 부호 있는 델타 보존: 매수(+)/매도(-) 방향별 거래세 부과에 필요
            delta = w_t.reindex(union).fillna(0.0) - drift.reindex(union).fillna(0.0)
            turno = float(delta.abs().sum())
            tax_stock = tax_cost(delta)
        else:
            turno = 0.0
            tax_stock = 0.0
        cost_stock = mp_cost_bps / 1e4 * turno

        prev_w, prev_r = w_t, r_t
        sel_bps = pp["transaction_cost_bps"]
        records.append({
            "date": t, "cew_return": cew,
            "gross_stock": gross, "cost_stock": cost_stock,
            "tax_stock": tax_stock,
            "net_stock": gross - cost_stock - tax_stock,
            "cost_factor_level": cost_fl,
            # netting ratio 용: factor-level 비용을 base bps 스케일로 환산
            # (trading_cost 는 bps 에 선형이므로 단순 비례 환산 가능)
            "cost_factor_at_base": cost_fl * (mp_cost_bps / sel_bps) if sel_bps > 0 else np.nan,
            "turnover_oneway": turno / 2.0,
        })

    df = pd.DataFrame(records).set_index("date")
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = "mp_level_cost_backtest_test" if test_file else "mp_level_cost_backtest"
    out_path = out_dir / f"{stem}{out_suffix}.csv"
    df.to_csv(out_path)
    print(f"saved {out_path} ({len(df)} months, {time.time() - t0:.0f}s)")
    return df


def perf(rets: pd.Series) -> dict:
    cum = (1 + rets).cumprod()
    n = len(rets)
    final = cum.iloc[-1]
    cagr = -1.0 if final <= 0 else final ** (12 / n) - 1
    mdd = ((cum - cum.cummax()) / cum.cummax()).min()
    sharpe = rets.mean() / rets.std() * np.sqrt(12) if rets.std() > 0 else 0.0
    return {"CAGR": cagr, "MDD": mdd, "Sharpe": sharpe,
            "Calmar": cagr / abs(mdd) if mdd != 0 else 0.0}


def summarize(df: pd.DataFrame, test_file: str | None, parity_csv: str | None = None):
    print("\n=== 성과 (OOS %d개월) ===" % len(df))
    for label, col in [("factor-level net (cew parity)", "cew_return"),
                       ("stock-level gross", "gross_stock"),
                       ("stock-level net (MP 실비용)", "net_stock")]:
        p = perf(df[col])
        print(f"{label:32s} CAGR {p['CAGR']:+.4f}  MDD {p['MDD']:+.4f}  "
              f"Sharpe {p['Sharpe']:+.3f}  Calmar {p['Calmar']:+.3f}")

    to = df["turnover_oneway"].iloc[1:]  # 첫 달 0 제외
    print(f"\nMP one-way 턴오버: 평균 {to.mean():.3f}/월 (연 {to.mean()*12:.1f}x)")
    cs = df["cost_stock"].iloc[1:]
    cf = df.get("cost_factor_at_base", df["cost_factor_level"]).iloc[1:]
    print(f"월평균 비용 (동일 bps 기준): stock-level {cs.mean()*1e4:.1f}bp vs factor-level {cf.mean()*1e4:.1f}bp")
    if cf.mean() > 0:
        print(f"netting ratio (실비용/팩터별 전액계상) = {cs.mean()/cf.mean():.3f}")

    # parity: canonical(또는 지정) 결과 CSV 와 cew 비교
    if not test_file:
        # 유니버스별 OUTPUT_DIR + 기준일 최신본 (구: 루트 output 고정 -> MXWO 에서 오표기)
        canon_path = (Path(parity_csv) if parity_csv
                      else latest(OUTPUT_DIR / "walk_forward_results.csv"))
        if canon_path.exists():
            canon = pd.read_csv(canon_path, parse_dates=["date"]).set_index("date")
            joined = df[["cew_return"]].join(canon["cew_return"], rsuffix="_canon").dropna()
            md = (joined["cew_return"] - joined["cew_return_canon"]).abs().max()
            print(f"\nparity: |cew - canonical| max = {md:.2e} ({len(joined)}개월)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="tests/fixtures/test_data.csv 로 빠른 검증")
    ap.add_argument("--out", default=None, help="출력 디렉토리 (기본: output/experiments)")
    ap.add_argument("--selection-cost-bps", type=float, default=None,
                    help="선정(factor-level) 비용 오버라이드. 0 = gross 선정. "
                         "기본: 엔진과 동일 (cost x multiplier)")
    ap.add_argument("--optimization-mode", default=None,
                    help="가중 모드 오버라이드 (예: equal_risk_weight). 기본: config")
    ap.add_argument("--parity-csv", default=None,
                    help="parity 비교 대상 CSV (기본: output/walk_forward_results.csv)")
    ap.add_argument("--weight-rebal-months", type=int, default=None, help="Tier 2 주기 오버라이드")
    ap.add_argument("--hysteresis", type=float, default=None, help="선정 히스테리시스 오버라이드")
    ap.add_argument("--is-window-months", type=int, default=None, help="롤링 IS 윈도우 (엔진과 동일 의미)")
    ap.add_argument("--pp-json", default=None,
                    help='PIPELINE_PARAMS 오버라이드 JSON (예: {"spread_threshold_pct":0.05})')
    args = ap.parse_args()
    test_file = "tests/fixtures/test_data.csv" if args.test else None
    out_dir = Path(args.out) if args.out else OUTPUT_DIR / "experiments"  # 유니버스별 (루트 output/ 금지)
    suffix = "" if args.selection_cost_bps is None else f"_sel{args.selection_cost_bps:g}bp"
    if args.optimization_mode:
        suffix += f"_{args.optimization_mode}"
    pp_override = None
    if args.pp_json:
        import json
        pp_override = json.loads(args.pp_json)
        suffix += "_ppov"
    df = run(test_file, out_dir, selection_cost_bps=args.selection_cost_bps, out_suffix=suffix,
             optimization_mode=args.optimization_mode,
             weight_rebal_override=args.weight_rebal_months,
             hysteresis_override=args.hysteresis,
             is_window_months=args.is_window_months,
             pp_override=pp_override)
    # parity 는 선정 비용이 엔진 기본과 같을 때만 의미 있음.
    # 모드 오버라이드 시엔 --parity-csv 로 해당 모드의 결과를 지정해야 의미 있음.
    skip = args.selection_cost_bps is not None or (args.optimization_mode and not args.parity_csv)
    summarize(df, test_file if not skip else "skip-parity", parity_csv=args.parity_csv)


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING)
    main()
