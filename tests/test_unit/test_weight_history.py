# -*- coding: utf-8 -*-
"""service/pipeline/weight_history.py 단위 테스트."""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from service.pipeline.weight_history import (
    _build_factor_style_df,
    load_prev_factor_weights,
    load_prev_selection,
    save_factor_styles,
    save_factor_weights,
    save_style_totals,
)


# ── load_prev_factor_weights ──────────────────────────────────────────────

def test_load_returns_none_when_dir_missing():
    """디렉토리가 없으면 (None, None) 반환 (첫 실행 시나리오)."""
    with tempfile.TemporaryDirectory() as tmp:
        history = Path(tmp) / "missing"
        assert load_prev_factor_weights(history, "2026-03-31") == (None, None)


def test_load_returns_none_when_dir_empty():
    """디렉토리가 비어 있으면 (None, None)."""
    with tempfile.TemporaryDirectory() as tmp:
        history = Path(tmp)
        assert load_prev_factor_weights(history, "2026-03-31") == (None, None)


def test_load_returns_most_recent_prev_only():
    """current_end_date 미만 중 가장 최근 파일을 로딩."""
    with tempfile.TemporaryDirectory() as tmp:
        history = Path(tmp)
        # 3 history 파일 생성
        save_factor_weights(history, "2025-12-31", {"A": 0.1, "B": 0.2})
        save_factor_weights(history, "2026-03-31", {"A": 0.15, "B": 0.25})
        # current=2026-06-30 -> 2026-03-31 이 최근 prev
        loaded, _ = load_prev_factor_weights(history, "2026-06-30")
        assert loaded == {"A": 0.15, "B": 0.25}


def test_load_excludes_current_and_future():
    """current_end_date 이상 파일은 제외."""
    with tempfile.TemporaryDirectory() as tmp:
        history = Path(tmp)
        save_factor_weights(history, "2025-12-31", {"A": 0.1})
        save_factor_weights(history, "2026-03-31", {"A": 0.15})  # current
        save_factor_weights(history, "2026-06-30", {"A": 0.2})   # future
        # current=2026-03-31 -> 2025-12-31 만 후보
        loaded, _ = load_prev_factor_weights(history, "2026-03-31")
        assert loaded == {"A": 0.1}


def test_load_returns_none_when_only_future_files():
    """current 이전 파일이 하나도 없으면 (None, None)."""
    with tempfile.TemporaryDirectory() as tmp:
        history = Path(tmp)
        save_factor_weights(history, "2026-06-30", {"A": 0.2})
        result = load_prev_factor_weights(history, "2026-03-31")
        assert result == (None, None)


def test_load_returns_weights_and_date():
    """load_prev_factor_weights 는 (weights, date) 튜플 반환."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        history = Path(tmp)
        save_factor_weights(history, "2026-01-31", {"A": 0.6, "B": 0.4})
        weights, prev_date = load_prev_factor_weights(history, "2026-02-28")
        assert weights == {"A": 0.6, "B": 0.4}
        assert prev_date == "2026-01-31"


def test_load_none_returns_none_tuple():
    """없으면 (None, None)."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        assert load_prev_factor_weights(Path(tmp) / "x", "2026-02-28") == (None, None)


# ── load_prev_selection (선정 히스테리시스용 직전 선정 집합) ───────────────

def test_load_prev_selection_missing_dir():
    """디렉토리가 없으면 (None, None) (첫 실행 시나리오)."""
    with tempfile.TemporaryDirectory() as tmp:
        assert load_prev_selection(Path(tmp) / "missing", "2026-03-31") == (None, None)


