# -*- coding: utf-8 -*-
"""국가별 증권거래세 헬퍼 검증 (2026-08-12)."""
import pandas as pd
import pytest

from service.pipeline import transaction_tax as tt


@pytest.fixture
def patched_rates(monkeypatch):
    """UK(매수 50bp) / HK(양방 10bp) / JP(면세) 3종목 세율표를 주입한다."""
    rf = pd.DataFrame(
        {"buy": [50 / 1e4, 10 / 1e4, 0.0], "sell": [0.0, 10 / 1e4, 0.0]},
        index=["UK1", "HK1", "JP1"],
    )
    monkeypatch.setattr(tt, "_rate_frame", lambda benchmark: rf)
    import config
    monkeypatch.setitem(config.PIPELINE_PARAMS, "apply_country_tax", True)  # MXCN1A env 에서도 세율 경로 검증
    return rf


def test_uk_buy_charged_sell_free(patched_rates):
    """영국은 매수 편측 과세 — 같은 크기의 매도는 0."""
    assert tt.tax_cost(pd.Series({"UK1": 1.0})) == pytest.approx(0.005)
    assert tt.tax_cost(pd.Series({"UK1": -1.0})) == pytest.approx(0.0)


def test_hk_both_sides(patched_rates):
    """홍콩은 양방 과세 — 매수/매도 동일."""
    assert tt.tax_cost(pd.Series({"HK1": 1.0})) == pytest.approx(0.001)
    assert tt.tax_cost(pd.Series({"HK1": -1.0})) == pytest.approx(0.001)


def test_exempt_and_unmapped_are_zero(patched_rates):
    """면세국과 미매핑 종목은 0 (세율표 미등재 = 면세 취급)."""
    assert tt.tax_cost(pd.Series({"JP1": 1.0, "UNKNOWN": -5.0})) == pytest.approx(0.0)


def test_mixed_book_sums_by_direction(patched_rates):
    """혼합 북: 방향별로 각각 집계된다."""
    delta = pd.Series({"UK1": 0.5, "HK1": -0.2, "JP1": 3.0})
    assert tt.tax_cost(delta) == pytest.approx(0.5 * 0.005 + 0.2 * 0.001)


def test_empty_delta(patched_rates):
    assert tt.tax_cost(pd.Series(dtype=float)) == 0.0


def test_apply_country_tax_flag_disables(patched_rates, monkeypatch):
    """apply_country_tax=False (MXCN1A) 면 세율표가 있어도 0 — A주는 홍콩 인지세 대상이 아님."""
    import config
    monkeypatch.setitem(config.PIPELINE_PARAMS, "apply_country_tax", False)
    assert tt.tax_cost(pd.Series({"UK1": 1.0, "HK1": -1.0})) == 0.0
    monkeypatch.setitem(config.PIPELINE_PARAMS, "apply_country_tax", True)
    assert tt.tax_cost(pd.Series({"UK1": 1.0})) == pytest.approx(0.005)


def test_missing_country_map_degrades_to_zero(monkeypatch, tmp_path):
    """국가맵 파일이 없으면 크래시 대신 세금 0 (CI/미매핑 유니버스 대비)."""
    monkeypatch.setattr("service.paths.DATA_DIR", tmp_path)  # universe_data_dir 이 참조
    tt._rate_frame.cache_clear()
    try:
        assert tt.tax_cost(pd.Series({"X": 1.0}), benchmark="NO_SUCH_BM") == 0.0
    finally:
        tt._rate_frame.cache_clear()
