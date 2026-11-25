# -*- coding: utf-8 -*-
"""MXWO 채택 파라미터 재검증 스윕 (2026-08-06, 앞뒤 3+케이스).

baseline = config 의 현행 채택 스택(BASE_ENGINE + PIPELINE_PARAMS 기본값).
각 케이스 = baseline 에서 파라미터 1개만 변경. factor-level walk-forward.
사용: python research/param_recheck_runner.py <worker_idx> <n_workers>
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.WARNING)

import numpy as np
import pandas as pd

from service.backtest.walk_forward_engine import WalkForwardEngine

from service.paths import OUTPUT_DIR
OUT = OUTPUT_DIR / "experiments" / "param_recheck"
OUT.mkdir(parents=True, exist_ok=True)

BASE_ENGINE = dict(min_is_months=36, factor_rebal_months=6, weight_rebal_months=1,
                   top_factors=50, selection_hysteresis=0.25, is_window_months=48)
# pp 는 config 기본(채택 스택)을 그대로 쓰고 delta 만 override

CASES = []
for w in (30, 36, 42, 54, 60, 66):
    CASES.append((f"is{w}", {}, {"is_window_months": w}))
for t in (1, 2, 3, 5, 6, 7):
    CASES.append((f"tsm{t}", {"ts_mom_window": t}, {}))
for n in (35, 40, 45, 55, 60, 65):
    CASES.append((f"top{n}", {}, {"top_factors": n}))
for s in (0.4, 0.5, 0.6, 0.8, 0.85, 0.9):
    CASES.append((f"shrink{s:g}", {"erc_shrinkage": s}, {}))
# 수축 전 구간 확장 (2026-08-07 사용자 요청: 0~1 전부)
for s in (0.0, 0.1, 0.2, 0.3, 0.95, 1.0):
    CASES.append((f"shrink{s:g}", {"erc_shrinkage": s}, {}))
for sp in (0.02, 0.03, 0.04, 0.065, 0.08, 0.10):
    CASES.append((f"spread{sp:g}", {"spread_threshold_pct": sp}, {}))
for h in (0.10, 0.15, 0.20, 0.35, 0.45, 0.60):
    CASES.append((f"hyst{h:g}", {}, {"selection_hysteresis": h}))
for sc in (0.2, 0.3, 0.4, 0.6, 0.7, 0.8):
    CASES.append((f"scale{sc:g}", {"ts_mom_scale": sc}, {}))
for name, pp_d, eng_d in [("wrebal2", {}, {"weight_rebal_months": 2}),
                          ("wrebal3", {}, {"weight_rebal_months": 3}),
                          ("wrebal6", {}, {"weight_rebal_months": 6}),
                          ("frebal3", {}, {"factor_rebal_months": 3}),
                          ("frebal9", {}, {"factor_rebal_months": 9}),
                          ("frebal12", {}, {"factor_rebal_months": 12})]:
    CASES.append((name, pp_d, eng_d))
# scale 전 구간 재검증 (2026-08-10 사용자 요청: 0~1, 최종 기준 TSM3+수축0.2 위에서.
# 동명 케이스는 이 결과가 구 TSM4 기준 결과를 대체. 1.0=틸트 무효, 0=음수모멘텀 제외)
for sc in (0.0, 0.1, 0.2, 0.3, 0.4, 0.6, 0.7, 0.8, 0.9, 1.0):
    CASES.append((f"scale{sc:g}", {"ts_mom_scale": sc}, {}))
# 전 축 최종 기준 재스윕 (2026-08-10 사용자 요청: TSM3+수축0.2 반영 후 전부 재측정.
# 수축/scale 축은 이미 최종 기준 측정치가 있어 제외. 인덱스 64~100)
for w in (24, 30, 36, 42, 54, 60, 66):
    CASES.append((f"is{w}", {}, {"is_window_months": w}))
for t in (1, 2, 4, 5, 6, 7):
    CASES.append((f"tsm{t}", {"ts_mom_window": t}, {}))
for n in (35, 40, 45, 55, 60, 65):
    CASES.append((f"top{n}", {}, {"top_factors": n}))
for sp in (0.02, 0.03, 0.04, 0.065, 0.08, 0.10):
    CASES.append((f"spread{sp:g}", {"spread_threshold_pct": sp}, {}))
for h in (0.10, 0.15, 0.20, 0.35, 0.45, 0.60):
    CASES.append((f"hyst{h:g}", {}, {"selection_hysteresis": h}))
for name, pp_d, eng_d in [("wrebal2", {}, {"weight_rebal_months": 2}),
                          ("wrebal3", {}, {"weight_rebal_months": 3}),
                          ("wrebal6", {}, {"weight_rebal_months": 6}),
                          ("frebal3", {}, {"factor_rebal_months": 3}),
                          ("frebal9", {}, {"factor_rebal_months": 9}),
                          ("frebal12", {}, {"factor_rebal_months": 12})]:
    CASES.append((name, pp_d, eng_d))
# 수축 축 최종 기준(scale0.2) 재스윕용: 0.7은 구 기준 baseline이라 케이스 부재 -> 추가 (인덱스 101)
CASES.append(("shrink0.7", {"erc_shrinkage": 0.7}, {}))
# 비용 민감도 (2026-08-11 사용자 요청): 20bp 기준 (인덱스 102)
CASES.append(("cost20", {"transaction_cost_bps": 20.0}, {}))


def metrics(r):
    r = r.dropna()
    cum = (1 + r).cumprod()
    yrs = len(r) / 12
    cagr = cum.iloc[-1] ** (1 / yrs) - 1
    mdd = (cum / cum.cummax() - 1).min()
    return {"sharpe": r.mean() / r.std() * np.sqrt(12), "cagr": cagr, "mdd": mdd,
            "calmar": cagr / abs(mdd) if mdd else np.nan}


def run_case(name, pp_delta, eng_delta):
    eng_kw = {**BASE_ENGINE, **eng_delta}
    is_window = eng_kw.pop("is_window_months")
    engine = WalkForwardEngine(pipeline_params_override=(pp_delta or None),
                               is_window_months=is_window, save_outputs=False, **eng_kw)
    result = engine.run("2015-06-30", "2026-06-30")
    result.to_csv(str(OUT / f"wf_{name}.csv"))
    df = pd.read_csv(OUT / f"wf_{name}.csv", parse_dates=["date"]).set_index("date")
    r = df["cew_return"]
    row = {"case": name}
    for label, rr in [("full", r), ("y2023", r[r.index >= "2023-01-01"]),
                      ("last24", r.iloc[-24:])]:
        for k, v in metrics(rr).items():
            row[f"{label}_{k}"] = round(float(v), 4)
    return row


def main():
    worker, n_workers = int(sys.argv[1]), int(sys.argv[2])
    my_cases = CASES[worker::n_workers]
    summary_path = OUT / f"summary_w{worker}.csv"
    rows = []
    for name, pp_d, eng_d in my_cases:
        print(f"START {name}", flush=True)
        try:
            row = run_case(name, pp_d, eng_d)
            rows.append(row)
            pd.DataFrame(rows).to_csv(summary_path, index=False)
            print(f"DONE {name} full={row['full_sharpe']} 2023={row['y2023_sharpe']}", flush=True)
        except Exception as e:
            print(f"FAIL {name}: {e}", flush=True)
    print("WORKER_COMPLETE", flush=True)


if __name__ == "__main__":
    main()
