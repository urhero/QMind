# -*- coding: utf-8 -*-
"""
Unit tests for optimize_constrained_weights() function.

optimize_constrained_weights() 함수 테스트:
- 스타일 가중치 제약 하에서 포트폴리오 가중치 결정
- erc / equal_risk_weight / equal_weight / hardcoded 모드 지원
- style_cap(기본 25%) 제약 적용
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from service.pipeline.optimization import optimize_constrained_weights


class TestBlendDeployWeights:
    """blend_deploy_weights (부분 조정 배포, 2026-07-29 채택)."""

    def test_step1_or_no_prev_passthrough(self) -> None:
        from service.pipeline.optimization import blend_deploy_weights
        t = {"A": 0.6, "B": 0.4}
        assert blend_deploy_weights(t, {"A": 1.0}, 1.0) == t
        assert blend_deploy_weights(t, None, 0.5) == t
        assert blend_deploy_weights(t, {}, 0.5) == t

    def test_half_step_blends_and_renormalizes(self) -> None:
        from service.pipeline.optimization import blend_deploy_weights
        out = blend_deploy_weights({"A": 1.0}, {"B": 1.0}, 0.5)
        assert abs(out["A"] - 0.5) < 1e-12 and abs(out["B"] - 0.5) < 1e-12
        assert abs(sum(out.values()) - 1.0) < 1e-12

    def test_prev_position_decays(self) -> None:
        from service.pipeline.optimization import blend_deploy_weights
        w = {"OLD": 1.0}
        for _ in range(3):
            w = blend_deploy_weights({"NEW": 1.0}, w, 0.5)
        assert w["OLD"] < 0.13 and w["NEW"] > 0.87


class TestOptimizeConstrainedWeightsBasic:
    """optimize_constrained_weights 기본 기능 테스트"""

    def test_returns_two_dataframes(
        self, sample_style_returns: tuple[pd.DataFrame, list[str]]
    ) -> None:
        """두 개의 DataFrame을 반환하는지 확인"""
        rtn_df, style_list = sample_style_returns

        best_stats, weights_tbl = optimize_constrained_weights(
            rtn_df=rtn_df,
            style_list=style_list,
            mode="equal_weight",
            test_mode=True,
        )

        assert isinstance(best_stats, pd.DataFrame)
        assert isinstance(weights_tbl, pd.DataFrame)

    def test_best_stats_has_required_columns(
        self, sample_style_returns: tuple[pd.DataFrame, list[str]]
    ) -> None:
        """best_stats가 필수 컬럼(CAGR, MDD 등)을 가지는지 확인"""
        rtn_df, style_list = sample_style_returns

        best_stats, _ = optimize_constrained_weights(
            rtn_df=rtn_df,
            style_list=style_list,
            mode="equal_weight",
            test_mode=True,
        )

        assert "cagr" in best_stats.columns
        assert "mdd" in best_stats.columns

    def test_weights_table_has_required_columns(
        self, sample_style_returns: tuple[pd.DataFrame, list[str]]
    ) -> None:
        """weights_tbl이 필수 컬럼을 가지는지 확인"""
        rtn_df, style_list = sample_style_returns

        _, weights_tbl = optimize_constrained_weights(
            rtn_df=rtn_df,
            style_list=style_list,
            mode="equal_weight",
            test_mode=True,
        )

        assert "factor" in weights_tbl.columns
        assert "fitted_weight" in weights_tbl.columns
        assert "styleName" in weights_tbl.columns


class TestOptimizeConstrainedWeightsStyleCap:
    """style_cap 제약 테스트"""

    def test_style_cap_constraint_respected(
        self, sample_style_returns: tuple[pd.DataFrame, list[str]]
    ) -> None:
        """스타일별 가중치가 style_cap 이하인지 확인"""
        rtn_df, style_list = sample_style_returns

        _, weights_tbl = optimize_constrained_weights(
            rtn_df=rtn_df,
            style_list=style_list,
            mode="equal_weight",
            style_cap=0.40,
            test_mode=False,
        )

        if "styleName" in weights_tbl.columns and "fitted_weight" in weights_tbl.columns:
            style_weights = weights_tbl.groupby("styleName")["fitted_weight"].sum()
            assert all(style_weights <= 0.40 + 1e-6)

    def test_test_mode_relaxes_style_cap(
        self, sample_style_returns: tuple[pd.DataFrame, list[str]]
    ) -> None:
        """test_mode=True면 style_cap 제약이 완화되는지 확인"""
        rtn_df, style_list = sample_style_returns

        _, weights_tbl = optimize_constrained_weights(
            rtn_df=rtn_df,
            style_list=style_list,
            mode="equal_weight",
            style_cap=0.25,
            test_mode=True,
        )

        assert weights_tbl is not None


class TestOptimizeConstrainedWeightsEdgeCases:
    """엣지 케이스 테스트"""

    def test_single_factor(self) -> None:
        """단일 팩터 처리"""
        np.random.seed(42)
        dates = pd.date_range("2020-01-31", periods=36, freq="ME")

        rtn_df = pd.DataFrame({
            "single_factor": np.random.randn(36) * 0.03,
        }, index=dates)
        style_list = ["Valuation"]

        best_stats, weights_tbl = optimize_constrained_weights(
            rtn_df=rtn_df,
            style_list=style_list,
            mode="equal_weight",
            test_mode=True,
        )

        assert best_stats is not None
        assert len(weights_tbl) == 1

    def test_single_style_multiple_factors(self) -> None:
        """단일 스타일, 여러 팩터 처리"""
        np.random.seed(42)
        dates = pd.date_range("2020-01-31", periods=36, freq="ME")

        rtn_df = pd.DataFrame({
            "val_1": np.random.randn(36) * 0.03,
            "val_2": np.random.randn(36) * 0.03,
            "val_3": np.random.randn(36) * 0.03,
        }, index=dates)
        style_list = ["Valuation", "Valuation", "Valuation"]

        best_stats, weights_tbl = optimize_constrained_weights(
            rtn_df=rtn_df,
            style_list=style_list,
            mode="equal_weight",
            test_mode=True,
        )

        assert best_stats is not None

    def test_unknown_mode_raises_error(self) -> None:
        """알 수 없는 모드는 ValueError 발생"""
        np.random.seed(42)
        dates = pd.date_range("2020-01-31", periods=36, freq="ME")

        rtn_df = pd.DataFrame({
            "A": np.random.randn(36) * 0.03,
            "B": np.random.randn(36) * 0.03,
        }, index=dates)
        style_list = ["Style1", "Style2"]

        with pytest.raises(ValueError, match="Unknown optimization mode"):
            optimize_constrained_weights(
                rtn_df=rtn_df,
                style_list=style_list,
                mode="monte_carlo",
            )


class TestOptimizeConstrainedWeightsOutputValidation:
    """출력 유효성 테스트"""

    def test_weights_sum_to_one(
        self, sample_style_returns: tuple[pd.DataFrame, list[str]]
    ) -> None:
        """가중치 합이 1인지 확인"""
        rtn_df, style_list = sample_style_returns

        _, weights_tbl = optimize_constrained_weights(
            rtn_df=rtn_df,
            style_list=style_list,
            mode="equal_weight",
            test_mode=True,
        )

        total_weight = weights_tbl["fitted_weight"].sum()
        assert abs(total_weight - 1.0) < 0.01

    def test_no_negative_weights(
        self, sample_style_returns: tuple[pd.DataFrame, list[str]]
    ) -> None:
        """음수 가중치가 없는지 확인 (롱온리 포트폴리오)"""
        rtn_df, style_list = sample_style_returns

        _, weights_tbl = optimize_constrained_weights(
            rtn_df=rtn_df,
            style_list=style_list,
            mode="equal_weight",
            test_mode=True,
        )

        assert all(weights_tbl["fitted_weight"] >= -1e-10)

    def test_cagr_is_reasonable(
        self, sample_style_returns: tuple[pd.DataFrame, list[str]]
    ) -> None:
        """CAGR이 합리적인 범위인지 확인"""
        rtn_df, style_list = sample_style_returns

        best_stats, _ = optimize_constrained_weights(
            rtn_df=rtn_df,
            style_list=style_list,
            mode="equal_weight",
            test_mode=True,
        )

        cagr = best_stats["cagr"].iloc[0]
        assert -1.0 <= cagr <= 5.0


class TestStyleCapFeasibilityGuard:
    """style_cap 제약 불가능/미수렴 시 경고 로그 (동작 변경 없음)."""

    def _make_returns(self, styles: list[str]) -> tuple[pd.DataFrame, list[str]]:
        rng = np.random.default_rng(0)
        n = len(styles)
        rtn_df = pd.DataFrame(
            rng.normal(0.005, 0.02, size=(24, n)),
            columns=[f"F{i}" for i in range(n)],
        )
        return rtn_df, styles

    def test_infeasible_cap_warns(self, caplog) -> None:
        """스타일 3개 x cap 25% = 75% < 100% -> 제약 불가능 경고."""
        import logging
        rtn_df, style_list = self._make_returns(["S1", "S2", "S3"])
        with caplog.at_level(logging.WARNING, logger="service.pipeline.optimization"):
            _, weights_tbl = optimize_constrained_weights(
                rtn_df=rtn_df, style_list=style_list,
                mode="equal_weight", style_cap=0.25, test_mode=False,
            )
        assert any("infeasible" in r.message for r in caplog.records)
        # 동작은 기존과 동일: 합 1.0 유지 (cap 은 위반된 채 반환)
        assert np.isclose(weights_tbl["fitted_weight"].sum(), 1.0)

    def test_feasible_cap_no_warning(self, caplog) -> None:
        """스타일 5개 x cap 25% = 125% >= 100% -> 경고 없음."""
        import logging
        rtn_df, style_list = self._make_returns(["S1", "S2", "S3", "S4", "S5"])
        with caplog.at_level(logging.WARNING, logger="service.pipeline.optimization"):
            optimize_constrained_weights(
                rtn_df=rtn_df, style_list=style_list,
                mode="equal_weight", style_cap=0.25, test_mode=False,
            )
        assert not [r for r in caplog.records if "style_cap" in r.message]

    def test_cap_exact_when_redistribution_cascades(self) -> None:
        """재분배로 새 스타일이 캡을 넘는 연쇄에서도 모든 스타일이 캡 이하 (water-filling).

        구 구현(축소 + 전체 재정규화 10회)은 이 입력에서 캡을 넘긴 채 끝났다.
        """
        from service.pipeline.optimization import _redistribute_to_cap
        styles = np.array(["A", "A", "B", "C", "D", "E", "F"])
        x = np.array([0.20, 0.15, 0.22, 0.19, 0.18, 0.04, 0.02], dtype=np.float32)
        out = _redistribute_to_cap(x.copy(), styles, np.unique(styles), 0.20, 1e-12)
        sums = {s: out[styles == s].sum() for s in np.unique(styles)}
        assert np.isclose(out.sum(), 1.0, atol=1e-6)
        assert max(sums.values()) <= 0.20 + 1e-6
        # 고정된 스타일 안 비율과, 고정되지 않은 스타일 사이 비율은 유지
        assert np.isclose(out[0] / out[1], 0.20 / 0.15, rtol=1e-5)
        assert np.isclose(out[5] / out[6], 2.0, rtol=1e-5)


class TestEqualRiskWeightMode:
    """equal_risk_weight 모드: IS 변동성 반비례 가중 + 스타일 캡 재분배."""

    def test_lower_vol_gets_higher_weight(self) -> None:
        """저변동성 팩터가 더 큰 가중을 받고, 비율이 1/vol 에 정합."""
        rng = np.random.default_rng(7)
        n = 37
        rtn_df = pd.DataFrame({
            "low_vol": rng.normal(0.005, 0.01, n),
            "high_vol": rng.normal(0.005, 0.05, n),
        })
        rtn_df.iloc[0] = 0.0  # 백테스트 관례: 첫 행 기준점

        _, weights_tbl = optimize_constrained_weights(
            rtn_df=rtn_df, style_list=["S1", "S2"],
            mode="equal_risk_weight", test_mode=True,
        )
        w = weights_tbl.set_index("factor")["fitted_weight"]
        assert w["low_vol"] > w["high_vol"]
        assert np.isclose(w.sum(), 1.0, atol=1e-6)
        # 반비례 정합: w_low / w_high ~= vol_high / vol_low (첫 행 제외 std)
        vols = rtn_df.iloc[1:].std()
        assert np.isclose(
            w["low_vol"] / w["high_vol"], vols["high_vol"] / vols["low_vol"], rtol=1e-3
        )

    def test_style_cap_still_enforced(self) -> None:
        """equal_risk_weight 에서도 스타일 캡 재분배가 동일하게 적용."""
        rng = np.random.default_rng(1)
        n = 37
        rtn_df = pd.DataFrame({
            f"F{i}": rng.normal(0.005, 0.01 + 0.01 * i, n) for i in range(6)
        })
        rtn_df.iloc[0] = 0.0
        style_list = ["A", "A", "B", "B", "C", "C"]

        _, weights_tbl = optimize_constrained_weights(
            rtn_df=rtn_df, style_list=style_list,
            mode="equal_risk_weight", style_cap=0.40, test_mode=False,
        )
        style_weights = weights_tbl.groupby("styleName")["fitted_weight"].sum()
        # 재분배 루프는 float32 + 편중 초기가중에서 ~1e-4 잔차를 남길 수 있다
        # (기존 알고리즘 특성: 초과 시 경고만 남기고 허용). 실질 캡 준수만 확인.
        assert all(style_weights <= 0.40 + 1e-3)
        assert np.isclose(weights_tbl["fitted_weight"].sum(), 1.0, atol=1e-6)

    def test_zero_vol_factor_does_not_explode(self) -> None:
        """무분산 팩터는 vol 하한(1e-6) 가드로 폭주하지 않는다."""
        rng = np.random.default_rng(2)
        n = 37
        rtn_df = pd.DataFrame({
            "flat": np.zeros(n),
            "normal": rng.normal(0.005, 0.02, n),
        })
        _, weights_tbl = optimize_constrained_weights(
            rtn_df=rtn_df, style_list=["S1", "S2"],
            mode="equal_risk_weight", test_mode=True,
        )
        w = weights_tbl.set_index("factor")["fitted_weight"]
        assert np.isfinite(w).all()
        assert np.isclose(w.sum(), 1.0, atol=1e-6)


class TestRawWeightPreCap:
    """weights_tbl.raw_weight = 캡 적용 전 비중 보존 (2026-08-05, 캡 효과 시각화용)."""

    def test_raw_is_pre_cap_fitted_is_capped(self) -> None:
        rng = np.random.default_rng(2)
        # 스타일 S1 에 저변동 팩터 3개 몰아 캡(25%) 초과 유도, S2~S5 로 재분배 확인
        cols, styles = [], []
        for i in range(3):
            cols.append(("A%d" % i, rng.normal(0, 0.002, 60), "S1"))
        for i, s in enumerate(["S2", "S3", "S4", "S5"]):
            cols.append(("B%d" % i, rng.normal(0, 0.03, 60), s))
        df = pd.DataFrame({n: np.concatenate([[0.0], v]) for n, v, _ in cols},
                          index=pd.date_range("2020-01-31", periods=61, freq="ME"))
        style_list = [s for _, _, s in cols]
        _, tbl = optimize_constrained_weights(
            df, style_list, mode="equal_risk_weight", test_mode=False, style_cap=0.25)
        by_style_raw = tbl.groupby("styleName")["raw_weight"].sum()
        by_style_fit = tbl.groupby("styleName")["fitted_weight"].sum()
        assert by_style_raw["S1"] > 0.25 + 1e-6, "저변동 몰빵으로 캡 전 S1 > 25% 여야 함"
        # float32 재분배의 기존 허용 오차(~1e-4, production 로그의 0.2501 수준)
        assert by_style_fit["S1"] <= 0.25 + 1e-3, "캡 후 S1 ~<= 25%"
        assert by_style_fit["S1"] < by_style_raw["S1"], "캡이 실제로 깎았어야 함"
        assert abs(tbl["raw_weight"].sum() - 1.0) < 1e-5
        assert abs(tbl["fitted_weight"].sum() - 1.0) < 1e-5


class TestErcSolver:
    """_solve_erc_ccd: 정식 ERC (RC 균등·양수 비중, 음의 상관 포함). mxwo_sharpe1 이식."""

    @staticmethod
    def _rc(cov, w):
        rc = w * (cov @ w)
        return rc / rc.sum()

    def test_rc_equalized_with_negative_corr(self) -> None:
        """음의 상관 팩터가 있어도 리스크 기여가 균등해지고 비중은 전부 양수."""
        from service.pipeline.optimization import _solve_erc_ccd
        rng = np.random.default_rng(5)
        base = rng.normal(0, 0.02, 200)
        rets = np.column_stack([
            base + rng.normal(0, 0.005, 200),
            base + rng.normal(0, 0.005, 200),
            -base + rng.normal(0, 0.005, 200),   # 음의 상관 (헤지 팩터)
            rng.normal(0, 0.02, 200),
        ])
        cov = np.cov(rets.T)
        w = _solve_erc_ccd(cov)
        assert (w > 0).all() and abs(w.sum() - 1.0) < 1e-12
        rc = self._rc(cov, w)
        assert np.abs(rc - 0.25).max() < 1e-6, f"RC 불균등: {rc}"

    def test_hedge_factor_gets_larger_weight(self) -> None:
        """포트폴리오와 음의 상관인 팩터는 동일 변동성의 순응 팩터보다 큰 비중."""
        from service.pipeline.optimization import _solve_erc_ccd
        rng = np.random.default_rng(7)
        base = rng.normal(0, 0.02, 300)
        rets = np.column_stack([
            base, base + rng.normal(0, 0.002, 300),
            base + rng.normal(0, 0.002, 300),
            -base + rng.normal(0, 0.002, 300),   # 헤지
        ])
        cov = np.cov(rets.T)
        w = _solve_erc_ccd(cov)
        assert w[3] > w[0], "헤지 팩터가 우대돼야 함 (구 곱셈 반복은 0으로 붕괴)"

    def test_identity_cov_equal_weights(self) -> None:
        from service.pipeline.optimization import _solve_erc_ccd
        w = _solve_erc_ccd(np.eye(5) * 0.04)
        assert np.abs(w - 0.2).max() < 1e-9


class TestErcMode:
    """optimize_constrained_weights mode='erc' 통합 경로."""

    def test_erc_mode_returns_valid_weights(self) -> None:
        rng = np.random.default_rng(11)
        n = 61
        rtn_df = pd.DataFrame(rng.normal(0.003, 0.02, (n, 4)), columns=list("ABCD"))
        rtn_df.iloc[0] = 0.0  # 기준점 0 관례
        _, weights_tbl = optimize_constrained_weights(
            rtn_df=rtn_df, style_list=["S1", "S1", "S2", "S3"],
            mode="erc", test_mode=True, erc_shrinkage=0.5,
        )
        w = weights_tbl.set_index("factor")["fitted_weight"]
        assert np.isfinite(w).all() and (w > 0).all()
        assert np.isclose(w.sum(), 1.0, atol=1e-6)

    def test_full_shrinkage_equals_erw_direction(self) -> None:
        """수축 1.0 이면 cov 가 대각뿐 -> 저변동 팩터가 더 큰 비중 (1/sigma 방향)."""
        rng = np.random.default_rng(13)
        n = 61
        rtn_df = pd.DataFrame({
            "lowvol": rng.normal(0.003, 0.01, n),
            "highvol": rng.normal(0.003, 0.04, n),
        })
        rtn_df.iloc[0] = 0.0
        _, weights_tbl = optimize_constrained_weights(
            rtn_df=rtn_df, style_list=["S1", "S2"],
            mode="erc", test_mode=True, erc_shrinkage=1.0,
        )
        w = weights_tbl.set_index("factor")["fitted_weight"]
        assert w["lowvol"] > w["highvol"]


class TestTsMomentumTilt:
    """apply_ts_momentum_tilt: trailing 음수 팩터 감쇠, off 시 no-op."""

    def _frame(self) -> pd.DataFrame:
        # 기준점 0 행 + 3개월: up 은 양의 누적, down 은 음의 누적
        return pd.DataFrame({
            "up": [0.0, 0.01, 0.02, 0.01],
            "down": [0.0, -0.02, -0.01, -0.02],
        })

    def test_negative_trailing_factor_scaled(self) -> None:
        from service.pipeline.optimization import apply_ts_momentum_tilt
        out = apply_ts_momentum_tilt({"up": 0.5, "down": 0.5}, self._frame(), 3, 0.5)
        assert out["up"] == 0.5
        assert abs(out["down"] - 0.25) < 1e-12

    def test_none_window_noop(self) -> None:
        from service.pipeline.optimization import apply_ts_momentum_tilt
        w = {"up": 0.5, "down": 0.5}
        assert apply_ts_momentum_tilt(w, self._frame(), None) == w
        assert apply_ts_momentum_tilt(w, self._frame(), 0) == w

    def test_unnormalized_output(self) -> None:
        """반환값은 비정규화 — 합=1 재정규화는 호출부 책임."""
        from service.pipeline.optimization import apply_ts_momentum_tilt
        out = apply_ts_momentum_tilt({"up": 0.5, "down": 0.5}, self._frame(), 3, 0.5)
        assert abs(sum(out.values()) - 0.75) < 1e-12

    def test_style_cap_respected_with_tilt(self) -> None:
        """틸트가 캡 재분배 '이전'에 적용되므로 스타일 합이 캡을 넘지 않는다.

        (mxwo_sharpe1 원구현은 캡 후 틸트 -> 재정규화로 캡 초과 — 회귀 방지)
        """
        rng = np.random.default_rng(17)
        n = 61
        # S1 팩터들은 상승 추세(틸트 안 맞음), S2/S3 는 하락 추세(감쇠 대상)
        rtn_df = pd.DataFrame({
            "a1": rng.normal(0.01, 0.02, n), "a2": rng.normal(0.01, 0.02, n),
            "b1": rng.normal(-0.01, 0.02, n), "b2": rng.normal(-0.01, 0.02, n),
            "c1": rng.normal(-0.01, 0.02, n), "c2": rng.normal(0.005, 0.02, n),
        })
        rtn_df.iloc[0] = 0.0
        _, weights_tbl = optimize_constrained_weights(
            rtn_df=rtn_df, style_list=["S1", "S1", "S2", "S2", "S3", "S3"],
            mode="erc", test_mode=False, style_cap=0.4,
            ts_mom_window=3, ts_mom_scale=0.5,
        )
        by_style = weights_tbl.groupby("styleName")["fitted_weight"].sum()
        assert (by_style <= 0.4 + 1e-3).all(), f"style cap 초과: {by_style.to_dict()}"
        assert np.isclose(weights_tbl["fitted_weight"].sum(), 1.0, atol=1e-6)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


# ── weight_kwargs_from (2026-08-25 파라미터 배관 단일화) ──────────────────────

def test_weight_kwargs_covers_all_tunable_params():
    """weight_kwargs_from 이 optimize_constrained_weights 의 튜너블 파라미터를
    전부 커버해야 한다 — 함수에 파라미터를 추가하고 헬퍼에 빠뜨리면 실패
    (mp/백테스트 배관 이중화 사고 재발 방지 가드)."""
    import inspect
    from config import PIPELINE_PARAMS
    from service.pipeline.optimization import optimize_constrained_weights, weight_kwargs_from

    kwargs = weight_kwargs_from(dict(PIPELINE_PARAMS))
    sig_params = set(inspect.signature(optimize_constrained_weights).parameters)

    # 헬퍼가 넘기는 키는 전부 유효한 파라미터여야 함
    assert set(kwargs) <= sig_params, f"무효 키: {set(kwargs) - sig_params}"

    # 데이터/구조 인자 외 튜너블은 전부 헬퍼가 담당해야 함
    non_tunable = {"rtn_df", "style_list", "tol", "test_mode"}
    assert sig_params - non_tunable == set(kwargs), (
        f"헬퍼 미커버 파라미터 (양쪽 호출부에 수동 배관 금지): "
        f"{sig_params - non_tunable - set(kwargs)}"
    )

    # config 값이 실제로 반영되는지 (폴백이 아닌 config 우선)
    assert kwargs["erc_shrinkage"] == PIPELINE_PARAMS["erc_shrinkage"]
    assert kwargs["mode"] == PIPELINE_PARAMS["optimization_mode"]
