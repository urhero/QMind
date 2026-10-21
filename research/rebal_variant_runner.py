# -*- coding: utf-8 -*-
"""MXWO 실제 운용 동등(규칙 매달 학습) 파라미터 재검증 러너 (2026-10-20).

배경: docs/experiments/factor_rebal_frequency_20261020.md — 공식 백테스트는 Tier 1 규칙을
6개월마다 학습하지만 production mp 는 매달 학습한다. 스택 파라미터는 전부 6개월 기준으로
튜닝됐으므로 production 동등 조건(규칙 매달 학습)에서 다시 확인한다.

구조: walk_forward_engine.run() 의 OOS 루프를 재현하되 Tier 1(규칙 학습 + 팩터 수익률
사전계산)을 한 번만 계산하고, 그 위에서 여러 Tier 2/3 변형(파라미터 조합)을 동시에 진행한다.
엔진 함수(_run_rule_learning, _apply_rules_and_aggregate, _rank_and_select,
_run_weight_optimization, _assemble_oos_record, stock_weights_at, build_stock_series)를
그대로 호출하므로 기준 변형은 엔진 산출물과 숫자가 같아야 한다 (validate 플랜으로 확인).

엔진에 없는 연구용 축:
  phase       : 규칙 학습 위상. (i - phase) % R == 0 인 달에 학습 (엔진은 phase=0 고정)
  rule_window : 규칙 학습 IS 창. 선정/가중 IS 창(sel_window)과 분리 (None 이면 sel_window)

표준 OUTPUT_DIR 에는 쓰지 않는다. 출력: output/{BM}/experiments/rebal1_recheck_20261020/
  sls_<t1>__<variant>.csv : 종목단 배포 시계열 (엔진 stock_level_series 와 같은 형식)
  wf_<t1>__<variant>.csv  : factor-level walk_forward_results 형식
  summary_<plan>.csv      : 지표 요약 (gross 32% 환산 순수익 기준)
사용: BENCHMARK=MXWO python research/rebal_variant_runner.py <plan>
"""
import logging
import pickle
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.WARNING)

import numpy as np
import pandas as pd

from config import PARAM, PIPELINE_PARAMS
from service.backtest import walk_forward_engine as wfe
from service.backtest.data_slicer import get_oos_dates
from service.backtest.result_stitcher import WalkForwardResult
from service.backtest.stock_level import build_stock_series, stock_weights_at
from service.paths import DATA_DIR, OUTPUT_DIR, UNIVERSE_DATA_DIR
from service.pipeline.factor_analysis import ANALYZE_COLS, calculate_factor_stats_batch
from service.pipeline.model_portfolio import ModelPortfolioPipeline
from service.pipeline.optimization import blend_deploy_weights
from service.pipeline.weight_construction import resolve_target_gross

OUT = OUTPUT_DIR / "experiments" / "rebal1_recheck_20261020"
REF = OUTPUT_DIR / "experiments" / "factor_rebal_20261019"   # 엔진(main.py backtest) 기준 산출물
MIN_IS = 36                                                    # main.py backtest CLI 기본
SEL_WINDOW = PIPELINE_PARAMS.get("is_window_months")          # 48
WREBAL = int(PIPELINE_PARAMS.get("weight_rebal_months", 3))   # 1
HYST = float(PIPELINE_PARAMS.get("selection_hysteresis", 0.0))  # 0.25
GROSS_NORM = 0.32                                              # 지표 환산 gross (현 배포)


def make_pp(override: dict | None = None) -> dict:
    """WalkForwardEngine.run() 과 같은 순서로 pipeline params 를 만든다."""
    pp = dict(PIPELINE_PARAMS)
    if override:
        pp.update(override)
    if pp["optimization_mode"] == "hardcoded":
        pp["optimization_mode"] = "equal_weight"
    pp["transaction_cost_bps"] = wfe._resolve_backtest_cost_bps(pp)
    return pp


class Variant:
    """Tier 2/3 변형 하나의 설정과 상태 (엔진 run() 의 cached_* 변수에 대응)."""

    def __init__(self, name: str, pp_over: dict | None = None, hyst: float | None = None,
                 wrebal: int | None = None):
        self.name = name
        self.pp = make_pp(pp_over)
        # _rank_and_select / _assemble_oos_record 만 쓴다 (순수 계산, self.selection_hysteresis 참조)
        self.engine = wfe.WalkForwardEngine(
            min_is_months=MIN_IS, weight_rebal_months=wrebal or WREBAL,
            selection_hysteresis=HYST if hyst is None else hyst)
        self.weights = self.meta = self.selected = self.top50 = None
        self.is_cagr = 0.0
        self.monthly: list = []
        self.results: list = []