def test_load_prev_selection_returns_raw_positive_only():
    """raw_weight > 0 인 factor 만 선정 집합으로 반환 (청산 중 exit 제외)."""
    with tempfile.TemporaryDirectory() as tmp:
        history = Path(tmp)
        # B 는 raw=0 (이번 회차 탈락, 청산 중) -> 선정 집합에 포함되면 안 됨
        save_factor_styles(
            history, "2026-01-31",
            raw_weights={"A": 0.6, "C": 0.4},
            prev_weights={"A": 0.5, "B": 0.1, "C": 0.4},
            new_weights={"A": 0.55, "B": 0.05, "C": 0.4},
            style_map={"A": "S1", "B": "S1", "C": "S2"},
        )
        selected, prev_date = load_prev_selection(history, "2026-02-28")
        assert selected == {"A", "C"}
        assert prev_date == "2026-01-31"


def test_load_prev_selection_strict_cutoff():
    """current_end_date 이상 파일은 제외, 미만 중 최근 선택."""
    with tempfile.TemporaryDirectory() as tmp:
        history = Path(tmp)
        for d, raw in [("2025-12-31", {"A": 1.0}), ("2026-01-31", {"B": 1.0}),
                       ("2026-02-28", {"C": 1.0})]:
            save_factor_styles(history, d, raw_weights=raw, prev_weights=None,
                               new_weights=raw, style_map={})
        selected, prev_date = load_prev_selection(history, "2026-02-28")
        assert selected == {"B"}
        assert prev_date == "2026-01-31"


def test_load_prev_selection_empty_dir():
    with tempfile.TemporaryDirectory() as tmp:
        assert load_prev_selection(Path(tmp), "2026-03-31") == (None, None)


# ── save_factor_weights ───────────────────────────────────────────────────

def test_save_creates_directory():
    """디렉토리가 없으면 생성."""
    with tempfile.TemporaryDirectory() as tmp:
        history = Path(tmp) / "new" / "nested"
        save_factor_weights(history, "2026-03-31", {"A": 0.1})
        assert history.exists()


def test_save_csv_format():
    """저장된 CSV 가 factor / weight 컬럼 포함, 정렬됨."""
    with tempfile.TemporaryDirectory() as tmp:
        history = Path(tmp)
        save_factor_weights(history, "2026-03-31", {"B": 0.2, "A": 0.1})
        path = history / "factor_weights_2026-03-31.csv"
        df = pd.read_csv(path)
        assert list(df.columns) == ["factor", "weight"]
        assert list(df["factor"]) == ["A", "B"]  # 정렬 확인
        assert list(df["weight"]) == [0.1, 0.2]


def test_save_then_load_roundtrip():
    """save 후 load 가 동일 dict 반환."""
    with tempfile.TemporaryDirectory() as tmp:
        history = Path(tmp)
        original = {"FactorX": 0.05, "FactorY": -0.03, "FactorZ": 0.12}
        save_factor_weights(history, "2026-01-31", original)
        loaded, _ = load_prev_factor_weights(history, "2026-02-28")
        assert loaded == original


# ── _build_factor_style_df ────────────────────────────────────────────────

def test_build_df_basic():
    """raw + prev + new + style_map 모두 채워진 표준 케이스."""
    raw = {"A": 0.5, "B": 0.3, "C": 0.2}
    prev = {"A": 0.4, "B": 0.4, "C": 0.2}
    new = {"A": 0.41, "B": 0.39, "C": 0.20}
    style_map = {"A": "Value", "B": "Value", "C": "Momentum"}

    df = _build_factor_style_df(raw, prev, new, style_map)

    assert list(df.columns) == [
        "factor", "style", "raw_weight", "prev_weight", "new_weight", "weight_within_style",
    ]
    assert len(df) == 3
    # 정렬: (style asc, new_weight desc) -> "Momentum" < "Value" 알파벳 순
    assert list(df["style"]) == ["Momentum", "Value", "Value"]
    assert list(df["factor"]) == ["C", "A", "B"]  # Value 내에서 new_weight desc
    # weight_within_style: Momentum 합=0.20, Value 합=0.80
    assert abs(df.loc[df["factor"] == "C", "weight_within_style"].iloc[0] - 1.0) < 1e-9
    assert abs(df.loc[df["factor"] == "A", "weight_within_style"].iloc[0] - 0.41 / 0.80) < 1e-9


