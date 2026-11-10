# -*- coding: utf-8 -*-
"""Walk-Forward OOS 순수성 계약 테스트.

CLAUDE.md 가 가장 강조하는 규칙을 핀으로 고정한다:
  "_apply_rules_and_aggregate() 에서 filter_and_label_factors() 를 전체
   데이터로 재실행하면 OOS look-ahead bias 발생. 반드시 rule_bundle 의
   IS 전용 규칙(dropped_sectors, label_rules)을 직접 매핑할 것."

설계: IS 구간과 전체 기간에서 섹터 필터 결론이 '갈라지는' 합성 데이터를
만든다 — S2 섹터는 IS 에서 스프레드 양(+)이지만 OOS 에서 폭락해 전체
기간 기준으로는 음(-). IS 규칙을 따르면 S2 가 포트폴리오에 남고(계약),
전체 데이터로 재학습하면 S2 가 제거된다(위반). 두 경우 OOS 수익률이
크게 달라지므로(-18% vs +4%) 회귀를 확실히 잡는다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import service.backtest.walk_forward_engine as wfe
from service.backtest.data_slicer import slice_data_by_date
from service.backtest.walk_forward_engine import (
    _apply_rules_and_aggregate,
    _run_rule_learning,
)
from service.pipeline.factor_analysis import (
    ANALYZE_COLS,
    calculate_factor_stats_batch,
    filter_and_label_factors,
)
from service.pipeline.model_portfolio import ModelPortfolioPipeline

IS_END = pd.Timestamp("2024-06-30")
OOS_CHECK_MONTH = pd.Timestamp("2024-08-31")

# 계약(IS 규칙 적용, S2 유지) 시 OOS 월 수익률: long(2%, -20%)/2 + short 대칭 = -18%
EXPECTED_CONTRACT_RETURN = -0.18
# 재학습(전체 기간, S2 제거) 시: long={A1}=+2%, short={A4}=+2% -> +4%
EXPECTED_RELEARNED_RETURN = 0.04


def _precompute_full_stats(pipeline, raw, mret):
    """run() 의 전체 데이터 5분위 통계 사전계산을 동일하게 재현한다.

    _apply_rules_and_aggregate 가 내부 재계산 대신 사전계산된 stats 를 받도록
    리팩터됨(루프 불변값 hoisting). 테스트도 run() 과 동일하게 1회 계산해 전달한다.
    """
    _, merged_full, abbrs, orders = pipeline._prepare_metadata(raw, mret)
    slim_full = merged_full[[c for c in ANALYZE_COLS if c in merged_full.columns]]
    stats_full = calculate_factor_stats_batch(
        slim_full, abbrs, orders, test_mode=True,
        min_sector_stocks=pipeline.pipeline_params["min_sector_stocks"],
    )
    return stats_full, abbrs


def _build_synthetic() -> tuple[pd.DataFrame, pd.DataFrame]:
    """(raw_data, mreturn_df) — 10개월, 2개 섹터 x 4종목, 팩터 F1.

    factorOrder=1 (값이 낮을수록 좋음) -> Q1 = 최저 val (A1, B1).
    S1: 전 기간 Q1 +2% / Q5 -2% (스프레드 항상 +).
    S2: 1-6월 Q1 +2% / Q5 -2%, 7-10월 Q1 -20% / Q5 +20% (전체 평균 스프레드 -).
    """
    dates = pd.date_range("2024-01-31", periods=10, freq="ME")
    stocks = [
        # (gvkeyiid, sec, val, ret_is, ret_oos)
        ("A1", "S1", 1.0, 0.02, 0.02),
        ("A2", "S1", 2.0, 0.00, 0.00),
        ("A3", "S1", 3.0, 0.00, 0.00),
        ("A4", "S1", 4.0, -0.02, -0.02),
        ("B1", "S2", 1.0, 0.02, -0.20),
        ("B2", "S2", 2.0, 0.00, 0.00),
        ("B3", "S2", 3.0, 0.00, 0.00),
        ("B4", "S2", 4.0, -0.02, 0.20),
    ]
    raw_rows, mret_rows = [], []
    for d in dates:
        is_period = d <= IS_END
        for g, sec, val, r_is, r_oos in stocks:
            raw_rows.append({
                "gvkeyiid": g, "ddt": d, "sec": sec, "val": val,
                "factorAbbreviation": "F1", "factorOrder": 1,
            })
            mret_rows.append({
                "gvkeyiid": g, "ddt": d,
                "M_RETURN": r_is if is_period else r_oos,
            })
    return pd.DataFrame(raw_rows), pd.DataFrame(mret_rows)


@pytest.fixture()
def pipeline(tmp_path) -> ModelPortfolioPipeline:
    info = tmp_path / "factor_info.csv"
    info.write_text(
        "factorAbbreviation,factorName,styleName,factorOrder\n"
        "F1,Factor One,TestStyle,1\n",
        encoding="utf-8",
    )
    return ModelPortfolioPipeline(
        config={},
        factor_info_path=info,
        is_test=True,
        pipeline_params={
            "min_sector_stocks": 10,        # test_mode 라 미적용
            "spread_threshold_pct": 0.10,
            "backtest_start": "2024-01-01",
            "transaction_cost_bps": 0.0,    # 수기 검산 단순화
        },
    )


class TestOOSPurityContract:
    def test_is_rules_diverge_from_full_relearn(self, pipeline):
        """데이터 설계 검증: IS 규칙은 S2 유지, 전체 재학습은 S2 제거.

        이 분기가 성립해야 아래 계약 테스트가 회귀를 잡을 수 있다.
        """
        raw, mret = _build_synthetic()
        is_raw, is_mret = slice_data_by_date(raw, mret, IS_END)
        bundle = _run_rule_learning(is_raw, is_mret, pipeline, test_file="syn")

        # IS 규칙: 두 섹터 모두 스프레드 + -> 제거 섹터 없음
        assert "F1" not in bundle["dropped_sectors"]
        assert bundle["label_rules"]["F1"]["Q1"] == 1
        assert bundle["label_rules"]["F1"]["Q5"] == -1

        # 전체 기간 재학습이라면: S2 의 누적 스프레드가 음 -> 제거됨
        _, merged_full, abbrs, orders = pipeline._prepare_metadata(raw, mret)
        stats_full = calculate_factor_stats_batch(
            merged_full, abbrs, orders, test_mode=True,
        )
        _, _, _, _, dropped_full, _ = filter_and_label_factors(
            abbrs, ["Factor One"], ["TestStyle"], stats_full,
            spread_threshold_pct=0.10,
        )
        assert "S2" in dropped_full[0], "전체 재학습 시 S2 가 제거되어야 분기 성립"

    def test_apply_rules_uses_is_rules_not_full_relearn(self, pipeline):
        """계약: 사전계산 수익률은 IS 규칙(S2 포함)을 반영해야 한다.

        재학습으로 바뀌면 OOS 월 수익률이 -18% 가 아닌 +4% 로 나와 실패한다.
        """
        raw, mret = _build_synthetic()
        is_raw, is_mret = slice_data_by_date(raw, mret, IS_END)
        bundle = _run_rule_learning(is_raw, is_mret, pipeline, test_file="syn")

        stats_full, abbrs = _precompute_full_stats(pipeline, raw, mret)
        ret_df = _apply_rules_and_aggregate(stats_full, abbrs, bundle, pipeline)

        assert "F1" in ret_df.columns
        # 사전계산은 전체 기간을 커버해야 한다 (OOS 월 조회 가능)
        assert ret_df.index.max() == pd.Timestamp("2024-10-31")

        oos_ret = float(ret_df.loc[OOS_CHECK_MONTH, "F1"])
        assert oos_ret == pytest.approx(EXPECTED_CONTRACT_RETURN, abs=1e-9), (
            f"OOS 수익률 {oos_ret:.4f} != 계약값 {EXPECTED_CONTRACT_RETURN} — "
            f"재학습값 {EXPECTED_RELEARNED_RETURN} 에 가깝다면 look-ahead 회귀"
        )

    def test_apply_rules_never_calls_filter_and_label(self, pipeline, monkeypatch):
        """구조 계약: _apply_rules_and_aggregate 는 filter_and_label_factors
        (규칙 학습 함수)를 절대 호출하지 않는다."""
        raw, mret = _build_synthetic()
        is_raw, is_mret = slice_data_by_date(raw, mret, IS_END)
        bundle = _run_rule_learning(is_raw, is_mret, pipeline, test_file="syn")

        def _forbidden(*args, **kwargs):
            raise AssertionError("filter_and_label_factors 가 OOS 적용 경로에서 호출됨 (재학습 금지)")

        monkeypatch.setattr(wfe, "filter_and_label_factors", _forbidden)
        stats_full, abbrs = _precompute_full_stats(pipeline, raw, mret)
        ret_df = _apply_rules_and_aggregate(stats_full, abbrs, bundle, pipeline)
        assert not ret_df.empty

    def test_start_truncates_without_changing_window_returns(self, pipeline):
        """롤링 IS: start 를 주면 그 달부터만 집계하고, 창 구간 수익률은 전체 집계와 같다.

        첫 행은 기준점(수익 0, 호출부가 0 으로 덮음)이라 비교에서 뺀다.
        """
        raw, mret = _build_synthetic()
        is_raw, is_mret = slice_data_by_date(raw, mret, IS_END)
        bundle = _run_rule_learning(is_raw, is_mret, pipeline, test_file="syn")
        stats_full, abbrs = _precompute_full_stats(pipeline, raw, mret)

        full = _apply_rules_and_aggregate(stats_full, abbrs, bundle, pipeline)
        start = pd.Timestamp("2024-05-31")
        win = _apply_rules_and_aggregate(stats_full, abbrs, bundle, pipeline, start=start)

        assert win.index.min() == start
        assert win.index.max() == full.index.max()
        pd.testing.assert_frame_equal(win.iloc[1:], full.loc[win.index[1:]], atol=1e-12)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
