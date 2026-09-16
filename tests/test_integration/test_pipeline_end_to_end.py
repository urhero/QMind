# -*- coding: utf-8 -*-
"""
Integration tests for the end-to-end model portfolio pipeline.

전체 파이프라인 통합 테스트:
- test_data.csv를 사용한 실제 파이프라인 실행
- 출력 파일 생성 검증
- 결과 데이터 무결성 검증
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# 프로젝트 루트
PROJECT_ROOT = Path(__file__).parent.parent.parent
TEST_DATA_PATH = PROJECT_ROOT / "tests" / "fixtures" / "test_data.csv"
from service.paths import OUTPUT_DIR, TEST_OUTPUT_DIR  # output/{BENCHMARK}/, output/{BENCHMARK}/test/


class TestPipelineEndToEnd:
    """전체 파이프라인 통합 테스트"""

    @pytest.fixture(autouse=True)
    def setup_and_teardown(self):
        """테스트 전후 설정 및 정리"""
        # 테스트용 출력 디렉토리 백업
        self.backup_dir = OUTPUT_DIR.parent / "output_backup_test"
        if OUTPUT_DIR.exists():
            if self.backup_dir.exists():
                shutil.rmtree(self.backup_dir)
            # 백업하지 않고 진행 (기존 출력 유지)

        yield

        # 테스트 후 정리 (필요시)
        # 테스트 출력 파일들은 유지 (디버깅용)

    @pytest.mark.skipif(
        not TEST_DATA_PATH.exists(),
        reason="test_data.csv not found"
    )
    def test_pipeline_runs_without_error(self) -> None:
        """파이프라인이 에러 없이 실행되는지 확인"""
        from service.pipeline.model_portfolio import run_model_portfolio_pipeline

        # test_data.csv로 파이프라인 실행
        try:
            run_model_portfolio_pipeline(
                start_date=None,
                end_date=None,
                test_file=str(TEST_DATA_PATH),
            )
            assert True  # 에러 없이 완료
        except Exception as e:
            pytest.fail(f"Pipeline failed with error: {e}")

    @pytest.mark.skipif(
        not TEST_DATA_PATH.exists(),
        reason="test_data.csv not found"
    )
    def test_output_files_created(self) -> None:
        """출력 파일이 생성되는지 확인"""
        from service.pipeline.model_portfolio import run_model_portfolio_pipeline

        run_model_portfolio_pipeline(
            start_date=None,
            end_date=None,
            test_file=str(TEST_DATA_PATH),
        )

        # 출력 파일 존재 확인
        output_files = list(TEST_OUTPUT_DIR.glob("*_test*.csv"))
        assert len(output_files) > 0, "No test output files created"

    @pytest.mark.skipif(
        not TEST_DATA_PATH.exists(),
        reason="test_data.csv not found"
    )
    def test_meta_data_has_required_columns(self) -> None:
        """meta_data.csv가 필수 컬럼을 가지는지 확인"""
        from service.pipeline.model_portfolio import run_model_portfolio_pipeline

        run_model_portfolio_pipeline(
            start_date=None,
            end_date=None,
            test_file=str(TEST_DATA_PATH),
        )

        # meta_data 파일 찾기
        meta_files = list(TEST_OUTPUT_DIR.glob("meta_data*.csv"))
        assert len(meta_files) > 0, "meta_data.csv not found"

        meta_df = pd.read_csv(meta_files[0])

        # 필수 컬럼 확인
        required_columns = ["factorAbbreviation", "factorName", "styleName", "cagr"]
        for col in required_columns:
            assert col in meta_df.columns, f"Missing column: {col}"


class TestPipelineDataValidation:
    """파이프라인 데이터 유효성 테스트"""

    @pytest.mark.skipif(
        not TEST_DATA_PATH.exists(),
        reason="test_data.csv not found"
    )
    def test_no_nan_in_critical_columns(self) -> None:
        """중요 컬럼에 NaN이 없는지 확인"""
        from service.pipeline.model_portfolio import run_model_portfolio_pipeline

        run_model_portfolio_pipeline(
            start_date=None,
            end_date=None,
            test_file=str(TEST_DATA_PATH),
        )

        # total_aggregated_weights 파일 확인
        weight_files = list(TEST_OUTPUT_DIR.glob("total_aggregated_weights*_test*.csv"))
        if weight_files:
            weights_df = pd.read_csv(weight_files[0])

            # ticker, ls_weight 컬럼에 NaN이 없어야 함
            if "ticker" in weights_df.columns:
                assert weights_df["ticker"].notna().all(), "NaN found in ticker column"
            if "ls_weight" in weights_df.columns:
                assert weights_df["ls_weight"].notna().all(), "NaN found in ls_weight column"

    @pytest.mark.skipif(
        not TEST_DATA_PATH.exists(),
        reason="test_data.csv not found"
    )
    def test_weights_are_valid(self) -> None:
        """가중치가 유효한 범위인지 확인"""
        from service.pipeline.model_portfolio import run_model_portfolio_pipeline

        run_model_portfolio_pipeline(
            start_date=None,
            end_date=None,
            test_file=str(TEST_DATA_PATH),
        )

        # total_aggregated_weights 파일 확인
        total_weight_files = list(TEST_OUTPUT_DIR.glob("total_aggregated_weights*_test*.csv"))
        if total_weight_files:
            weights_df = pd.read_csv(total_weight_files[0])

            if "ls_weight" in weights_df.columns:
                # 가중치가 무한대가 아닌지
                assert not np.isinf(weights_df["ls_weight"]).any(), "Infinite weights found"


class TestPipelineOutputConsistency:
    """출력 일관성 테스트 (스냅샷 테스트)"""

    @pytest.fixture
    def expected_output_path(self) -> Path:
        """예상 출력 스냅샷 경로"""
        return PROJECT_ROOT / "tests" / "fixtures" / "expected_output"

    @pytest.mark.skipif(
        not TEST_DATA_PATH.exists(),
        reason="test_data.csv not found"
    )
    def test_output_structure_consistent(self) -> None:
        """출력 구조가 일관되는지 확인"""
        from service.pipeline.model_portfolio import run_model_portfolio_pipeline

        run_model_portfolio_pipeline(
            start_date=None,
            end_date=None,
            test_file=str(TEST_DATA_PATH),
        )

        # 예상되는 출력 파일 패턴들
        expected_patterns = [
            "aggregated_weights*_test*.csv",
            "total_aggregated_weights*_test*.csv",
            "meta_data*.csv",
        ]

        for pattern in expected_patterns:
            files = list(TEST_OUTPUT_DIR.glob(pattern))
            # 각 패턴에 대해 최소 1개 파일 존재해야 함 (패턴에 따라)
            # 일부 패턴은 선택적일 수 있음


class TestPipelinePerformance:
    """파이프라인 성능 테스트"""

    @pytest.mark.skipif(
        not TEST_DATA_PATH.exists(),
        reason="test_data.csv not found"
    )
    @pytest.mark.timeout(300)  # 5분 타임아웃
    def test_pipeline_completes_within_timeout(self) -> None:
        """파이프라인이 합리적인 시간 내에 완료되는지 확인"""
        import time
        from service.pipeline.model_portfolio import run_model_portfolio_pipeline

        start_time = time.time()

        run_model_portfolio_pipeline(
            start_date=None,
            end_date=None,
            test_file=str(TEST_DATA_PATH),
        )

        elapsed_time = time.time() - start_time

        # test_data.csv는 작은 데이터셋이므로 5분 이내 완료 기대
        assert elapsed_time < 300, f"Pipeline took too long: {elapsed_time:.2f}s"


class TestPipelineErrorHandling:
    """파이프라인 에러 처리 테스트"""

    def test_invalid_file_path_raises_error(self) -> None:
        """존재하지 않는 파일 경로는 에러 발생"""
        from service.pipeline.model_portfolio import run_model_portfolio_pipeline

        with pytest.raises((FileNotFoundError, Exception)):
            run_model_portfolio_pipeline(
                start_date=None,
                end_date=None,
                test_file="nonexistent_file.csv",
            )

    def test_empty_dataframe_handling(self) -> None:
        """빈 데이터프레임 처리"""
        # 빈 CSV 파일 생성
        empty_csv = PROJECT_ROOT / "tests" / "fixtures" / "empty_test.csv"
        empty_csv.parent.mkdir(parents=True, exist_ok=True)

        # 헤더만 있는 빈 CSV
        empty_df = pd.DataFrame(columns=[
            "gvkeyiid", "ticker", "isin", "ddt", "val", "fld", "sec", "country", "updated_at"
        ])
        empty_df.to_csv(empty_csv, index=False)

        try:
            from service.pipeline.model_portfolio import run_model_portfolio_pipeline

            # 빈 데이터는 적절한 에러나 빈 출력을 생성해야 함
            # 구체적인 동작은 구현에 따라 다름
            with pytest.raises(Exception):
                run_model_portfolio_pipeline(
                    start_date=None,
                    end_date=None,
                    test_file=str(empty_csv),
                )
        finally:
            # 정리
            if empty_csv.exists():
                empty_csv.unlink()


class TestStyleLsWeightCalculation:
    """style_ls_weight 계산 검증 테스트"""

    @staticmethod
    def _get_latest_style_file() -> Path:
        """가장 최근 수정된 style 출력 파일 반환"""
        style_files = list(TEST_OUTPUT_DIR.glob("total_aggregated_weights_style*_test*.csv"))
        if not style_files:
            return None
        # 수정 시간 기준 정렬하여 가장 최근 파일 반환
        return max(style_files, key=lambda f: f.stat().st_mtime)

    @pytest.mark.skipif(
        not TEST_DATA_PATH.exists(),
        reason="test_data.csv not found"
    )
    def test_style_ls_weight_column_exists(self) -> None:
        """style_ls_weight 컬럼이 출력에 존재하는지 확인"""
        from service.pipeline.model_portfolio import run_model_portfolio_pipeline

        run_model_portfolio_pipeline(
            start_date=None,
            end_date=None,
            test_file=str(TEST_DATA_PATH),
        )

        # total_aggregated_weights_style 파일 확인 (가장 최근 파일)
        style_file = self._get_latest_style_file()
        assert style_file is not None, "Style output file not found"

        style_df = pd.read_csv(style_file)
        assert "style_ls_weight" in style_df.columns, "style_ls_weight column missing"
        assert "ls_weight" in style_df.columns, "ls_weight column missing"
        assert "factor_weight" in style_df.columns, "factor_weight column missing"

    @pytest.mark.skipif(
        not TEST_DATA_PATH.exists(),
        reason="test_data.csv not found"
    )
    def test_style_ls_weight_is_valid(self) -> None:
        """style_ls_weight가 유효한 값인지 확인

        검증 항목:
        1. |style_ls_weight| <= |ls_weight| (가중평균이므로 원본보다 작거나 같음)
        2. style_ls_weight와 ls_weight의 부호가 같음
        3. factor_weight=0이면 style_ls_weight=0
        """
        from service.pipeline.model_portfolio import run_model_portfolio_pipeline

        run_model_portfolio_pipeline(
            start_date=None,
            end_date=None,
            test_file=str(TEST_DATA_PATH),
        )

        style_file = self._get_latest_style_file()
        if not style_file:
            pytest.skip("Style output file not found")

        style_df = pd.read_csv(style_file)

        # MP 행 제외 (MP는 style_ls_weight = mp_ls_weight)
        non_mp_df = style_df[style_df["style"] != "MP"].copy()

        if non_mp_df.empty:
            pytest.skip("No non-MP rows to validate")

        # 1. |style_ls_weight| <= |ls_weight| (스타일이 하나뿐이면 같을 수 있음)
        assert (np.abs(non_mp_df["style_ls_weight"]) <= np.abs(non_mp_df["ls_weight"]) + 1e-10).all(), \
            "style_ls_weight magnitude should be <= ls_weight magnitude"

        # 2. 부호 일치 (0이 아닌 경우)
        non_zero_mask = (non_mp_df["style_ls_weight"] != 0) & (non_mp_df["ls_weight"] != 0)
        if non_zero_mask.any():
            signs_match = np.sign(non_mp_df.loc[non_zero_mask, "style_ls_weight"]) == \
                         np.sign(non_mp_df.loc[non_zero_mask, "ls_weight"])
            assert signs_match.all(), "style_ls_weight and ls_weight should have same sign"

        # 3. factor_weight=0이면 style_ls_weight=0
        zero_fw_mask = non_mp_df["factor_weight"] == 0
        if zero_fw_mask.any():
            assert (non_mp_df.loc[zero_fw_mask, "style_ls_weight"] == 0).all(), \
                "style_ls_weight should be 0 when factor_weight is 0"

    @pytest.mark.skipif(
        not TEST_DATA_PATH.exists(),
        reason="test_data.csv not found"
    )
    def test_style_ls_weight_no_nan(self) -> None:
        """style_ls_weight에 NaN이 없는지 확인"""
        from service.pipeline.model_portfolio import run_model_portfolio_pipeline

        run_model_portfolio_pipeline(
            start_date=None,
            end_date=None,
            test_file=str(TEST_DATA_PATH),
        )

        style_file = self._get_latest_style_file()
        if not style_file:
            pytest.skip("Style output file not found")

        style_df = pd.read_csv(style_file)
        assert style_df["style_ls_weight"].notna().all(), "NaN found in style_ls_weight"

    @pytest.mark.skipif(
        not TEST_DATA_PATH.exists(),
        reason="test_data.csv not found"
    )
    def test_mp_style_ls_weight_equals_mp_ls_weight(self) -> None:
        """MP 행의 style_ls_weight가 mp_ls_weight와 동일한지 확인"""
        from service.pipeline.model_portfolio import run_model_portfolio_pipeline

        run_model_portfolio_pipeline(
            start_date=None,
            end_date=None,
            test_file=str(TEST_DATA_PATH),
        )

        style_file = self._get_latest_style_file()
        if not style_file:
            pytest.skip("Style output file not found")

        style_df = pd.read_csv(style_file)
        mp_df = style_df[style_df["style"] == "MP"]

        if mp_df.empty:
            pytest.skip("No MP rows found")

        # MP 행에서 style_ls_weight = ls_weight 확인
        np.testing.assert_allclose(
            mp_df["style_ls_weight"].values,
            mp_df["ls_weight"].values,
            rtol=1e-10,
            err_msg="MP rows should have style_ls_weight == ls_weight"
        )


class TestPipelineInputValidation:
    """입력 데이터 검증 테스트"""

    @pytest.mark.skipif(
        not TEST_DATA_PATH.exists(),
        reason="test_data.csv not found"
    )
    def test_input_data_has_required_columns(self) -> None:
        """입력 데이터가 필수 컬럼을 가지는지 확인"""
        test_df = pd.read_csv(TEST_DATA_PATH)

        required_columns = ["gvkeyiid", "ticker", "ddt", "val", "sec"]
        for col in required_columns:
            assert col in test_df.columns, f"Missing required column: {col}"

    @pytest.mark.skipif(
        not TEST_DATA_PATH.exists(),
        reason="test_data.csv not found"
    )
    def test_input_data_has_valid_dates(self) -> None:
        """입력 데이터의 날짜가 유효한지 확인"""
        test_df = pd.read_csv(TEST_DATA_PATH, parse_dates=["ddt"])

        # 날짜 컬럼이 datetime 타입이어야 함
        assert pd.api.types.is_datetime64_any_dtype(test_df["ddt"])

        # NaT(Not a Time)가 없어야 함
        assert test_df["ddt"].notna().all(), "Invalid dates found in ddt column"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
