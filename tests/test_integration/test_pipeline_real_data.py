# -*- coding: utf-8 -*-
"""
실제 parquet 데이터를 사용한 파이프라인 통합 테스트.

연도별 분할 parquet (data/MXCN1A/factor_{YYYY}.parquet + mreturn.parquet)
로 전체 파이프라인을 실행하고 출력 품질을 검증한다. 날짜 범위는
ModelPortfolioPipeline._load_data 가 parquet 에서 스스로 도출하므로
파일명 파싱이 필요 없다.

주의: module fixture 가 실제 production 파이프라인을 1회 실행하므로
output/ 의 추적 파일들이 재생성된다 — 출력은 결정적이라 byte-identical
(다르면 그 자체가 회귀 신호).

실행 방법:
    # real_data 마커가 있는 테스트만 실행
    pipenv run python -m pytest tests/test_integration/test_pipeline_real_data.py -v

    # 전체 테스트에서 real_data 제외 (기본 CI 실행 시)
    pipenv run python -m pytest tests/ -m "not real_data" -v
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ═══════════════════════════════════════════════════════════════════════════════
# 경로 설정
# ═══════════════════════════════════════════════════════════════════════════════
PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "MXCN1A"  # 유니버스 폴더
from service.paths import OUTPUT_DIR  # output/{BENCHMARK}/
FACTOR_INFO_PATH = PROJECT_ROOT / "data" / "factor_info.csv"

# 연도별 분할 parquet 탐색 (파일명 = factor_{YYYY}.parquet, 연도순 정렬)
FACTOR_PARQUET_FILES = sorted(DATA_DIR.glob("factor_*.parquet"))
MRETURN_PARQUET = DATA_DIR / "mreturn.parquet"
HAS_REAL_DATA = bool(FACTOR_PARQUET_FILES) and MRETURN_PARQUET.exists()
LATEST_PARQUET = FACTOR_PARQUET_FILES[-1] if FACTOR_PARQUET_FILES else None  # 최신 연도 파일


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def pipeline_result():
    """실제 parquet 데이터로 파이프라인을 1회만 실행하고 결과를 캐시한다.

    scope="module" 이므로 이 모듈의 모든 테스트에서 동일한 실행 결과를 공유한다.
    파이프라인 실행 시간이 길기 때문에 중복 실행을 방지한다.

    날짜는 _load_data 가 parquet 범위에서 도출하므로 인자로 전달하지 않는다
    (연도분할 경로에서는 CLI 날짜 인자가 무시됨 — research.md 참조).
    """
    if not HAS_REAL_DATA:
        pytest.skip("No year-split parquet found in data/ directory")

    from service.pipeline.model_portfolio import ModelPortfolioPipeline
    from config import PARAM

    pipeline = ModelPortfolioPipeline(
        config=PARAM,
        factor_info_path=FACTOR_INFO_PATH,
        is_test=False,
    )

    t0 = time.time()
    pipeline.run(start_date=None, end_date=None)
    elapsed = time.time() - t0

    # 실제 사용된 날짜 범위는 로드된 데이터에서 도출
    start_date = pipeline.raw_data["ddt"].min().strftime("%Y-%m-%d")
    end_date = pipeline.raw_data["ddt"].max().strftime("%Y-%m-%d")

    return {
        "pipeline": pipeline,
        "start_date": start_date,
        "end_date": end_date,
        "elapsed": elapsed,
        "parquet_path": LATEST_PARQUET,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 1. 파이프라인 실행 기본 검증
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.real_data
class TestRealDataPipelineExecution:
    """실제 데이터로 파이프라인 실행 검증"""

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_pipeline_completes_without_error(self, pipeline_result) -> None:
        """파이프라인이 에러 없이 완료되는지 확인"""
        assert pipeline_result["elapsed"] > 0, "Pipeline should take some time"

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_pipeline_completes_within_timeout(self, pipeline_result) -> None:
        """파이프라인이 15분 이내에 완료되는지 확인"""
        assert pipeline_result["elapsed"] < 900, (
            f"Pipeline took {pipeline_result['elapsed']:.1f}s (> 15 min limit)"
        )

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_pipeline_stores_intermediate_results(self, pipeline_result) -> None:
        """파이프라인 중간 결과가 인스턴스에 저장되는지 확인"""
        p = pipeline_result["pipeline"]
        assert p.raw_data is not None, "raw_data should be stored"
        assert p.factor_metadata is not None, "factor_metadata should be stored"
        assert p.return_matrix is not None, "return_matrix should be stored"
        assert p.meta is not None, "meta should be stored"
        assert p.weights is not None, "weights should be stored"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. 출력 파일 검증
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.real_data
class TestRealDataOutputFiles:
    """실제 데이터 출력 파일 존재 및 구조 검증"""

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_weights_factor_file_exists(self, pipeline_result) -> None:
        """weights_factor CSV가 생성되는지 확인"""
        end_date = pipeline_result["end_date"]
        pattern = f"weights_factor_{end_date}_test.csv"
        files = list(OUTPUT_DIR.glob(pattern))
        assert len(files) >= 1, f"Expected {pattern} in output/"

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_weights_style_file_exists(self, pipeline_result) -> None:
        """weights_style CSV가 생성되는지 확인"""
        end_date = pipeline_result["end_date"]
        pattern = f"weights_style_{end_date}_mp_test.csv"
        files = list(OUTPUT_DIR.glob(pattern))
        assert len(files) >= 1, f"Expected {pattern} in output/"

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_pivoted_weights_file_exists(self, pipeline_result) -> None:
        """weights_pivot CSV가 생성되는지 확인"""
        end_date = pipeline_result["end_date"]
        pattern = f"weights_pivot_{end_date}.csv"
        files = list(OUTPUT_DIR.glob(pattern))
        assert len(files) >= 1, f"Expected {pattern} in output/"

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_meta_data_file_exists(self, pipeline_result) -> None:
        """meta_data.csv가 생성되는지 확인"""
        files = list(OUTPUT_DIR.glob("meta_data.csv"))
        assert len(files) >= 1, "Expected meta_data.csv in output/"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. 메타데이터 품질 검증
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.real_data
class TestRealDataMetaDataQuality:
    """meta_data.csv 결과 품질 검증"""

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_meta_has_required_columns(self, pipeline_result) -> None:
        """meta_data에 필수 컬럼이 있는지 확인"""
        meta = pipeline_result["pipeline"].meta
        required = ["factorAbbreviation", "factorName", "styleName", "cagr", "rank_score"]
        for col in required:
            assert col in meta.columns, f"Missing column: {col}"

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_meta_has_multiple_styles(self, pipeline_result) -> None:
        """meta_data에 다수의 스타일이 있는지 확인 (최소 3개 이상)"""
        meta = pipeline_result["pipeline"].meta
        n_styles = meta["styleName"].nunique()
        assert n_styles >= 3, f"Expected >= 3 styles, got {n_styles}"

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_meta_top50_selected(self, pipeline_result) -> None:
        """상위 50개 팩터가 선정되는지 확인"""
        meta = pipeline_result["pipeline"].meta
        assert len(meta) <= 50, f"Meta should have <= 50 factors, got {len(meta)}"
        assert len(meta) >= 10, f"Meta should have >= 10 factors, got {len(meta)}"

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_meta_cagr_is_positive_for_top_factors(self, pipeline_result) -> None:
        """상위 팩터들의 CAGR이 양수인지 확인

        선정은 rank_score(기본 t-stat) 내림차순 — 상위 t-stat 팩터는 평균
        수익이 양수이므로 CAGR 도 양수여야 정상이다.
        """
        meta = pipeline_result["pipeline"].meta
        top_10 = meta.head(10)
        assert (top_10["cagr"] > 0).all(), (
            f"Top 10 factors should have positive CAGR:\n{top_10[['factorAbbreviation', 'cagr']]}"
        )

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_meta_no_duplicate_factors(self, pipeline_result) -> None:
        """meta_data에 중복 팩터가 없는지 확인"""
        meta = pipeline_result["pipeline"].meta
        n_unique = meta["factorAbbreviation"].nunique()
        assert n_unique == len(meta), "Duplicate factor abbreviations found in meta"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. 수익률 행렬 검증
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.real_data
class TestRealDataReturnMatrix:
    """return_matrix 품질 검증"""

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_return_matrix_no_inf(self, pipeline_result) -> None:
        """수익률 행렬에 무한대 값이 없는지 확인"""
        ret = pipeline_result["pipeline"].return_matrix
        assert not np.any(np.isinf(ret.values)), "Infinite values found in return matrix"

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_return_matrix_no_nan(self, pipeline_result) -> None:
        """수익률 행렬에 NaN이 없는지 확인"""
        ret = pipeline_result["pipeline"].return_matrix
        assert not ret.isna().any().any(), "NaN values found in return matrix"

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_return_matrix_reasonable_range(self, pipeline_result) -> None:
        """월간 수익률이 합리적인 범위인지 확인 (±50% 이내)"""
        ret = pipeline_result["pipeline"].return_matrix
        extreme = np.abs(ret.values) > 0.5
        pct_extreme = extreme.sum() / ret.size * 100
        assert pct_extreme < 1, (
            f"{pct_extreme:.2f}% of returns exceed ±50% - should be < 1%"
        )

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_return_matrix_sufficient_history(self, pipeline_result) -> None:
        """수익률 행렬이 충분한 기간을 커버하는지 확인 (최소 36개월)"""
        ret = pipeline_result["pipeline"].return_matrix
        n_months = len(ret)
        assert n_months >= 36, f"Expected >= 36 months, got {n_months}"

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_return_matrix_first_row_is_zero(self, pipeline_result) -> None:
        """수익률 행렬 첫 행이 0인지 확인 (prepend_start_zero 적용)"""
        ret = pipeline_result["pipeline"].return_matrix
        assert (ret.iloc[0] == 0).all(), "First row should be all zeros"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. 가중치 품질 검증
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.real_data
class TestRealDataWeightQuality:
    """optimize_constrained_weights 결과 검증"""

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_weights_have_required_columns(self, pipeline_result) -> None:
        """가중치 결과에 필수 컬럼이 있는지 확인"""
        w = pipeline_result["pipeline"].weights
        required = ["factor", "fitted_weight", "styleName"]
        for col in required:
            assert col in w.columns, f"Missing column: {col}"

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_weights_no_nan(self, pipeline_result) -> None:
        """가중치에 NaN이 없는지 확인"""
        w = pipeline_result["pipeline"].weights
        assert w["fitted_weight"].notna().all(), "NaN found in fitted_weight"

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_weights_are_positive(self, pipeline_result) -> None:
        """fitted_weight가 양수인지 확인"""
        w = pipeline_result["pipeline"].weights
        assert (w["fitted_weight"] >= 0).all(), "Negative fitted_weight found"

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_weights_sum_close_to_one(self, pipeline_result) -> None:
        """가중치 합이 1에 가까운지 확인 (±5% 허용)"""
        w = pipeline_result["pipeline"].weights
        total = w["fitted_weight"].sum()
        assert abs(total - 1.0) < 0.05, f"Weight sum {total:.4f} deviates from 1.0"

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_style_cap_constraint_respected(self, pipeline_result) -> None:
        """각 스타일 가중치가 약 25% 이하인지 확인

        Note: hardcoded 모드에서는 사전 결정된 가중치를 사용하므로
        25%를 약간 초과할 수 있다 (허용 오차 2%).
        """
        w = pipeline_result["pipeline"].weights
        style_weights = w.groupby("styleName")["fitted_weight"].sum()
        violations = style_weights[style_weights > 0.25 + 0.02]
        assert len(violations) == 0, (
            f"Style cap violations (> 27%): {violations.to_dict()}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 7. 출력 CSV 데이터 품질 검증
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.real_data
class TestRealDataOutputCSVQuality:
    """출력 CSV 파일의 데이터 품질 검증"""

    @staticmethod
    def _load_total_weights(end_date: str) -> pd.DataFrame | None:
        files = list(OUTPUT_DIR.glob(f"weights_factor_{end_date}_test.csv"))
        return pd.read_csv(files[0]) if files else None

    @staticmethod
    def _load_style_weights(end_date: str) -> pd.DataFrame | None:
        files = list(OUTPUT_DIR.glob(f"weights_style_{end_date}_mp_test.csv"))
        return pd.read_csv(files[0]) if files else None

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_total_weights_no_inf(self, pipeline_result) -> None:
        """weights_factor에 무한대 값이 없는지 확인"""
        df = self._load_total_weights(pipeline_result["end_date"])
        if df is None:
            pytest.skip("weights_factor file not found")

        for col in ["mp_ls_weight", "ls_weight", "factor_weight"]:
            if col in df.columns:
                assert not np.isinf(df[col]).any(), f"Infinite values in {col}"

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_total_weights_ticker_format(self, pipeline_result) -> None:
        """ticker 형식이 '000000 CH Equity' 패턴인지 확인"""
        df = self._load_total_weights(pipeline_result["end_date"])
        if df is None:
            pytest.skip("weights_factor file not found")

        # 모든 ticker가 "CH Equity"로 끝나야 함
        ch_equity_mask = df["ticker"].str.endswith("CH Equity")
        assert ch_equity_mask.all(), (
            f"{(~ch_equity_mask).sum()} tickers don't end with 'CH Equity'"
        )

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_mp_rows_exist(self, pipeline_result) -> None:
        """MP (집계) 행이 존재하는지 확인"""
        df = self._load_total_weights(pipeline_result["end_date"])
        if df is None:
            pytest.skip("weights_factor file not found")

        mp_rows = df[df["style"] == "MP"]
        assert len(mp_rows) > 0, "No MP rows found in output"

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_multiple_styles_in_output(self, pipeline_result) -> None:
        """출력에 다수의 스타일이 포함되는지 확인"""
        df = self._load_total_weights(pipeline_result["end_date"])
        if df is None:
            pytest.skip("weights_factor file not found")

        styles = df["style"].unique()
        non_mp_styles = [s for s in styles if s != "MP"]
        assert len(non_mp_styles) >= 2, f"Expected >= 2 styles, got {non_mp_styles}"

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_style_ls_weight_column_exists(self, pipeline_result) -> None:
        """style_ls_weight 컬럼이 존재하는지 확인"""
        df = self._load_total_weights(pipeline_result["end_date"])
        if df is None:
            pytest.skip("weights_factor file not found")

        assert "style_ls_weight" in df.columns, "style_ls_weight column missing"

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_style_ls_weight_no_nan(self, pipeline_result) -> None:
        """style_ls_weight에 NaN이 없는지 확인"""
        df = self._load_total_weights(pipeline_result["end_date"])
        if df is None:
            pytest.skip("weights_factor file not found")

        assert df["style_ls_weight"].notna().all(), "NaN found in style_ls_weight"

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_mp_style_ls_weight_equals_mp_ls_weight(self, pipeline_result) -> None:
        """MP 행의 style_ls_weight가 mp_ls_weight와 동일한지 확인"""
        df = self._load_total_weights(pipeline_result["end_date"])
        if df is None:
            pytest.skip("weights_factor file not found")

        mp_df = df[df["style"] == "MP"]
        if mp_df.empty:
            pytest.skip("No MP rows found")

        np.testing.assert_allclose(
            mp_df["style_ls_weight"].values,
            mp_df["mp_ls_weight"].values,
            rtol=1e-10,
            err_msg="MP: style_ls_weight should equal mp_ls_weight",
        )

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_long_short_balance(self, pipeline_result) -> None:
        """MP 행의 롱/숏 비중이 합리적인지 확인"""
        df = self._load_total_weights(pipeline_result["end_date"])
        if df is None:
            pytest.skip("weights_factor file not found")

        mp_df = df[df["style"] == "MP"]
        if mp_df.empty:
            pytest.skip("No MP rows found")

        long_weight = mp_df.loc[mp_df["mp_ls_weight"] > 0, "mp_ls_weight"].sum()
        short_weight = mp_df.loc[mp_df["mp_ls_weight"] < 0, "mp_ls_weight"].sum()

        # 롱/숏 포트폴리오이므로 양쪽 모두 0이 아니어야 함
        assert long_weight > 0, "No long positions in MP"
        assert short_weight < 0, "No short positions in MP"


# ═══════════════════════════════════════════════════════════════════════════════
# 8. 입력 데이터 품질 검증 (parquet)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.real_data
class TestRealDataInputQuality:
    """실제 parquet 입력 데이터 품질 검증 (연도분할 레이아웃)"""

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_factor_parquet_has_required_columns(self) -> None:
        """연도 parquet 의 pipeline-ready 스키마 확인 (데이터 로드 없이 schema 만)"""
        import pyarrow.parquet as pq
        names = pq.read_schema(LATEST_PARQUET).names
        required = ["gvkeyiid", "ticker", "isin", "ddt", "sec", "val",
                    "factorAbbreviation", "factorOrder"]
        for col in required:
            assert col in names, f"Missing column in {LATEST_PARQUET.name}: {col}"

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_mreturn_parquet_valid(self) -> None:
        """M_RETURN parquet 스키마 + 전 기간 날짜 커버리지 (최소 24개월)"""
        df = pd.read_parquet(MRETURN_PARQUET)
        for col in ["gvkeyiid", "ddt", "M_RETURN"]:
            assert col in df.columns, f"Missing column in mreturn: {col}"
        n_dates = df["ddt"].nunique()
        assert n_dates >= 24, f"Expected >= 24 unique dates, got {n_dates}"

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_factor_parquet_has_sufficient_factors(self) -> None:
        """최신 연도 parquet 에 충분한 수의 팩터가 있는지 확인 (최소 100개)"""
        df = pd.read_parquet(LATEST_PARQUET, columns=["factorAbbreviation"])
        n_factors = df["factorAbbreviation"].nunique()
        assert n_factors >= 100, f"Expected >= 100 factors, got {n_factors}"

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_year_split_coverage(self) -> None:
        """연도분할 파일이 다년 커버리지를 가지는지 확인 (최소 2개 연도)"""
        assert len(FACTOR_PARQUET_FILES) >= 2, (
            f"Expected >= 2 year files, got {[p.name for p in FACTOR_PARQUET_FILES]}"
        )

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_factor_info_csv_exists_and_valid(self) -> None:
        """factor_info.csv가 존재하고 유효한지 확인"""
        assert FACTOR_INFO_PATH.exists(), "factor_info.csv not found"
        fi = pd.read_csv(FACTOR_INFO_PATH)
        required = ["factorAbbreviation", "factorName", "styleName", "factorOrder"]
        for col in required:
            assert col in fi.columns, f"factor_info.csv missing column: {col}"
        assert len(fi) >= 100, f"factor_info.csv has only {len(fi)} factors"


# ═══════════════════════════════════════════════════════════════════════════════
# 9. 성능 벤치마크
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.real_data
class TestRealDataPerformanceBenchmark:
    """파이프라인 성능 벤치마크 (정보 출력 목적)"""

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="No year-split parquet in data/")
    def test_log_performance_summary(self, pipeline_result) -> None:
        """성능 요약을 로그에 출력 (항상 pass)"""
        p = pipeline_result["pipeline"]
        elapsed = pipeline_result["elapsed"]
        parquet = pipeline_result["parquet_path"]

        summary = (
            f"\n{'='*60}\n"
            f"  Real Data Pipeline Performance Summary\n"
            f"{'='*60}\n"
            f"  Parquet: {parquet.name}\n"
            f"  Period: {pipeline_result['start_date']} ~ {pipeline_result['end_date']}\n"
            f"  Elapsed: {elapsed:.1f}s ({elapsed/60:.1f}min)\n"
            f"  Raw data rows: {len(p.raw_data):,}\n"
            f"  Factors analyzed: {len(p.factor_stats)}\n"
            f"  Return matrix shape: {p.return_matrix.shape}\n"
            f"  Selected factors (top50): {len(p.meta)}\n"
            f"  Final weight factors: {len(p.weights)}\n"
            f"{'='*60}"
        )
        print(summary)
        # 항상 pass - 정보 출력 목적
        assert True


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