def load_data():
    """엔진 run() 의 [1] 데이터 로딩 + 1-b 전체 5분위 통계 (Tier 1 불변값)."""
    pp0 = make_pp()
    pipeline = ModelPortfolioPipeline(config=PARAM, factor_info_path=DATA_DIR / "factor_info.csv",
                                      is_test=False, pipeline_params=pp0)
    raw, mret, _, _ = pipeline._load_data("2015-06-30", "2026-09-30", None)
    all_dates = sorted(raw["ddt"].unique())
    oos_dates = get_oos_dates(all_dates, MIN_IS)
    meta_full, merged_full, abbrs, orders = pipeline._prepare_metadata(raw, mret)
    del raw
    slim = merged_full[[c for c in ANALYZE_COLS if c in merged_full.columns]]
    fstats = calculate_factor_stats_batch(slim, abbrs, orders, test_mode=False,
                                          min_sector_stocks=pp0["min_sector_stocks"])
    return dict(pipeline=pipeline, merged=merged_full, meta=meta_full, abbrs=abbrs, orders=orders,
                fstats=fstats, all_dates=all_dates, oos_dates=oos_dates)


def step_variant(v: Variant, i, oos_date, is_end_date, sel_start, is_rule_rebal, pre, bundle, frames):
    """엔진 run() 의 Tier 2 + Tier 3 블록 (변수명만 다름, 연산 순서 동일)."""
    pp = v.pp
    is_weight_rebal = False
    if v.weights is None or is_rule_rebal or i % v.engine.weight_rebal_months == 0:
        is_weight_rebal = True
        ret_df_is = pre[pre.index <= is_end_date]
        if sel_start is not None:
            ret_df_is = ret_df_is[ret_df_is.index >= pd.Timestamp(sel_start)]
        ret_df_is = ret_df_is.copy()
        if len(ret_df_is) < 3:
            if v.weights is None:
                return
        else:
            ret_df_is.iloc[0] = 0.0
            valid = ret_df_is.columns[(ret_df_is.iloc[1:] == 0).sum() <= pp["max_zero_return_months"]]
            ret_df_is = ret_df_is[valid]
            if len(ret_df_is.columns) < wfe.MIN_REQUIRED_FACTORS:
                if v.weights is None:
                    return
            else:
                style_map: dict[str, str] = {}
                if bundle:
                    for abbr, style in zip(bundle.get("kept_abbrs", []) or [], bundle.get("kept_styles", []) or []):
                        style_map[abbr] = style
                selected, meta_top, rank_topn = v.engine._rank_and_select(ret_df_is, style_map, pp, v.selected)
                v.top50 = rank_topn
                ret_sel = ret_df_is[selected]
                try:
                    raw, v.meta = wfe._run_weight_optimization(ret_sel, meta_top, pp)
                except (ValueError, RuntimeError):
                    if v.weights is None:
                        return
                    raw = None
                if raw is not None:
                    is_months = len(ret_sel) - 1
                    if is_months > 0:
                        wret = sum(ret_sel[f] * raw.get(f, 0) for f in ret_sel.columns if f in raw)
                        v.is_cagr = (1 + wret).cumprod().iloc[-1] ** (12 / is_months) - 1
                    order = sorted(raw)
                    scale = 1.0 / sum(raw[f] for f in order)
                    target = {f: raw[f] * scale for f in order}
                    v.weights = blend_deploy_weights(target, v.weights, float(pp.get("deploy_step", 1.0)))
                    v.selected = list(v.weights.keys())
    if v.weights is None or v.selected is None:
        return
    if oos_date not in pre.index:
        return
    if frames:
        w_t, r_t = stock_weights_at(frames, v.weights, oos_date, pd.Timestamp(pp["backtest_start"]),
                                    sector_short_cap=pp.get("sector_short_cap"))
        if len(w_t):
            v.monthly.append((oos_date, w_t, r_t))
    rec = v.engine._assemble_oos_record(oos_date, pre, v.weights, v.meta, v.top50, v.is_cagr,
                                        is_rule_rebal, is_weight_rebal)
    if rec is not None:
        v.results.append(rec)


