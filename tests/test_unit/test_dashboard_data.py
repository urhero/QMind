# -*- coding: utf-8 -*-
"""service/report/dashboard_data.py 및 조립(dashboard.py) 단위/스모크 테스트."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from service.report import dashboard_data as dd


# ── 픽스처 빌더 ────────────────────────────────────────────────────────────

def _curves(n: int = 12) -> pd.DataFrame:
    """walk_forward_results.csv 스키마의 소형 곡선 DataFrame."""
    rets = [0.02, -0.01, 0.03, 0.01, 0.0, 0.02, -0.02, 0.04, 0.01, -0.01, 0.03, 0.02][:n]
    ret = pd.Series(rets)
    cum = (1 + ret).cumprod()
    half = (1 + ret * 0.5).cumprod()  # 선정 EW(약한 버전)
    dates = pd.date_range("2020-01-31", periods=n, freq="ME")
    df = pd.DataFrame(
        {
            "cew_return": ret.values,
            "ew_return": (ret * 0.5).values,
            "ew_all_return": (ret * 0.5).values,
            "ew_top50_return": (ret * 0.7).values,
            "cew_cumulative": cum.values,
            "ew_cumulative": half.values,
            "ew_all_cumulative": half.values,
            "ew_top50_cumulative": (1 + ret * 0.7).cumprod().values,
        },
        index=dates,
    )
    df.index.name = "date"
    return df


def _weights() -> pd.DataFrame:
    """weights_factor_*.csv 스키마의 소형 가중치 DataFrame."""
    data = [
        ("F1", "A", 0.1, [("T1", 0.05), ("T2", -0.05)]),
        ("F2", "A", 0.2, [("T1", 0.10), ("T3", -0.10)]),
        ("F3", "B", 0.3, [("T2", 0.15), ("T3", -0.15)]),
        ("F4", "B", 0.0, [("T1", 0.0), ("T4", 0.0)]),  # neutral, 제외 대상
    ]
    rows = []
    for factor, style, fw, tks in data:
        for tk, mp in tks:
            rows.append({
                "ddt": "2099-01-31", "ticker": tk, "isin": "X", "gvkeyiid": "Y",
                "mp_ls_weight": mp, "ls_weight": mp, "factor_weight": fw,
                "factor": factor, "style": style, "name": "n", "count": 10,
                "style_ls_weight": mp,
            })
    return pd.DataFrame(rows)


# ── compute_drawdown ───────────────────────────────────────────────────────

def test_compute_drawdown_zero_at_new_high():
    cum = pd.Series([1.0, 1.1, 1.2])
    dd_series = dd.compute_drawdown(cum)
    assert (dd_series <= 1e-12).all()
    assert dd_series.iloc[-1] == pytest.approx(0.0)


def test_compute_drawdown_recovers():
    cum = pd.Series([1.0, 0.8, 0.9])
    dd_series = dd.compute_drawdown(cum)
    assert dd_series.iloc[1] == pytest.approx(-0.2)
    assert dd_series.iloc[2] == pytest.approx(-0.1)


# ── compute_kpis / build_kpis ──────────────────────────────────────────────

def test_compute_kpis_keys_and_ranges():
    k = dd.compute_kpis(_curves())
    for key in ("cagr", "mdd", "sharpe", "calmar", "excess_cagr", "win_rate", "n_months"):
        assert key in k
    assert k["n_months"] == 12
    assert k["mdd"] <= 0.0
    assert 0.0 <= k["win_rate"] <= 1.0
    # win_rate = (cew_return > ew_return[선정 EW]) 비율. 선정 EW = cew*0.5 이므로
    # 양수 달에만 참 -> 양수 8개월 / 12 = 0.667. (음수 달엔 full 손실이 half 보다 커서 false)
    assert k["win_rate"] == pytest.approx(8 / 12)
    assert k["excess_cagr"] > 0.0  # net 양(+) 스트림에서 full > half


def test_build_kpis_prefers_diagnostics_values():
    """진단 파일 값이 있으면 곡선 계산값을 덮어쓴다 (기존 리포트와 일치)."""
    diag = {
        ("OOS 성과 - Constrained EW", "CAGR"): "1.6624%",
        ("OOS 성과 - Constrained EW", "Sharpe"): "0.8026",
        ("Constrained EW vs EW_Top50 비교", "Win Rate"): "49.3827%",
    }
    k = dd.build_kpis(_curves(), diag)
    assert k["cagr"] == pytest.approx(0.016624)
    assert k["sharpe"] == pytest.approx(0.8026)
    assert k["win_rate"] == pytest.approx(0.493827)


def test_build_kpis_merges_funnel_pattern():
    diag = {("1순위 - Funnel Value-Add", "패턴"): "NORMAL"}
    k = dd.build_kpis(_curves(), diag)
    assert k["funnel_pattern"] == "NORMAL"


def test_build_kpis_funnel_blank_without_diag():
    k = dd.build_kpis(_curves(), None)
    assert k["funnel_pattern"] == ""


# ── 현재 포트 집계 ─────────────────────────────────────────────────────────

def test_aggregate_style_weights_dedups_factor():
    s = dd.aggregate_style_weights(_weights())
    assert s["A"] == pytest.approx(0.3)  # F1 0.1 + F2 0.2
    assert s["B"] == pytest.approx(0.3)  # F3 0.3, F4 0.0 제외
    # 내림차순 정렬
    assert list(s.index)[0] in ("A", "B")


def test_active_factors_excludes_zero_weight():
    assert dd.active_factors(_weights()) == {"F1", "F2", "F3"}


def test_load_weights_drops_aggregation_rows(tmp_path):
    """MP 합계 행(style=='MP', factor=='AGG')은 로드 시 제외돼야 한다."""
    rows = [
        {"ddt": "2099-01-31", "ticker": "T1", "isin": "x", "gvkeyiid": "G1",
         "mp_ls_weight": 0.05, "ls_weight": 0.05, "factor_weight": 0.1,
         "factor": "F1", "style": "A", "name": "n", "count": 10, "style_ls_weight": 0.05},
        {"ddt": "2099-01-31", "ticker": "T1", "isin": "x", "gvkeyiid": "G1",
         "mp_ls_weight": 0.05, "ls_weight": 0.05, "factor_weight": 0.2928,
         "factor": "AGG", "style": "MP", "name": "MXCN1A_MP", "count": 10, "style_ls_weight": 0.05},
    ]
    p = tmp_path / "weights_factor_2099-01-31_test.csv"
    pd.DataFrame(rows).to_csv(p, index=True)
    w = dd.load_weights(p)
    assert "AGG" not in set(w["factor"])
    assert "MP" not in set(w["style"])
    assert len(w) == 1


def test_style_allocation_prefers_style_totals():
    deltas = pd.DataFrame({"style": ["A", "B", "C"], "new_weight": [0.25, 0.15, 0.0]})
    s = dd.style_allocation(_weights(), deltas)
    assert list(s.index) == ["A", "B"]          # 0 가중 C 제외 + 내림차순
    assert s["A"] == pytest.approx(0.25)         # cap 바인딩 값 그대로


def test_style_allocation_fallback_normalizes_to_one():
    # style_totals 없으면 factor_weight 집계를 합=1 로 정규화
    s = dd.style_allocation(_weights(), None)
    assert s.sum() == pytest.approx(1.0)


def test_factor_tilt_sorted_and_no_zero():
    tilt = dd.factor_tilt(_weights())
    assert list(tilt["factor"]) == ["F3", "F2", "F1"]
    assert "F4" not in set(tilt["factor"])


def test_top_longs_shorts_signs_and_dedup():
    ls = dd.top_longs_shorts(_weights(), n=2)
    by_ticker = dict(zip(ls["ticker"], zip(ls["weight"], ls["side"])))
    assert "T4" not in by_ticker  # net 0 제외
    assert by_ticker["T3"][0] < 0 and by_ticker["T3"][1] == "short"
    assert by_ticker["T1"][0] > 0 and by_ticker["T1"][1] == "long"
    # 티커 중복 없음
    assert ls["ticker"].is_unique


# ── 파일 탐색 / 파싱 ───────────────────────────────────────────────────────

def test_find_latest_weights_file_picks_max_date_excludes_style(tmp_path):
    for d in ("2025-01-31", "2025-02-28", "2025-03-31"):
        (tmp_path / d).mkdir()
    (tmp_path / "2025-01-31" / "weights_factor_2025-01-31_test.csv").write_text("x")
    (tmp_path / "2025-02-28" / "weights_factor_2025-02-28_test.csv").write_text("x")
    (tmp_path / "2025-03-31" / "weights_style_2025-03-31_test.csv").write_text("x")
    # 기준일 폴더 밖(test/ 등)의 파일은 후보가 아니다
    (tmp_path / "weights_factor_2025-04-30_test.csv").write_text("x")
    found = dd.find_latest_weights_file(tmp_path)
    assert found is not None
    assert found.name == "weights_factor_2025-02-28_test.csv"


def test_find_latest_weights_file_respects_end_date(tmp_path):
    for d in ("2025-01-31", "2025-02-28"):
        (tmp_path / d).mkdir()
        (tmp_path / d / f"weights_factor_{d}_test.csv").write_text("x")
    found = dd.find_latest_weights_file(tmp_path, end_date="2025-01-31")
    assert found.name == "weights_factor_2025-01-31_test.csv"


def test_find_latest_weights_file_none_when_empty(tmp_path):
    assert dd.find_latest_weights_file(tmp_path) is None


def test_snapshot_date_from_path():
    p = Path("output/weights_factor_2026-05-31_test.csv")
    assert dd.snapshot_date_from_path(p) == "2026-05-31"


def test_parse_diagnostics_roundtrip(tmp_path):
    path = tmp_path / "overfit_diagnostics.csv"
    df = pd.DataFrame(
        [
            ("1순위 - Funnel Value-Add", "패턴", "NORMAL", "정상"),
            ("OOS 성과 - Constrained EW", "Sharpe", "0.8026", ""),
        ],
        columns=["Category", "Metric", "Value", "Interpretation"],
    )
    df.to_csv(path, index=False, encoding="utf-8-sig")
    parsed = dd.parse_diagnostics(path)
    assert parsed[("1순위 - Funnel Value-Add", "패턴")] == "NORMAL"
    assert parsed[("OOS 성과 - Constrained EW", "Sharpe")] == "0.8026"


def test_parse_diagnostics_missing_file(tmp_path):
    assert dd.parse_diagnostics(tmp_path / "nope.csv") == {}


# ── 섹터 분해 ──────────────────────────────────────────────────────────────

def test_sector_net_weights_groups_and_unknown():
    w = pd.DataFrame({
        "ticker": ["T1", "T2", "T3", "T4"],
        "gvkeyiid": ["G1", "G2", "G3", "G9"],
        "mp_ls_weight": [0.10, -0.20, 0.05, 0.03],
    })
    sector_map = {"G1": "Tech", "G2": "Tech", "G3": "Energy"}  # G9 누락
    s = dd.sector_net_weights(w, sector_map)
    assert s["Tech"] == pytest.approx(-0.10)   # 0.10 - 0.20
    assert s["Energy"] == pytest.approx(0.05)
    assert s["Unknown"] == pytest.approx(0.03)  # G9 -> Unknown


def test_factor_delta_decomposition_prev_vs_new(tmp_path):
    hist = tmp_path / "mp_weight_history"
    hist.mkdir()
    pd.DataFrame({"factor": ["F1", "F2"], "styleName": ["S1", "S2"],
                  "raw_weight": [0.1, 0.2], "fitted_weight": [0.10, 0.20]}
                 ).to_csv(hist / "style_cap_effect_2026-01-31.csv", index=False)
    pd.DataFrame({"factor": ["F1", "F3"], "styleName": ["S1", "S3"],
                  "raw_weight": [0.15, 0.05], "fitted_weight": [0.15, 0.05]}
                 ).to_csv(hist / "style_cap_effect_2026-02-28.csv", index=False)
    out = dd.factor_delta_decomposition(tmp_path, "2026-02-28")
    assert out is not None
    d, prev_snap = out
    assert prev_snap == "2026-01-31"
    by = d.set_index("factor")
    assert by.loc["F1", "delta"] == pytest.approx(0.05)   # 비중 조정
    assert by.loc["F2", "delta"] == pytest.approx(-0.20)  # 편출 (new=0)
    assert by.loc["F3", "delta"] == pytest.approx(0.05)   # 신규 편입 (prev=0)
    assert by.loc["F2", "style"] == "S2"                  # 편출 팩터도 스타일 유지
    # 직전 파일 없는 첫 스냅샷은 None
    assert dd.factor_delta_decomposition(tmp_path, "2026-01-31") is None


def test_sector_style_decomposition_contrib_sums_to_net():
    w = pd.DataFrame({
        "ticker": ["T1", "T2", "T3"],
        "gvkeyiid": ["G1", "G2", "G3"],
        "style": ["S1", "S2", "S1"],
        "mp_ls_weight": [0.10, -0.20, 0.05],
    })
    sector_map = {"G1": "Tech", "G2": "Tech", "G3": "Energy"}
    d = dd.sector_style_decomposition(w, sector_map)
    by_sec = d.groupby("sec")["contrib"].sum()
    assert by_sec["Tech"] == pytest.approx(-0.10)
    assert by_sec["Energy"] == pytest.approx(0.05)
    nets = d.drop_duplicates("sec").set_index("sec")["net"]
    assert nets["Tech"] == pytest.approx(-0.10)


def test_top_longs_shorts_id_col_isin():
    """id_col='isin' 이면 isin 으로 집계/라벨 (출력 컬럼명은 'ticker' 유지)."""
    w = pd.DataFrame({
        "ticker": [None, "T2"],           # ticker 결측 종목도 포함돼야 함
        "isin": ["ISIN1", "ISIN2"],
        "mp_ls_weight": [0.10, -0.20],
    })
    ls = dd.top_longs_shorts(w, n=2, id_col="isin")
    assert set(ls["ticker"]) == {"ISIN1", "ISIN2"}


def test_load_sector_map_from_parquet(tmp_path):
    df = pd.DataFrame({
        "ddt": pd.to_datetime(["2099-01-31", "2099-01-31", "2099-02-28"]),
        "gvkeyiid": ["G1", "G2", "G1"],
        "ticker": ["T1", "T2", "T1"],
        "isin": ["i1", "i2", "i1"],
        "sec": ["Tech", "Energy", "Tech"],
        "val": [1.0, 2.0, 3.0],
        "factorAbbreviation": ["x", "x", "x"],
        "factorOrder": [0, 0, 0],
    })
    df.to_parquet(tmp_path / "factor_2099.parquet", index=False)
    m = dd.load_sector_map(tmp_path, "2099-01-31")
    assert m == {"G1": "Tech", "G2": "Energy"}


def test_load_sector_map_missing_returns_empty(tmp_path):
    assert dd.load_sector_map(tmp_path, "2099-01-31") == {}


# ── 백테스트 가중치 추이 / 회전율 ──────────────────────────────────────────

def _weight_history() -> pd.DataFrame:
    dates = pd.date_range("2020-01-31", periods=4, freq="ME")
    return pd.DataFrame(
        {"F1": [0.5, 0.5, 0.3, 0.3], "F2": [0.5, 0.5, 0.7, 0.7], "F3": [None, None, None, None]},
        index=pd.Index(dates, name="date"),
    )


def test_compute_turnover():
    t = dd.compute_turnover(_weight_history())
    assert t.iloc[0] == 0.0           # 첫 기간 0
    assert t.iloc[1] == pytest.approx(0.0)
    assert t.iloc[2] == pytest.approx(0.2)  # 0.5*(|0.3-0.5|+|0.7-0.5|)
    assert t.iloc[3] == pytest.approx(0.0)


def test_selection_churn_split_entries_and_exits():
    dates = pd.date_range("2020-01-31", periods=3, freq="ME")
    wh = pd.DataFrame(
        {"F1": [0.5, 0.5, 0.0],   # p3 편출
         "F2": [0.5, 0.3, 0.5],   # 유지
         "F3": [0.0, 0.2, 0.5]},  # p2 편입
        index=pd.Index(dates, name="date"),
    )
    split = dd.selection_churn_split(wh)
    assert list(split["entries"]) == [0.0, 1.0, 0.0]
    assert list(split["exits"]) == [0.0, 0.0, 1.0]


def test_style_weight_history_buckets():
    sh = dd.style_weight_history(_weight_history(), {"F1": "A", "F2": "B"})
    assert set(sh.columns) == {"A", "B", "Unknown"}
    assert sh["A"].iloc[2] == pytest.approx(0.3)
    assert sh["B"].iloc[2] == pytest.approx(0.7)
    assert sh["Unknown"].sum() == pytest.approx(0.0)  # F3 전부 NaN -> 0


def test_factor_style_map(tmp_path):
    path = tmp_path / "factor_info.csv"
    pd.DataFrame({
        "factorId": [0, 1],
        "factorAbbreviation": ["F1", "F2"],
        "factorName": ["n1", "n2"],
        "factorShortName": ["s1", "s2"],
        "styleName": ["A", "B"],
        "factorOrder": [0, 0],
    }).to_csv(path, index=False)
    m = dd.factor_style_map(path)
    assert m == {"F1": "A", "F2": "B"}


# ── build_dashboard 스모크 ─────────────────────────────────────────────────

def test_build_dashboard_smoke(tmp_path):
    pytest.importorskip("plotly")  # viz 레이어(dashboard) 의존 — 미설치 CI 에서는 skip
    # 백테스트 + 현재 포트 픽스처를 tmp output 디렉토리에 기록
    _curves().reset_index().to_csv(tmp_path / "walk_forward_results.csv", index=False)
    _weight_history().to_csv(tmp_path / "walk_forward_weight_history.csv")
    (tmp_path / "2099-01-31").mkdir()  # 기준일 폴더 (2026-09-09)
    _weights().to_csv(tmp_path / "2099-01-31" / "weights_factor_2099-01-31_test.csv", index=True)
    pd.DataFrame(
        {
            "factorAbbreviation": ["F1", "F2", "F9"],
            "factorName": ["f1", "f2", "f9"],
            "styleName": ["A", "A", "B"],
            "cagr": [0.1, 0.08, 0.02],
            "tstat": [5.0, 4.0, 1.0],
            "newey_west_tstat": [4.5, 3.5, 0.9],
            "rank_score": [5.0, 4.0, 1.0],
            "rank_style": [1.0, 2.0, 1.0],
            "rank_total": [1.0, 2.0, 3.0],
        }
    ).to_csv(tmp_path / "meta_data.csv", index=False)

    from service.report.dashboard import build_dashboard

    out = build_dashboard(output_dir=tmp_path)
    assert out.exists()
    assert out == tmp_path / "2099-01-31" / "dashboard_2099-01-31.html"
    html = out.read_text(encoding="utf-8")
    assert "백테스트" in html
    assert "현재 포트" in html
    assert "Plotly" in html  # plotly.js 인라인 임베드 확인


def test_diagnostics_table_pivots_variants_and_escapes(tmp_path):
    """Funnel 변형 행(EW_All/EW_Top50/Constrained EW)은 한 행 3열로 피벗되고,
    단일값 행은 colspan, Interpretation 의 '>'는 escape 된다."""
    pytest.importorskip("plotly")  # dashboard import 가 plotly(dashboard_charts)를 끌어옴
    from service.report.dashboard import _diagnostics_table

    pd.DataFrame(
        {
            "Category": ["Funnel", "Funnel", "Funnel", "Funnel"],
            "Metric": ["패턴", "EW_All CAGR", "EW_Top50 CAGR", "Constrained EW CAGR"],
            "Value": ["DRAG", "0.84%", "2.79%", "2.17%"],
            "Interpretation": ["B > C > A", "", "", ""],
        }
    ).to_csv(tmp_path / "overfit_diagnostics.csv", index=False, encoding="utf-8-sig")

    html = _diagnostics_table(tmp_path)
    # 세 변형 -> CAGR 한 행으로 피벗, 세 값 모두 존재
    assert html.count("<td>CAGR</td>") == 1
    assert "0.84%" in html and "2.79%" in html and "2.17%" in html
    assert "EW_All CAGR" not in html  # 원본 접두 지표명은 피벗되어 사라짐
    # 단일값(패턴) 행은 colspan, 해석의 '>'는 escape 되어 raw 누출 없음
    assert 'colspan="3"' in html
    assert "&gt;" in html and "B > C" not in html


def test_diagnostics_table_missing_file(tmp_path):
    pytest.importorskip("plotly")
    from service.report.dashboard import _diagnostics_table
    assert _diagnostics_table(tmp_path) == ""


def test_compute_series_perf_reads_named_columns():
    """곡선쌍 perf 가 지정 컬럼을 읽어 변형별로 다른 값을 낸다."""
    c = _curves()
    cew = dd.compute_series_perf(c, "cew_return", "cew_cumulative")
    ew_all = dd.compute_series_perf(c, "ew_all_return", "ew_all_cumulative")
    assert set(cew) == {"cagr", "mdd", "sharpe", "calmar"}
    assert cew["cagr"] > ew_all["cagr"]  # cew > 약한(0.5x) ew_all
    # n=12 이라 CAGR = 누적 마지막값 - 1
    assert cew["cagr"] == pytest.approx(float(c["cew_cumulative"].iloc[-1]) - 1.0)


def test_compute_drawdown_episodes():
    """고점->저점->회복 episode 추출 + ONGOING + 1% 임계값 필터."""
    dates = pd.date_range("2020-01-31", periods=6, freq="ME")
    cum = pd.Series([1.00, 1.20, 0.96, 1.20, 1.30, 1.17], index=dates)
    eps = dd.compute_drawdown_episodes(cum, min_depth=0.01)
    assert len(eps) == 2
    a, b = eps  # 깊은 순(가장 깊은 게 먼저)
    assert a["depth"] == pytest.approx(-0.20)
    assert a["peak"].strftime("%Y-%m") == "2020-02"
    assert a["trough"].strftime("%Y-%m") == "2020-03"
    assert a["recovery"].strftime("%Y-%m") == "2020-04"
    assert (a["peak_to_trough"], a["trough_to_recovery"], a["total"]) == (1, 1, 2)
    # 두번째 episode 는 회복 전(ONGOING)
    assert b["depth"] == pytest.approx(-0.10)
    assert b["recovery"] is None and b["trough_to_recovery"] is None
    assert b["total"] == 1
    # 1% 미만 dip 은 제외, 빈/단일 시리즈는 빈 리스트
    shallow = pd.Series([1.0, 0.995, 1.0], index=pd.date_range("2021-01-31", periods=3, freq="ME"))
    assert dd.compute_drawdown_episodes(shallow, min_depth=0.01) == []
    assert dd.compute_drawdown_episodes(pd.Series([], dtype=float)) == []


def test_drawdown_episodes_section_renders():
    """낙폭 episode 표: (본전략, 비교곡선) 분리 렌더 (2026-08-28). 곡선 없으면 빈 튜플."""
    pytest.importorskip("plotly")  # dashboard import 가 plotly 끌어옴
    from service.report.dashboard import _drawdown_episodes_section
    main, others = _drawdown_episodes_section(_curves())
    assert "peak→trough" in main and "episodes, 최대 낙폭 (Maximum Drawdown)" in main
    assert "episodes, 최대 낙폭 (Maximum Drawdown)" in others          # EW 계열은 비교곡선 쪽으로
    bare = _curves().drop(columns=["cew_cumulative", "ew_all_cumulative", "ew_top50_cumulative"])
    assert _drawdown_episodes_section(bare) == ("", "")


def test_monthly_returns_table():
    t = dd.monthly_returns_table(_curves())
    assert "Year" in t.columns
    assert [c for c in t.columns if c != "Year"] == list(range(1, 13))
    assert len(t) == 1  # _curves 는 2020 한 해 12개월
    yr = (1 + _curves()["cew_return"]).prod() - 1
    assert t["Year"].iloc[0] == pytest.approx(float(yr))


def test_extended_stats_keys_and_ranges():
    s = dd.extended_stats(_curves())
    assert set(s) >= {"ann_vol", "sortino", "best_month", "worst_month",
                      "pct_positive", "avg_month", "skew", "max_loss_streak"}
    assert s["ann_vol"] >= 0
    assert 0.0 <= s["pct_positive"] <= 1.0
    assert s["best_month"] >= s["worst_month"]
    assert isinstance(s["max_loss_streak"], int)


# ── 변동성 국면 (vol regime) ─────────────────────────────────────────────────

def _vol_regime_returns(n_high: int = 18, n_low: int = 18,
                        high: float = 0.05, low: float = 0.001) -> pd.DataFrame:
    """전반 고변동(+-high) n_high개월 + 후반 저변동(+-low) n_low개월 합성 수익률.

    후반부는 rolling(18) 창이 완전히 저변동 구간에 들어가도록 n_low>=window 로 맞춰,
    마지막 시점의 k 가 cap 에 걸리도록(median >> realized) 설계한다.
    """
    rets = [high, -high] * (n_high // 2) + [low, -low] * (n_low // 2)
    dates = pd.date_range("2019-01-31", periods=len(rets), freq="ME")
    return pd.DataFrame({"cew_return": rets}, index=pd.Index(dates, name="date"))


def test_build_vol_regime_formula_first_valid_row():
    """첫 유효 시점(window=18)은 median==realized(자기 자신) -> k==1.0 (cap 미적용)."""
    df = _vol_regime_returns()
    vr_df, _ = dd.build_vol_regime(df, window=18, k_cap=1.5)
    first_valid = vr_df.dropna().iloc[0]
    assert first_valid["realized_vol"] == pytest.approx(first_valid["median_vol"])
    assert first_valid["k"] == pytest.approx(1.0)


def test_build_vol_regime_cap_and_summary():
    df = _vol_regime_returns()
    vr_df, summary = dd.build_vol_regime(df, window=18, k_cap=1.5)

    r = df["cew_return"].astype(float)
    expected_realized = r.rolling(18).std() * (12 ** 0.5)
    expected_median = expected_realized.expanding().median()
    expected_k = (expected_median / expected_realized).clip(upper=1.5)
    pd.testing.assert_series_equal(vr_df["realized_vol"], expected_realized, check_names=False)
    pd.testing.assert_series_equal(vr_df["median_vol"], expected_median, check_names=False)
    pd.testing.assert_series_equal(vr_df["k"], expected_k, check_names=False)

    # 후반 저변동 구간 끝 -> median 이 realized 를 크게 웃돌아 cap(1.5)에 걸림
    assert summary["k"] == pytest.approx(1.5)
    assert summary["k_cap"] == pytest.approx(1.5)
    assert summary["realized_vol"] == pytest.approx(expected_realized.dropna().iloc[-1])
    assert summary["median_vol"] == pytest.approx(expected_median.dropna().iloc[-1])
    # 마지막(최저) realized_vol 이 전체 최솟값 -> 백분위는 가장 낮은 값 근처
    assert summary["min_vol"] == pytest.approx(summary["realized_vol"])
    assert summary["max_vol"] == pytest.approx(expected_realized.dropna().max())
    assert summary["percentile"] == pytest.approx(expected_realized.dropna().rank(pct=True).iloc[-1])
    assert 0.0 < summary["percentile"] <= 1.0


def test_build_vol_regime_accepts_path(tmp_path):
    """경로(csv) 입력도 df 입력과 동일 결과."""
    df = _vol_regime_returns()
    path = tmp_path / "walk_forward_results.csv"
    df.reset_index().to_csv(path, index=False)
    from_path = dd.build_vol_regime(path, window=18, k_cap=1.5)
    from_df = dd.build_vol_regime(df, window=18, k_cap=1.5)
    assert from_path is not None
    pd.testing.assert_frame_equal(from_path[0], from_df[0], check_freq=False)
    assert from_path[1] == from_df[1]


def test_build_vol_regime_none_when_rows_insufficient():
    """window+1(19) 미만 행이면 None (선택적 데이터 처리 패턴)."""
    short = _vol_regime_returns(n_high=18, n_low=0)  # 18행 < 19
    assert dd.build_vol_regime(short, window=18) is None


def test_build_vol_regime_none_when_file_missing(tmp_path):
    assert dd.build_vol_regime(tmp_path / "nope.csv") is None


def test_relative_metrics():
    m = dd.relative_metrics(_curves(), bench_col="ew_return")
    assert set(m) >= {"beta", "alpha_ann", "tracking_error", "info_ratio", "bench"}
    assert m["bench"] == "ew_return"
    assert m["tracking_error"] >= 0
    # _curves: ew_return = 0.5*cew_return -> beta = cov/var = 2.0
    assert m["beta"] == pytest.approx(2.0)
    # 벤치 컬럼 없으면 빈 dict
    assert dd.relative_metrics(_curves().drop(columns=["ew_return"])) == {}


def test_oos_rows_three_variants():
    """OOS 성과 행은 4지표 x EW/Top50/CEW. 곡선/컬럼 없으면 빈 리스트."""
    pytest.importorskip("plotly")  # dashboard import 가 plotly 끌어옴
    from service.report.dashboard import _oos_rows
    rows = _oos_rows(_curves())
    assert [r["metric"] for r in rows] == ["CAGR", "최대 낙폭 (Maximum Drawdown)", "Sharpe", "Calmar"]
    assert all(r["single"] is None and r["ew"] and r["top50"] and r["cew"] for r in rows)
    assert _oos_rows(_curves().drop(columns=["ew_all_return"])) == []
    assert _oos_rows(None) == []


def test_diagnostics_table_folds_oos_and_hides_funnel_variants(tmp_path):
    """곡선이 있으면 OOS 성과 블록으로 통합 + funnel 변형/CSV OOS 행 숨김(패턴 유지)."""
    pytest.importorskip("plotly")
    from service.report.dashboard import _diagnostics_table
    pd.DataFrame(
        {
            "Category": ["1순위 - Funnel Value-Add", "1순위 - Funnel Value-Add",
                         "1순위 - Funnel Value-Add", "OOS 성과 - Constrained EW"],
            "Metric": ["패턴", "EW_All CAGR", "Constrained EW CAGR", "Sharpe"],
            "Value": ["NORMAL", "0.8%", "2.2%", "0.73"],
            "Interpretation": ["C > B > A", "", "", ""],
        }
    ).to_csv(tmp_path / "overfit_diagnostics.csv", index=False, encoding="utf-8-sig")

    html = _diagnostics_table(tmp_path, curves=_curves())
    # 통합 블록 등장 (3열째 라벨은 유니버스별 '<BM>전략' — CEW 표기 대체, 2026-08-28)
    assert "OOS 성과 (EW/Top50/" in html
    assert "<td>Sharpe</td>" in html and "<td>Calmar</td>" in html
    assert "NORMAL" in html                        # 패턴 행 유지
    assert "&gt;" in html and "C > B" not in html  # 패턴 해석 escape
    assert "0.8%" not in html                      # funnel 변형 CSV 행은 숨김(곡선값으로 대체)


# ── factor_tilt / aggregate_style_weights: 중립 첫 행 누락 회귀 (2026-07-22) ──

def test_factor_tilt_includes_factor_with_neutral_first_row():
    """첫 행이 중립 종목(factor_weight=0)인 팩터도 tilt 에서 누락되면 안 된다."""
    w = pd.DataFrame({
        "factor": ["A", "A", "B"],
        "style": ["S1", "S1", "S2"],
        "factor_weight": [0.0, 0.6, 0.4],  # A 의 첫 행이 중립
    })
    tilt = dd.factor_tilt(w)
    assert set(tilt["factor"]) == {"A", "B"}
    assert tilt.set_index("factor").loc["A", "factor_weight"] == 0.6


def test_aggregate_style_weights_includes_neutral_first_row_factor():
    w = pd.DataFrame({
        "factor": ["A", "A", "B"],
        "style": ["S1", "S1", "S2"],
        "factor_weight": [0.0, 0.6, 0.4],
    })
    agg = dd.aggregate_style_weights(w)
    assert agg["S1"] == 0.6
    assert agg["S2"] == 0.4


# ── longs_shorts_style_decomposition (2026-07-22 신규) ──────────────────────

def _decomp_weights() -> pd.DataFrame:
    """2종목 x 3팩터, 혼합 부호 기여 픽스처."""
    return pd.DataFrame({
        "ticker": ["AAA", "AAA", "AAA", "BBB", "BBB"],
        "factor": ["F1", "F2", "F3", "F1", "F3"],
        "style": ["S1", "S1", "S2", "S1", "S2"],
        "mp_ls_weight": [0.004, -0.001, 0.002, -0.003, -0.002],
        "factor_weight": [0.1, 0.1, 0.2, 0.1, 0.2],
    })


def test_decomposition_contrib_sums_to_net():
    """(ticker, style) contrib 를 ticker 로 합치면 순비중과 일치."""
    d = dd.longs_shorts_style_decomposition(_decomp_weights(), n=5)
    by_ticker = d.groupby("ticker")["contrib"].sum()
    assert abs(by_ticker["AAA"] - 0.005) < 1e-12
    assert abs(by_ticker["BBB"] - (-0.005)) < 1e-12
    # net 컬럼도 동일
    nets = d.drop_duplicates("ticker").set_index("ticker")["net"]
    assert abs(nets["AAA"] - 0.005) < 1e-12


def test_decomposition_detail_lists_factors_and_rest():
    """detail 에 팩터명이 들어가고, top_factors 초과분은 '외 N개'로 합산."""
    d = dd.longs_shorts_style_decomposition(_decomp_weights(), n=5, top_factors=1)
    aaa_s1 = d[(d["ticker"] == "AAA") & (d["style"] == "S1")].iloc[0]
    assert "F1" in aaa_s1["detail"]          # |기여| 최대 팩터
    assert "외 1개" in aaa_s1["detail"]      # F2 는 잔여 합산
    assert abs(aaa_s1["contrib"] - 0.003) < 1e-12


def test_contrib_style_yearly_and_top_factors():
    """연도x스타일 %p 집계(컬럼 = 기여 합 내림차순) + 연도별 상/하위 팩터."""
    idx = pd.to_datetime(["2024-01-31", "2024-02-29", "2025-01-31"])
    c = pd.DataFrame({"A": [0.01, 0.01, -0.005], "B": [0.0, -0.002, 0.003]}, index=idx)
    sty = dd.contrib_style_yearly(c, {"A": "Value", "B": "Growth"})
    assert sty.loc[2024, "Value"] == pytest.approx(2.0)
    assert sty.loc[2024, "Growth"] == pytest.approx(-0.2)
    assert list(sty.columns)[0] == "Value"
    rows = dd.contrib_top_factors_yearly(c, n_top=1, n_bottom=1)
    r24 = next(r for r in rows if r["year"] == 2024)
    assert r24["top"][0][0] == "A" and r24["bottom"][0][0] == "B"
    assert r24["total"] == pytest.approx(1.8)
