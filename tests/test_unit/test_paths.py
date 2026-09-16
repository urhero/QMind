# -*- coding: utf-8 -*-
"""산출물 기준일 폴더/파일명 헬퍼 (2026-08-19, 2026-09-09 기준일 폴더로 확장)."""
import pandas as pd

from service.paths import dated, dated_dir, latest


def _touch(tmp_path, d, name):
    (tmp_path / d).mkdir(exist_ok=True)
    (tmp_path / d / name).write_text("x", encoding="utf-8")


def test_dated_puts_file_in_date_folder(tmp_path):
    p = tmp_path / "walk_forward_results.csv"
    out = dated(p, "2026-06-30")
    assert out == tmp_path / "2026-06-30" / "walk_forward_results_2026-06-30.csv"
    assert out.parent.is_dir()  # 쓰는 쪽이 to_csv 직행이므로 폴더는 여기서 만든다
    assert dated(p, pd.Timestamp("2026-06-30")) == out


def test_dated_dir_creates_folder(tmp_path):
    assert dated_dir(tmp_path, pd.Timestamp("2026-06-30")) == tmp_path / "2026-06-30"
    assert (tmp_path / "2026-06-30").is_dir()


def test_dated_none_keeps_original(tmp_path):
    p = tmp_path / "meta_data.csv"
    assert dated(p, None) == p


def test_latest_picks_newest_date(tmp_path):
    for d in ("2026-04-30", "2026-06-30", "2026-05-31"):
        _touch(tmp_path, d, f"meta_data_{d}.csv")
    assert latest(tmp_path / "meta_data.csv") == tmp_path / "2026-06-30" / "meta_data_2026-06-30.csv"


def test_latest_ignores_non_date_siblings(tmp_path):
    """test/ 폴더의 meta_data_test_test_data.csv 나 루트의 무날짜 파일은 후보에서 제외."""
    _touch(tmp_path, "2026-06-30", "meta_data_2026-06-30.csv")
    _touch(tmp_path, "test", "meta_data_test_test_data.csv")
    (tmp_path / "meta_data_2026-07-31.csv").write_text("x", encoding="utf-8")
    assert latest(tmp_path / "meta_data.csv").name == "meta_data_2026-06-30.csv"


def test_latest_falls_back_to_undated(tmp_path):
    """날짜 파일이 없으면 구 무날짜 경로 반환 (기존 산출물 호환)."""
    p = tmp_path / "walk_forward_results.csv"
    assert latest(p) == p


def test_latest_as_of_prefers_that_date(tmp_path):
    """as_of 를 주면 그 기준일 이하 중 최신본 — 과거 시점 재현 시 최신본 혼입 방지."""
    for d in ("2025-06-30", "2026-06-30"):
        _touch(tmp_path, d, f"walk_forward_results_{d}.csv")
    p = tmp_path / "walk_forward_results.csv"
    assert latest(p, "2025-06-30").name == "walk_forward_results_2025-06-30.csv"
    assert latest(p, "2026-06-30").name == "walk_forward_results_2026-06-30.csv"
    assert latest(p).name == "walk_forward_results_2026-06-30.csv"


def test_latest_as_of_earlier_than_all_falls_back(tmp_path):
    _touch(tmp_path, "2026-06-30", "meta_data_2026-06-30.csv")
    assert latest(tmp_path / "meta_data.csv", "2020-01-01").name == "meta_data_2026-06-30.csv"


def test_dirs_are_per_benchmark(monkeypatch):
    """MXCN1A 도 예외 없이 output/MXCN1A/, data/MXCN1A/ (2026-09-02 / 2026-09-09 유니버스 폴더 통일)."""
    import importlib
    import config
    import service.paths as sp
    monkeypatch.setenv("BENCHMARK", "MXCN1A")
    try:
        importlib.reload(config)
        importlib.reload(sp)
        assert sp.OUTPUT_DIR.name == "MXCN1A" and sp.OUTPUT_DIR.parent.name == "output"
        assert sp.HISTORY_DIR == sp.OUTPUT_DIR / "mp_weight_history"
        assert sp.TEST_OUTPUT_DIR == sp.OUTPUT_DIR / "test"
        assert sp.UNIVERSE_DATA_DIR == sp.DATA_DIR / "MXCN1A" == sp.universe_data_dir("MXCN1A")
    finally:
        monkeypatch.undo()
        importlib.reload(config)
        importlib.reload(sp)