def test_build_df_prev_none():
    """prev=None 이면 prev_weight 전체 NaN, new == raw."""
    raw = {"A": 0.6, "B": 0.4}
    new = {"A": 0.6, "B": 0.4}
    style_map = {"A": "Value", "B": "Momentum"}

    df = _build_factor_style_df(raw, None, new, style_map)

    assert df["prev_weight"].isna().all()
    # 정렬: Momentum < Value 알파벳 순
    assert list(df["factor"]) == ["B", "A"]
    assert (df["raw_weight"] == df["new_weight"]).all()


def test_build_df_factor_union():
    """raw-only / prev-only / both 모두 행으로 출현."""
    raw = {"A": 0.7, "B": 0.3}                   # C 없음 (탈락)
    prev = {"A": 0.5, "C": 0.5}                  # B 없음 (신규)
    new = {"A": 0.52, "B": 0.03, "C": 0.45}      # 모두 출현
    style_map = {"A": "Value", "B": "Quality", "C": "Momentum"}

    df = _build_factor_style_df(raw, prev, new, style_map)

    assert set(df["factor"]) == {"A", "B", "C"}
    # B 는 prev 0, raw 0.3
    b_row = df[df["factor"] == "B"].iloc[0]
    assert b_row["raw_weight"] == 0.3
    assert b_row["prev_weight"] == 0.0
    # C 는 raw 0, prev 0.5
    c_row = df[df["factor"] == "C"].iloc[0]
    assert c_row["raw_weight"] == 0.0
    assert c_row["prev_weight"] == 0.5


def test_build_df_unmapped_style():
    """style_map 에 없는 factor 는 '(unmapped)' 로 표시."""
    raw = {"A": 0.6, "Unknown": 0.4}
    new = {"A": 0.6, "Unknown": 0.4}
    style_map = {"A": "Value"}  # Unknown 누락

    df = _build_factor_style_df(raw, None, new, style_map)

    unknown_row = df[df["factor"] == "Unknown"].iloc[0]
    assert unknown_row["style"] == "(unmapped)"


def test_build_df_within_style_zero_total():
    """스타일 내 new_weight 합이 0 이면 weight_within_style = 0 (분모 0 회피)."""
    raw = {"A": 0.0, "B": 0.0}
    prev = {"A": 0.5, "B": 0.5}
    new = {"A": 0.0, "B": 0.0}                   # 둘 다 0
    style_map = {"A": "Value", "B": "Value"}

    df = _build_factor_style_df(raw, prev, new, style_map)

    assert (df["weight_within_style"] == 0.0).all()


def test_build_df_no_deployed_keeps_legacy_columns():
    """factor_styles DataFrame 에는 deployed_weight 컬럼이 없어야 한다 (제거된 기능)."""
    raw = {"A": 0.6, "B": 0.4}
    new = {"A": 0.6, "B": 0.4}
    style_map = {"A": "Value", "B": "Momentum"}

    df = _build_factor_style_df(raw, None, new, style_map)

    assert "deployed_weight" not in df.columns


# ── save_factor_styles ────────────────────────────────────────────────────

def test_save_factor_styles_creates_file():
    """파일 생성 + 헤더 + 정렬 확인."""
    with tempfile.TemporaryDirectory() as tmp:
        history = Path(tmp)
        raw = {"A": 0.5, "B": 0.5}
        prev = {"A": 0.6, "B": 0.4}
        new = {"A": 0.51, "B": 0.49}
        style_map = {"A": "Value", "B": "Momentum"}

        out_path = save_factor_styles(history, "2026-04-30", raw, prev, new, style_map)

        assert out_path == history / "factor_styles_2026-04-30.csv"
        assert out_path.exists()

        df = pd.read_csv(out_path)
        assert list(df.columns) == [
            "factor", "style", "raw_weight", "prev_weight", "new_weight", "weight_within_style",
        ]
        # Momentum < Value 정렬
        assert list(df["style"]) == ["Momentum", "Value"]
        assert list(df["factor"]) == ["B", "A"]


