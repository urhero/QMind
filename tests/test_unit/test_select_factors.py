# -*- coding: utf-8 -*-
"""select_factors 단일 진입점 (mp / walk-forward 공유) + backtest CLI parity 계약 테스트."""
from __future__ import annotations

import numpy as np
import pandas as pd

from service.factor.selection import select_factors


def _scores(vals: dict[str, float]) -> pd.Series:
    # 호출부 계약: rank_score 내림차순으로 정렬된 Series (meta 정렬 순서)
    return pd.Series(vals).sort_values(ascending=False)


def _rets(cols: str) -> pd.DataFrame:
    return pd.DataFrame(np.zeros((5, len(cols))), columns=list(cols))


def test_topn_without_dedup_keeps_caller_order():
    s = _scores({"a": 3.0, "b": 2.0, "c": 1.0, "d": 0.5})
    assert select_factors(_rets("abcd"), s, {"use_cluster_dedup": False}, top_n=2) == ["a", "b"]


def test_hysteresis_reverts_small_gap_swap():
    # incumbent c 와 challenger b 의 격차 0.1 < margin 0.25 -> 교체 되돌림
    s = _scores({"a": 3.0, "b": 2.0, "c": 1.9})
    out = select_factors(_rets("abc"), s, {"use_cluster_dedup": False}, top_n=2,
                         incumbents={"a", "c"}, margin=0.25)
    assert set(out) == {"a", "c"}


def test_hysteresis_noop_preserves_order_when_set_unchanged():
    # 격차 1.9 >= margin -> 교체 유지, 원래 순서 그대로 (ERC cov 열 순서 보존 계약)
    s = _scores({"a": 3.0, "b": 2.0, "c": 0.1})
    out = select_factors(_rets("abc"), s, {"use_cluster_dedup": False}, top_n=2,
                         incumbents={"a", "c"}, margin=0.25)
    assert out == ["a", "b"]


def test_engine_top_factors_defaults_to_config():
    """CLI --top-factors 미지정 시 엔진은 config top_factor_count 를 따른다 (parity)."""
    from config import PIPELINE_PARAMS
    from service.backtest.walk_forward_engine import WalkForwardEngine

    engine = WalkForwardEngine()
    assert engine.top_factors is None
    # run() 의 pp 세팅 규칙 재현: None 이면 덮어쓰지 않는다
    pp = dict(PIPELINE_PARAMS)
    if engine.top_factors is not None:
        pp["top_factor_count"] = engine.top_factors
    assert pp["top_factor_count"] == PIPELINE_PARAMS["top_factor_count"]
