# -*- coding: utf-8 -*-
"""6개월 규칙 주기 위상 앙상블 (2026-10-20, rebal_variant_runner.py phase6 플랜의 후처리).

위상 0~5 (규칙 학습 달이 서로 다른 6개 포트)의 월별 종목 비중을 평균한 '엇갈린(staggered)'
포트를 만든다. 매달 학습한 규칙이 6개월 동안 1/6 씩 겹쳐 쓰이는 구조 — 규칙을 매달 갱신하되
한 번의 학습 잡음이 전체를 흔들지 않게 하는 후보. 비용·세금·배수는 엔진과 같은
build_stock_series 로 평균 비중의 실제 회전에 대해 계상한다.

사용: BENCHMARK=MXWO python research/rebal_phase_ensemble.py
"""
import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

from config import PARAM, PIPELINE_PARAMS
from rebal_variant_runner import OUT, summarize
from service.backtest.stock_level import build_stock_series
from service.paths import UNIVERSE_DATA_DIR
from service.pipeline.weight_construction import resolve_target_gross


def load(p: int, variant: str = "base") -> dict:
    with open(OUT / f"w_r6p{p}__{variant}.pkl", "rb") as fh:
        return {pd.Timestamp(d): (w, r) for d, w, r in pickle.load(fh)}


def main(variant: str = "base", phases=range(6)):
    books = [load(p, variant) for p in phases]
    dates = sorted(set.intersection(*[set(b) for b in books]))
    monthly = []
    for d in dates:
        ws = [b[d][0] for b in books]
        rs = [b[d][1] for b in books]
        w = pd.concat(ws, axis=1).fillna(0.0).mean(axis=1)      # 위상별 동일가중 평균 비중
        r = rs[0]
        for x in rs[1:]:
            r = r.combine_first(x)                                # 같은 종목 같은 달 수익률 (위상 무관)
        w = w[w != 0].sort_index()
        monthly.append((d, w, r.reindex(w.index)))
    tg_path, tg_def = UNIVERSE_DATA_DIR / "mp_target_gross.csv", PIPELINE_PARAMS.get("mp_target_gross")
    s = build_stock_series(monthly, float(PIPELINE_PARAMS["transaction_cost_bps"]),
                           lambda d: resolve_target_gross(d, tg_path, tg_def),
                           benchmark=(PARAM["benchmark"] if PIPELINE_PARAMS.get("bm_short_cap") else None))
    tag = "r6ens" + "".join(str(p) for p in phases)
    s.to_csv(OUT / f"sls_{tag}__{variant}.csv")
    row = summarize(f"{tag}__{variant}", s)
    print(row)
    return row


if __name__ == "__main__":
    for v in (sys.argv[1:] or ["base"]):
        main(v)