def test_save_factor_styles_prev_none_writes_empty():
    """prev=None 인 경우 prev_weight 컬럼은 빈값 (read_csv 시 NaN)."""
    with tempfile.TemporaryDirectory() as tmp:
        history = Path(tmp)
        save_factor_styles(
            history, "2026-04-30",
            raw_weights={"A": 1.0}, prev_weights=None, new_weights={"A": 1.0},
            style_map={"A": "Value"},
        )
        df = pd.read_csv(history / "factor_styles_2026-04-30.csv")
        assert df["prev_weight"].isna().all()


# ── save_style_totals ─────────────────────────────────────────────────────

def test_save_style_totals_basic():
    """스타일 합계 + factor_count + factors 문자열 검증."""
    with tempfile.TemporaryDirectory() as tmp:
        history = Path(tmp)
        raw = {"A": 0.4, "B": 0.3, "C": 0.3}
        prev = {"A": 0.5, "B": 0.3, "C": 0.2}
        new = {"A": 0.41, "B": 0.30, "C": 0.29}
        style_map = {"A": "Value", "B": "Value", "C": "Momentum"}

        out_path = save_style_totals(history, "2026-04-30", raw, prev, new, style_map)

        df = pd.read_csv(out_path)
        assert list(df.columns) == [
            "style", "raw_weight", "prev_weight", "new_weight", "delta",
            "factor_count", "factors",
        ]
        # new_weight desc 정렬: Value(0.71) > Momentum(0.29)
        assert list(df["style"]) == ["Value", "Momentum"]
        # Value: A=0.41, B=0.30 -> "A;B"
        value_row = df[df["style"] == "Value"].iloc[0]
        assert abs(value_row["new_weight"] - 0.71) < 1e-9
        assert abs(value_row["raw_weight"] - 0.7) < 1e-9
        assert abs(value_row["prev_weight"] - 0.8) < 1e-9
        assert abs(value_row["delta"] - (0.71 - 0.8)) < 1e-9
        assert value_row["factor_count"] == 2
        assert value_row["factors"] == "A;B"


def test_save_style_totals_prev_none():
    """prev=None 이면 prev_weight, delta 빈값."""
    with tempfile.TemporaryDirectory() as tmp:
        history = Path(tmp)
        save_style_totals(
            history, "2026-04-30",
            raw_weights={"A": 1.0}, prev_weights=None, new_weights={"A": 1.0},
            style_map={"A": "Value"},
        )
        df = pd.read_csv(history / "style_totals_2026-04-30.csv")
        assert df["prev_weight"].isna().all()
        assert df["delta"].isna().all()
        assert df.iloc[0]["factor_count"] == 1
        assert df.iloc[0]["factors"] == "A"


def test_save_style_totals_excludes_zero_weight_from_count():
    """new_weight=0 factor 는 factor_count 에서 제외, factors 문자열에서도 제외."""
    with tempfile.TemporaryDirectory() as tmp:
        history = Path(tmp)
        raw = {"A": 1.0, "B": 0.0}                   # B 신규 0
        prev = {"A": 0.0, "B": 1.0}
        new = {"A": 1.0, "B": 0.0}                   # B는 new=0
        style_map = {"A": "Value", "B": "Value"}

        save_style_totals(history, "2026-04-30", raw, prev, new, style_map)
        df = pd.read_csv(history / "style_totals_2026-04-30.csv")
        row = df[df["style"] == "Value"].iloc[0]
        assert row["factor_count"] == 1
        assert row["factors"] == "A"