def run_t1(cfg: dict, data: dict, variants: list[Variant]):
    """Tier 1 설정 하나(규칙 주기/위상/창)로 OOS 루프를 돌며 모든 변형을 진행한다."""
    R, phase = cfg.get("R", 1), cfg.get("phase", 0)
    sel_window = cfg.get("sel_window", SEL_WINDOW)
    rule_window = cfg.get("rule_window") or sel_window
    pipeline, all_dates, oos_dates = data["pipeline"], data["all_dates"], data["oos_dates"]
    pipeline.pipeline_params = make_pp(cfg.get("pp_t1"))   # _run_rule_learning / aggregate 가 읽음
    n = min(len(oos_dates), cfg.get("max_months") or len(oos_dates))
    frames: dict = {}
    bundle = pre = None
    t_t1 = t_t2 = 0.0
    for i, oos_date in enumerate(oos_dates[:n]):
        is_end_idx = MIN_IS + i - 1
        is_end_date = all_dates[is_end_idx]
        sel_start = wfe.rolling_is_start(all_dates, is_end_idx, sel_window)
        rule_start = wfe.rolling_is_start(all_dates, is_end_idx, rule_window)
        is_rule_rebal = False
        if bundle is None or (i - phase) % R == 0:
            t0 = time.time()
            is_rule_rebal = True
            merged_is = data["merged"][data["merged"]["ddt"] <= pd.Timestamp(is_end_date)]
            if rule_start is not None:
                lag_start = pd.Timestamp(rule_start) - pd.DateOffset(months=1, days=5)
                merged_is = merged_is[merged_is["ddt"] >= lag_start]
            bundle = wfe._run_rule_learning(None, None, pipeline, None,
                                            prepared=(data["meta"], merged_is, data["abbrs"], data["orders"]))
            next_i = i + R - ((i - phase) % R)   # 다음 Tier 1 의 i (phase=0 이면 엔진의 i+R)
            horizon_idx = min(len(all_dates) - 1, MIN_IS + next_i)
            pre = wfe._apply_rules_and_aggregate(data["fstats"], data["abbrs"], bundle, pipeline,
                                                 out_frames=frames, horizon=pd.Timestamp(all_dates[horizon_idx]))
            t_t1 += time.time() - t0
            if pre.empty:
                continue
            pre.loc[pre.index[0]] = 0.0
            pre = pre.sort_index()
            for v in variants:
                v.meta = None
                v.top50 = None
        if pre is None or pre.empty:
            continue
        t0 = time.time()
        for v in variants:
            step_variant(v, i, oos_date, is_end_date, sel_start, is_rule_rebal, pre, bundle, frames)
        t_t2 += time.time() - t0
        if i % 10 == 0:
            print(f"  [{cfg['name']}] i={i} {pd.Timestamp(oos_date).date()} tier1 {t_t1/60:.1f}m "
                  f"tier2/3 {t_t2/60:.1f}m ({len(variants)} variants)", flush=True)
    return t_t1, t_t2


def finish(cfg: dict, v: Variant) -> dict:
    stem = f"{cfg['name']}__{v.name}"
    res = WalkForwardResult(v.results)
    res.to_csv(str(OUT / f"wf_{stem}.csv"))
    if not res.weight_history.empty:   # 팩터 비중 이력 (집중도/RC 점검용, 2026-10-21 추가)
        res.weight_history.to_csv(OUT / f"wh_{stem}.csv")
    tg_path, tg_def = UNIVERSE_DATA_DIR / "mp_target_gross.csv", v.pp.get("mp_target_gross")
    s = build_stock_series(v.monthly, float(PIPELINE_PARAMS["transaction_cost_bps"]),
                           lambda d: resolve_target_gross(d, tg_path, tg_def),
                           benchmark=(PARAM["benchmark"] if v.pp.get("bm_short_cap") else None))
    s.to_csv(OUT / f"sls_{stem}.csv")
    if cfg.get("save_weights"):
        with open(OUT / f"w_{stem}.pkl", "wb") as fh:
            pickle.dump(v.monthly, fh)
    wf = pd.read_csv(OUT / f"wf_{stem}.csv", parse_dates=["date"]).set_index("date")
    return summarize(stem, s, wf)


def summarize(stem: str, s: pd.DataFrame, wf: pd.DataFrame | None = None) -> dict:
    s = s.copy()
    s.index = pd.to_datetime(s.index)
    r = s["net_return"] / s["target_gross"] * GROSS_NORM
    g = s["gross_return"] / s["target_gross"] * GROSS_NORM

    def sr(x):
        return float(x.mean() / x.std() * np.sqrt(12)) if len(x) > 2 and x.std() > 0 else np.nan
    h = len(r) // 2
    cum = (1 + r).cumprod()
    mdd = float((cum / cum.cummax() - 1).min())
    row = {"case": stem, "months": len(r), "net_bp": r.mean() * 1e4, "gross_bp": g.mean() * 1e4,
           "std_bp": r.std() * 1e4, "sharpe": sr(r), "sr_1st": sr(r.iloc[:h]), "sr_2nd": sr(r.iloc[h:]),
           "sr_2023": sr(r[r.index >= "2023-01-01"]), "sr_24m": sr(r.iloc[-24:]), "mdd_bp": mdd * 1e4,
           "calmar": r.mean() * 12 / abs(mdd) if mdd else np.nan,
           "turnover_yr": s["turnover_oneway"].mean() * 12}
    if wf is not None and "cew_return" in wf:
        row["cew_sharpe"] = sr(wf["cew_return"])
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in row.items()}


