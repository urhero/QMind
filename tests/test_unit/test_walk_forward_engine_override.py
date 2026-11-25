# -*- coding: utf-8 -*-
"""WalkForwardEngine.pipeline_params_override 회귀 테스트."""
from __future__ import annotations

from service.backtest.walk_forward_engine import (
    WalkForwardEngine,
    _resolve_backtest_cost_bps,
)


def test_engine_accepts_none_override():
    """override=None 기본값은 기존 동작과 동일해야 한다."""
    engine = WalkForwardEngine()
    assert engine.pipeline_params_override is None


def test_engine_stores_override_dict():
    """주입된 override dict 는 인스턴스 속성으로 저장된다."""
    override = {"use_cluster_dedup": True, "n_clusters": 10, "per_cluster_keep": 2}
    engine = WalkForwardEngine(pipeline_params_override=override)
    assert engine.pipeline_params_override == override


def test_override_does_not_mutate_global_pipeline_params():
    """override 주입은 config.PIPELINE_PARAMS 모듈 상수를 변경하지 않는다."""
    from config import PIPELINE_PARAMS
    before = dict(PIPELINE_PARAMS)
    engine = WalkForwardEngine(
        pipeline_params_override={"use_cluster_dedup": True, "n_clusters": 99}
    )
    assert engine.pipeline_params_override == {"use_cluster_dedup": True, "n_clusters": 99}
    # PIPELINE_PARAMS 자체는 불변
    assert PIPELINE_PARAMS == before
    assert PIPELINE_PARAMS.get("n_clusters") != 99


def test_override_can_change_top_factor_count():
    """override 의 top_factor_count 가 CLI 의 top_factors 를 덮어쓸 수 있다.

    `pp` 세팅 순서가: 기본 -> top_factor_count=self.top_factors -> override 적용.
    따라서 override 에 top_factor_count 가 있으면 최종적으로 그 값이 살아남는다.
    """
    # WalkForwardEngine 의 internal logic 을 직접 호출하기 어려우므로
    # __init__ 후 store 되는지 확인 + run() 의 pp 세팅 의도를 inline 으로 재현 검증
    engine = WalkForwardEngine(
        top_factors=50,
        pipeline_params_override={"top_factor_count": 18},
    )
    # 인스턴스 속성으로는 50 / 18 모두 보존
    assert engine.top_factors == 50
    assert engine.pipeline_params_override == {"top_factor_count": 18}

    # run() 의 pp 세팅 로직 inline 재현 (regression-safe 검증)
    from config import PIPELINE_PARAMS
    pp = dict(PIPELINE_PARAMS)
    pp["top_factor_count"] = engine.top_factors
    if engine.pipeline_params_override:
        pp.update(engine.pipeline_params_override)
    assert pp["top_factor_count"] == 18  # override 가 이김


def test_save_outputs_false_skips_output_dir_writes(monkeypatch, tmp_path):
    """save_outputs=False 면 정식 산출물 경로에 아무것도 쓰지 않는다 (실험 러너 보호).

    저장 분기는 실데이터 run() 에서만 타므로 (test_file 모드는 원래 저장 생략)
    유닛 테스트에서는 소스의 가드가 두 저장 블록 모두에 걸려 있는지를 확인한다.
    """
    import inspect

    assert WalkForwardEngine().save_outputs is True
    assert WalkForwardEngine(save_outputs=False).save_outputs is False
    src = inspect.getsource(WalkForwardEngine.run)
    assert "save = self.save_outputs and not test_file" in src
    # 두 to_csv 저장 모두 `if save and` 가드 아래에 있다
    assert src.count("if save and") == src.count(".to_csv(") == 2


# ── factor-level 백테스트 비용 보정 (_resolve_backtest_cost_bps) ──────────────

def test_backtest_cost_default_is_18bp():
    """기본 multiplier 0.6 -> 30bp x 0.6 = 18bp (MP-level 실측 netting ratio 0.574 근사)."""
    assert _resolve_backtest_cost_bps({"transaction_cost_bps": 30.0}) == 18.0


def test_backtest_cost_respects_multiplier():
    assert _resolve_backtest_cost_bps(
        {"transaction_cost_bps": 30.0, "backtest_cost_multiplier": 1.0}
    ) == 30.0
    assert _resolve_backtest_cost_bps(
        {"transaction_cost_bps": 30.0, "backtest_cost_multiplier": 2.5}
    ) == 75.0


def test_config_has_backtest_cost_multiplier():
    """config 에 multiplier 가 존재하고 양수 (실측 netting ratio 근사 — 구성별 재보정:
    MXCN1A/구채택본 0.6(실측 0.574~0.62), MXWO step0.5 1.1(실측 1.09))."""
    from config import PIPELINE_PARAMS
    assert PIPELINE_PARAMS.get("backtest_cost_multiplier", 0) > 0