def test_save_factor_clusters_groups_correlated_factors():
    """|corr|>0.5 인 팩터끼리 같은 cluster_id, 독립 팩터는 단독 무리."""
    from service.pipeline.weight_history import save_factor_clusters

    rng = np.random.default_rng(0)
    base = rng.normal(0, 0.02, 48)
    ret = pd.DataFrame({
        "A": np.concatenate([[0.0], base + rng.normal(0, 0.004, 48)]),
        "B": np.concatenate([[0.0], base + rng.normal(0, 0.004, 48)]),   # A와 고상관
        "C": np.concatenate([[0.0], rng.normal(0, 0.02, 48)]),           # 독립
    }, index=pd.date_range("2022-01-31", periods=49, freq="ME"))
    weights = {"A": 0.4, "B": 0.35, "C": 0.25}
    styles = {"A": "Quality", "B": "Quality", "C": "Momentum"}

    with tempfile.TemporaryDirectory() as tmp:
        path = save_factor_clusters(Path(tmp), "2026-06-30", ret, weights, styles)
        df = pd.read_csv(path)
        cid = df.set_index("factor")["cluster_id"]
        assert cid["A"] == cid["B"]          # 고상관 무리
        assert cid["C"] != cid["A"]          # 독립
        # 무리 정렬: A+B 합산 비중(0.75)이 최대 -> cluster_id 1
        assert cid["A"] == 1
        assert set(df.columns) >= {"factor", "styleName", "weight", "cluster_id"}


def test_save_factor_clusters_single_factor_returns_none():
    from service.pipeline.weight_history import save_factor_clusters
    ret = pd.DataFrame({"A": [0.0, 0.01, 0.02]},
                       index=pd.date_range("2026-01-31", periods=3, freq="ME"))
    with tempfile.TemporaryDirectory() as tmp:
        assert save_factor_clusters(Path(tmp), "2026-06-30", ret, {"A": 1.0}, {}) is None

# ── style_cap_used (2026-10-21) ──────────────────────────────────────────

def _cap_effect(tmp: Path, snap: str, rows: list[tuple[str, str, float, float]]) -> None:
    pd.DataFrame(rows, columns=["factor", "styleName", "raw_weight", "fitted_weight"]).to_csv(
        tmp / f"style_cap_effect_{snap}.csv", index=False)


def test_style_cap_used_recovers_cap_of_that_book():
    """설정이 0.20 으로 바뀐 뒤에도 25% 캡으로 만든 북은 0.25 로 읽는다."""
    from service.pipeline.weight_history import style_cap_used
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        # Valuation 30% -> 캡 25% 로 눌림, 나머지는 재정규화로 증가
        _cap_effect(t, "2026-09-30", [("V1", "Valuation", 0.30, 0.25), ("Q1", "Quality", 0.40, 0.4286),
                                       ("G1", "Growth", 0.30, 0.3214)])
        assert style_cap_used(t, "2026-09-30", 0.20) == 0.25


def test_style_cap_used_rounds_unconverged_overshoot():
    """10회 반복 미수렴으로 20.5% 가 남아도 캡은 0.20 으로 본다."""
    from service.pipeline.weight_history import style_cap_used
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        _cap_effect(t, "2026-10-31", [("V1", "Valuation", 0.26, 0.205), ("H1", "Growth", 0.24, 0.205),
                                       ("A1", "Analyst", 0.50, 0.59)])
        assert style_cap_used(t, "2026-10-31", 0.25) == 0.20


def test_style_cap_used_falls_back_to_default():
    """이력이 없거나 캡이 발동하지 않았으면 기본값."""
    from service.pipeline.weight_history import style_cap_used
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        assert style_cap_used(t, "2026-09-30", 0.20) == 0.20                       # 파일 없음
        _cap_effect(t, "2026-08-31", [("V1", "Valuation", 0.5, 0.5), ("Q1", "Quality", 0.5, 0.5)])
        assert style_cap_used(t, "2026-08-31", 0.25) == 0.25                       # 캡 미발동