def compare_ref(stem: str, ref_dir: str, n: int):
    """엔진 산출물(REF) 앞 n 행과 수치 비교 — 러너 재현성 검증."""
    out = []
    for kind, ref_name in (("sls", "stock_level_series"), ("wf", "walk_forward_results")):
        mine = pd.read_csv(OUT / f"{kind}_{stem}.csv").head(n)
        ref = pd.read_csv(REF / ref_dir / f"{ref_name}_2026-09-30.csv").head(n)
        cols = [c for c in ref.columns if c in mine.columns and pd.api.types.is_numeric_dtype(ref[c])]
        diff = max(float((mine[c] - ref[c]).abs().max()) for c in cols)
        same_dates = (mine.iloc[:, 0].astype(str) == ref.iloc[:, 0].astype(str)).all()
        out.append(f"{kind}: rows {len(mine)} dates_equal {same_dates} max|diff| {diff:.3e}")
    return out


# ── 플랜 ────────────────────────────────────────────────────────────────────
def t2_variants() -> list[Variant]:
    """현 스택에서 1개씩만 바꾼 Tier 2/3 변형 (규칙 학습 결과를 공유)."""
    v = [Variant("base")]
    v += [Variant(f"shrink{s:g}", {"erc_shrinkage": s}) for s in (0.0, 0.1, 0.3, 0.5, 0.7)]
    v += [Variant(f"scale{s:g}", {"ts_mom_scale": s}) for s in (0.0, 0.1, 0.3, 0.5, 1.0)]
    v += [Variant(f"tsm{w}", {"ts_mom_window": w}) for w in (2, 4, 6)]
    v += [Variant(f"hyst{h:g}", hyst=h) for h in (0.0, 0.1, 0.4, 0.6)]
    v += [Variant(f"top{n}", {"top_factor_count": n}) for n in (35, 40, 45, 55, 60)]
    v += [Variant(f"stylecap{c:g}", {"style_cap": c}) for c in (0.20, 0.30)]
    v += [Variant(f"seccap{c}", {"sector_short_cap": c}) for c in (None, 0.10, 0.20)]
    v += [Variant("step0.5", {"deploy_step": 0.5})]
    v += [Variant("dedup_on", {"use_cluster_dedup": True})]
    return v


def grid_variants() -> list[Variant]:
    """t2sweep 개선 후보 6축의 완전 조합 (5x3x2x2x2x2 = 240). 이름 = g_s{수축}_h{히스}_n{TopN}_c{스타일캡}_k{섹터숏캡}_st{step}.
    g_s0.2_h0.25_n50_c0.25_k0.15_st1 = 현행 스택 (base 와 수치 동일해야 함)."""
    out = []
    for s in (0.2, 0.5, 0.7, 0.85, 1.0):
        for h in (0.0, 0.1, 0.25):
            for n in (50, 60):
                for c in (0.20, 0.25):
                    for k in (0.10, 0.15):
                        for st in (1.0, 0.5):
                            out.append(Variant(f"g_s{s:g}_h{h:g}_n{n}_c{c:g}_k{k:g}_st{st:g}",
                                               {"erc_shrinkage": s, "top_factor_count": n, "style_cap": c,
                                                "sector_short_cap": k, "deploy_step": st}, hyst=h))
    return out


def key_variants() -> list[Variant]:
    """구조 실험(규칙 창 등)을 현행/주요 개선 후보 조합 위에서 함께 비교."""
    a5 = {"erc_shrinkage": 0.7, "top_factor_count": 60, "style_cap": 0.20, "sector_short_cap": 0.10}
    return [Variant("base"),
            Variant("shrink0.7", {"erc_shrinkage": 0.7}),
            Variant("hyst0", hyst=0.0),
            Variant("shrink0.7_hyst0", {"erc_shrinkage": 0.7}, hyst=0.0),
            Variant("all5", a5, hyst=0.0),
            Variant("all6", {**a5, "deploy_step": 0.5}, hyst=0.0)]


PLANS = {
    # 러너 재현성: 엔진 산출물(규칙 매달/6개월) 앞부분과 수치 일치 확인
    "validate": [dict(name="r1_w48", R=1, max_months=8, ref="rebal1", variants=lambda: [Variant("base")]),
                 dict(name="r6_w48", R=6, max_months=14, ref="rebal6", variants=lambda: [Variant("base")])],
    # 규칙 매달 학습(production 동등) 위에서 Tier 2/3 파라미터 1개씩 변경
    "t2sweep": [dict(name="r1_w48", R=1, variants=t2_variants)],
    # 6개월 규칙 주기의 위상 의존성 (+ 위상 앙상블용 비중 저장)
    "phase6": [dict(name=f"r6p{p}", R=6, phase=p, save_weights=True, variants=lambda: [Variant("base")])
               for p in range(6)],
    # t2sweep 개선 후보의 조합 그리드 (규칙 매달 학습, 창 48)
    "t2grid": [dict(name="r1_w48", R=1, variants=grid_variants)],
    # 규칙 학습 창만 늘림 (선정/가중은 48 유지) — 규칙 노이즈 완화 후보
    "rulewin_only": [dict(name=f"r1_rw{w}", R=1, rule_window=w, variants=key_variants) for w in (72, 96)],
    # 롤링 IS 창 (규칙 + 선정/가중 공통) — 2026-10-20 보류 (6개월 기준에서 w48 우위, 시간 3h+)
    "window": [],
}


def tranche_variants() -> list[Variant]:
    """그리드에서 양 반쪽 방향이 일치한 3축(히스 0, Top60, 스타일캡 0.20) 기반 + 수축/섹터캡 변형."""
    r3 = {"top_factor_count": 60, "style_cap": 0.20}
    return [Variant("base"),
            Variant("robust3", r3, hyst=0.0),
            Variant("robust3_s0.5", {**r3, "erc_shrinkage": 0.5}, hyst=0.0),
            Variant("robust3_s0.5_k0.1", {**r3, "erc_shrinkage": 0.5, "sector_short_cap": 0.10}, hyst=0.0)]


def robust_grid() -> list[Variant]:
    """robust3 주변 고원 확인: TopN 55~70 x 스타일캡 0.15/0.20 x 히스 0~0.1 x 수축 0.2/0.5/0.7 (72)."""
    out = []
    for n in (55, 60, 65, 70):
        for c in (0.15, 0.20):
            for h in (0.0, 0.05, 0.1):
                for s in (0.2, 0.5, 0.7):
                    out.append(Variant(f"rg_n{n}_c{c:g}_h{h:g}_s{s:g}",
                                       {"top_factor_count": n, "style_cap": c, "erc_shrinkage": s}, hyst=h))
    return out


# 트랜칭(6개 위상 슬리브 평균) x 개선 후보 — 후처리는 rebal_phase_ensemble.py <variant...>
PLANS["phase6_key"] = [dict(name=f"r6p{p}", R=6, phase=p, save_weights=True, variants=tranche_variants)
                       for p in range(6)]
# robust3 주변 고원 확인 (규칙 매달 학습, 창 48)
PLANS["t2robust"] = [dict(name="r1_w48", R=1, variants=robust_grid)]
# 2026-10-20: 이미 실행 중인 순차 드라이버가 'rulewin' 이름으로 호출하므로 그리드를 먼저 돌리도록 묶음
PLANS["rulewin"] = PLANS["t2grid"] + PLANS["rulewin_only"]


def main():
    plan = sys.argv[1]
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    data = load_data()
    print(f"[{plan}] data loaded in {(time.time() - t0)/60:.1f}m, OOS {len(data['oos_dates'])} months", flush=True)
    rows = []
    for cfg in PLANS[plan]:
        variants = cfg["variants"]()
        t1, t2 = run_t1(cfg, data, variants)
        print(f"[{cfg['name']}] done: tier1 {t1/60:.1f}m tier2/3 {t2/60:.1f}m", flush=True)
        for v in variants:
            row = finish(cfg, v)
            rows.append(row)
            print(f"  {row['case']}: SR {row['sharpe']} (1st {row['sr_1st']} 2nd {row['sr_2nd']}) "
                  f"net {row['net_bp']}bp MDD {row['mdd_bp']}bp", flush=True)
            if cfg.get("ref"):
                for line in compare_ref(row["case"], cfg["ref"], cfg["max_months"]):
                    print(f"    vs engine {cfg['ref']}: {line}", flush=True)
        pd.DataFrame(rows).to_csv(OUT / f"summary_{plan}.csv", index=False)
    print(f"[{plan}] PLAN_COMPLETE {(time.time() - t0)/60:.1f}m", flush=True)


if __name__ == "__main__":
    main()
