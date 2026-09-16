# -*- coding: utf-8 -*-
"""대시보드 데이터 레이어 (read-only).

기존 output/*.csv 만 읽어 정돈된 DataFrame 과 파생 지표(낙폭/KPI/스타일 집계/
상위 롱숏/팩터 틸트/선정 팩터/진단 파싱)를 만든다. plotly 의존성이 없어
단위 테스트가 쉽다. 파이프라인 코드는 일절 건드리지 않는다.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from service.paths import DATA_DIR, DATE_GLOB, OUTPUT_DIR, UNIVERSE_DATA_DIR
from service.report.diagnostics_keys import CAT_CMP, CAT_FUNNEL, CAT_OOS_CEW, METRIC_PATTERN

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


# ── 파일 탐색 ──────────────────────────────────────────────────────────────

def find_latest_weights_file(output_dir: Path, end_date: str | None = None) -> Path | None:
    """total_aggregated_weights_<date>[suffix].csv 중 최신(또는 지정일) 파일 반환.

    _style 변형은 제외. 파일명에서 날짜를 파싱해 최대 날짜를 고르고,
    동일 날짜가 여럿이면 수정시각(mtime)이 최신인 파일을 택한다.
    """
    output_dir = Path(output_dir)
    candidates: list[tuple[str, float, Path]] = []
    for p in output_dir.glob(f"{DATE_GLOB}/total_aggregated_weights_*.csv"):
        if p.name.startswith("total_aggregated_weights_style"):
            continue
        m = _DATE_RE.search(p.name)
        if not m:
            continue
        d = m.group(1)
        if end_date is not None and d != end_date:
            continue
        candidates.append((d, p.stat().st_mtime, p))
    if not candidates:
        return None
    candidates.sort(key=lambda t: (t[0], t[1]))
    return candidates[-1][2]


def snapshot_date_from_path(path: Path) -> str | None:
    """파일명에서 YYYY-MM-DD 스냅샷 날짜 추출."""
    m = _DATE_RE.search(Path(path).name)
    return m.group(1) if m else None


# ── 백테스트 ───────────────────────────────────────────────────────────────

def load_backtest_curves(path: Path) -> pd.DataFrame:
    """walk_forward_results.csv -> date 인덱스 DataFrame."""
    df = pd.read_csv(path, parse_dates=["date"])
    return df.set_index("date").sort_index()


def compute_drawdown(cum: pd.Series) -> pd.Series:
    """누적수익 곡선 -> 낙폭 시계열 (running max 대비 비율, <= 0)."""
    cum = pd.Series(cum).astype(float)
    running_max = cum.cummax()
    return cum / running_max - 1.0


def _months_between(d0, d1) -> int:
    """두 월말 Timestamp 사이 개월 수 (월 그리드 기준, 갭에 견고)."""
    return (d1.year - d0.year) * 12 + (d1.month - d0.month)


def compute_drawdown_episodes(cum: pd.Series, min_depth: float = 0.01) -> list[dict]:
    """누적수익 곡선에서 낙폭 episode(고점->저점->회복)를 추출한다.

    각 underwater 구간을 1개 episode 로 본다. 직전 고점(running max) 아래로 내려간
    시점부터, 다시 그 고점 이상으로 회복(또는 데이터 끝)할 때까지가 1 episode.
    회복 전이면 recovery=None('ONGOING'), total 은 고점~마지막 시점.

    Args:
        cum: date 인덱스의 누적수익 Series (월말 그리드 가정).
        min_depth: 이 깊이(절대값) 미만 episode 제외 (기본 1%).

    Returns:
        깊은 순(depth 오름차순=가장 깊은 게 먼저) episode dict 리스트. 각 dict:
        depth(<0), peak/trough(Timestamp), recovery(Timestamp|None),
        peak_to_trough/trough_to_recovery/total(개월; recovery 없으면 trough_to_recovery=None).
    """
    cum = pd.Series(cum).astype(float)
    if len(cum) < 2:
        return []
    dates, vals = list(cum.index), cum.values

    def _episode(peak_i, trough_i, recovery_i):
        peak_d, trough_d = dates[peak_i], dates[trough_i]
        depth = float(vals[trough_i] / vals[peak_i] - 1.0)
        if recovery_i is None:
            rec_d, t2r, total = None, None, _months_between(peak_d, dates[-1])
        else:
            rec_d = dates[recovery_i]
            t2r, total = _months_between(trough_d, rec_d), _months_between(peak_d, rec_d)
        return {"depth": depth, "peak": peak_d, "trough": trough_d,
                "peak_to_trough": _months_between(peak_d, trough_d),
                "recovery": rec_d, "trough_to_recovery": t2r, "total": total}

    episodes = []
    peak_i, peak_val, in_dd, trough_i = 0, vals[0], False, 0
    for i in range(1, len(vals)):
        v = vals[i]
        if v >= peak_val:
            if in_dd:
                episodes.append(_episode(peak_i, trough_i, i))
                in_dd = False
            peak_val, peak_i = v, i
        else:
            if not in_dd:
                in_dd, trough_i = True, i
            elif v < vals[trough_i]:
                trough_i = i
    if in_dd:
        episodes.append(_episode(peak_i, trough_i, None))

    episodes = [e for e in episodes if abs(e["depth"]) >= min_depth]
    episodes.sort(key=lambda e: e["depth"])  # depth<0 -> 오름차순=가장 깊은 게 먼저
    return episodes


def compute_series_perf(curves: pd.DataFrame, ret_col: str, cum_col: str) -> dict:
    """임의 수익률/누적 곡선쌍에서 CAGR/MDD/Sharpe/Calmar 계산 (연 12개월 기준).

    overfit_diagnostics.csv 와 동일 공식 — 곡선만으로 진단값과 일치(research §7.3).
    cew/ew/ew_all/ew_top50 등 어떤 변형이든 같은 함수로 산출해 OOS 성과를 비교한다.
    """
    r = curves[ret_col].astype(float)
    cum = curves[cum_col].astype(float)
    n = len(r)
    cagr = float(cum.iloc[-1] ** (12.0 / n) - 1.0) if n else float("nan")
    mdd = float(compute_drawdown(cum).min())
    std = float(r.std())
    sharpe = float(r.mean() / std * np.sqrt(12)) if std > 0 else float("nan")
    calmar = float(cagr / abs(mdd)) if mdd != 0 else float("nan")
    return {"cagr": cagr, "mdd": mdd, "sharpe": sharpe, "calmar": calmar}


def compute_kpis(curves: pd.DataFrame) -> dict:
    """CEW 곡선에서 CAGR/MDD/Sharpe/Calmar/승률/초과CAGR 계산.

    overfit_diagnostics.csv 와 동일 공식 (연 12개월 기준).
    """
    n = len(curves)
    p = compute_series_perf(curves, "cew_return", "cew_cumulative")
    cagr, mdd, sharpe, calmar = p["cagr"], p["mdd"], p["sharpe"], p["calmar"]

    # 초과수익/승률은 진단(compare_cew_vs_ew_oos)과 동일하게 '선정 EW'(ew_*) 기준.
    ew_cum = curves["ew_cumulative"].astype(float)
    ew_cagr = float(ew_cum.iloc[-1] ** (12.0 / n) - 1.0) if n else float("nan")
    excess_cagr = cagr - ew_cagr
    win_rate = float(
        (curves["cew_return"].astype(float) > curves["ew_return"].astype(float)).mean()
    )
    return {
        "cagr": cagr,
        "mdd": mdd,
        "sharpe": sharpe,
        "calmar": calmar,
        "excess_cagr": excess_cagr,
        "win_rate": win_rate,
        "n_months": n,
    }


def monthly_returns_table(curves: pd.DataFrame) -> pd.DataFrame:
    """CEW 월별 수익률을 연(행) x 월(1~12 열) 행렬 + 연간 복리('Year' 열)로 변환."""
    r = curves["cew_return"].astype(float)
    df = pd.DataFrame({"y": r.index.year, "m": r.index.month, "r": r.values})
    pivot = (df.pivot_table(index="y", columns="m", values="r", aggfunc="first")
             .reindex(columns=range(1, 13)))
    pivot["Year"] = df.groupby("y")["r"].apply(lambda s: (1.0 + s).prod() - 1.0)
    return pivot


def extended_stats(curves: pd.DataFrame) -> dict:
    """QC Key Statistics 스타일 확장 지표 (CEW 월별 수익률 기반, 연 12개월)."""
    r = curves["cew_return"].astype(float)
    down = r[r < 0]
    dstd = float(down.std()) if len(down) > 1 else float("nan")
    streak = mx = 0
    for v in r:
        streak = streak + 1 if v < 0 else 0
        mx = max(mx, streak)
    return {
        "ann_vol": float(r.std() * (12 ** 0.5)),
        "sortino": float(r.mean() / dstd * (12 ** 0.5)) if dstd and dstd > 0 else float("nan"),
        "best_month": float(r.max()),
        "worst_month": float(r.min()),
        "pct_positive": float((r > 0).mean()),
        "avg_month": float(r.mean()),
        "skew": float(r.skew()),
        "max_loss_streak": int(mx),
    }


def build_vol_regime(source, window: int = 18, k_cap: float = 1.5):
    """전략(CEW) 월간 수익률의 실현변동성 국면 + 참고 배수 k.

    k_t = clip(median_vol_t / realized_vol_t, upper=k_cap). median_vol_t/realized_vol_t 모두
    t월까지의 데이터만 쓰는 expanding/rolling 이라, k_t 는 원래 "t월까지 알 수 있는 다음달
    (t+1) 노출 참고치"다 (연구 docs/experiments/calmar_overlay_20260721.md 의 walk-forward
    shift(1)과 동일 의미 — 이 함수는 계산만 하고 자동으로 shift 하지 않으니, 호출부에서
    k.iloc[-1]을 "다음 달" 참고용으로 해석할 것). 자동 스케일링에는 쓰지 않고 Bloomberg
    Target Portfolio 의 multiplier/TE 타깃을 수동으로 정할 때 정성 참고용으로만 쓴다.

    Args:
        source: walk_forward_results.csv 경로(Path/str) 또는 load_backtest_curves 로 이미
            읽은 DataFrame ('cew_return' 컬럼 필요).
        window: 실현변동성 rolling 창(개월). 기본 18 (연구 채택안, docs/experiments 참조).
        k_cap: k 상한. 기본 1.5.

    Returns:
        (df, summary) 튜플. df 는 원본 date 인덱스에 realized_vol/median_vol/k 컬럼을 더한
        DataFrame. summary 는 최신 시점 realized_vol/median_vol/k, k_cap, 그리고 realized_vol
        의 역대 백분위(percentile, rank pct)/min_vol/max_vol 을 담은 dict.
        파일이 없거나 유효 행이 window+1 미만이면 None (기존 선택적 데이터 처리 패턴,
        예: load_style_deltas).
    """
    if isinstance(source, pd.DataFrame):
        df = source
    else:
        p = Path(source)
        if not p.exists():
            return None
        df = load_backtest_curves(p)

    if len(df) < window + 1 or "cew_return" not in df.columns:
        return None

    r = df["cew_return"].astype(float)
    realized_vol = r.rolling(window).std() * np.sqrt(12)
    median_vol = realized_vol.expanding().median()
    k = (median_vol / realized_vol).clip(upper=k_cap)
    out = pd.DataFrame({"realized_vol": realized_vol, "median_vol": median_vol, "k": k})

    valid = realized_vol.dropna()
    if valid.empty:
        return None
    summary = {
        "realized_vol": float(valid.iloc[-1]),
        "median_vol": float(median_vol.dropna().iloc[-1]),
        "k": float(k.dropna().iloc[-1]),
        "k_cap": float(k_cap),
        "percentile": float(valid.rank(pct=True).iloc[-1]),
        "min_vol": float(valid.min()),
        "max_vol": float(valid.max()),
    }
    return out, summary


def relative_metrics(curves: pd.DataFrame, bench_col: str = "ew_return") -> dict:
    """벤치마크(기본 선정 EW) 대비 Beta / Alpha(연) / 추적오차(연) / 정보비율 (CEW)."""
    if bench_col not in curves.columns:
        return {}
    p = curves["cew_return"].astype(float)
    b = curves[bench_col].astype(float)
    var_b = float(b.var())
    beta = float(p.cov(b) / var_b) if var_b > 0 else float("nan")
    alpha_ann = float((p.mean() - beta * b.mean()) * 12) if var_b > 0 else float("nan")
    active = p - b
    te = float(active.std() * (12 ** 0.5))
    ir = float(active.mean() * 12 / te) if te > 0 else float("nan")
    return {"beta": beta, "alpha_ann": alpha_ann, "tracking_error": te,
            "info_ratio": ir, "bench": bench_col}


def parse_diagnostics(path: Path) -> dict:
    """overfit_diagnostics.csv (세로형) -> {(category, metric): value_str}."""
    p = Path(path)
    if not p.exists():
        return {}
    df = pd.read_csv(p, encoding="utf-8-sig")
    out: dict[tuple[str, str], str] = {}
    for _, row in df.iterrows():
        out[(str(row["Category"]).strip(), str(row["Metric"]).strip())] = str(row["Value"]).strip()
    return out


def _diag_num(diag: dict, category: str, metric: str) -> float | None:
    """진단 Value 문자열('1.66%' / '0.80')을 float 으로. 없거나 N/A 면 None."""
    v = diag.get((category, metric))
    if v is None:
        return None
    v = str(v).strip()
    if v in ("", "N/A", "nan"):
        return None
    try:
        return float(v[:-1]) / 100.0 if v.endswith("%") else float(v)
    except ValueError:
        return None


def build_kpis(curves: pd.DataFrame, diag: dict | None = None) -> dict:
    """곡선에서 KPI 계산 후, 진단파일(overfit_diagnostics.csv) 값이 있으면 그것을 우선.

    진단 파일은 사용자의 기존 리포트 기준값이므로, 일치시켜 혼선을 막는다.
    진단 파일이 없으면(test 모드 등) 곡선 계산값을 그대로 쓴다.
    """
    k = compute_kpis(curves)
    diag = diag or {}
    cew = CAT_OOS_CEW
    cmp_ = CAT_CMP
    overrides = {
        "cagr": _diag_num(diag, cew, "CAGR"),
        "mdd": _diag_num(diag, cew, "MDD"),
        "sharpe": _diag_num(diag, cew, "Sharpe"),
        "calmar": _diag_num(diag, cew, "Calmar"),
        "win_rate": _diag_num(diag, cmp_, "Win Rate"),
        "excess_cagr": _diag_num(diag, cmp_, "Excess CAGR"),
    }
    for key, val in overrides.items():
        if val is not None:
            k[key] = val
    k["funnel_pattern"] = diag.get((CAT_FUNNEL, METRIC_PATTERN), "")
    return k


# ── 현재 포트 / 배팅 ───────────────────────────────────────────────────────

def load_weights(path: Path) -> pd.DataFrame:
    """total_aggregated_weights_*.csv -> 무명 인덱스열 + MP 집계행 제거한 DataFrame.

    파일에는 종목x팩터 원천 행 외에 MP 합계 행(style=='MP', factor=='AGG')이 섞여 있다.
    이는 합계 산출물이므로 차트 집계(스타일/팩터/종목/섹터)에서 제외한다 - 포함하면
    거대 합계 막대로 x축이 왜곡되고 종목 순비중이 이중 계산된다.
    """
    df = pd.read_csv(path)
    drop = [c for c in df.columns if str(c).startswith("Unnamed")]
    if drop:
        df = df.drop(columns=drop)
    if "factor" in df.columns:
        df = df[df["factor"] != "AGG"]
    if "style" in df.columns:
        df = df[df["style"] != "MP"]
    return df.reset_index(drop=True)


def active_factors(weights: pd.DataFrame) -> set:
    """factor_weight > 0 인 (선정된) 팩터 집합."""
    fw = weights[weights["factor_weight"] > 0]
    return set(fw["factor"].unique())


def aggregate_style_weights(weights: pd.DataFrame) -> pd.Series:
    """스타일별 배분: 팩터 단위로 dedup 후 factor_weight 를 style 로 합산 (합 ~= 1).

    주의: weight>0 필터를 dedup **이전**에 적용해야 한다. 종목 단위 행에서 중립
    종목은 factor_weight=0 이라, dedup 을 먼저 하면 첫 행이 중립인 팩터가 통째로
    누락된다 (2026-07-22 수정 — 구 코드는 선정 팩터 일부를 빠뜨렸음).
    """
    nz = weights[weights["factor_weight"] > 0]
    uniq = nz[["factor", "style", "factor_weight"]].drop_duplicates(subset=["factor"])
    return uniq.groupby("style")["factor_weight"].sum().sort_values(ascending=False)


def style_allocation(weights: pd.DataFrame, style_deltas: pd.DataFrame | None = None) -> pd.Series:
    """스타일 배분(합 ~= 1). 운영 style_totals(new_weight, cap 바인딩 반영) 우선.

    style_totals 가 없으면(test 모드) weights 의 factor_weight 집계를 1로 정규화해 대체.
    """
    if style_deltas is not None and not style_deltas.empty and "new_weight" in style_deltas.columns:
        s = style_deltas.set_index("style")["new_weight"].astype(float)
        return s[s > 0].sort_values(ascending=False)
    s = aggregate_style_weights(weights)
    total = s.sum()
    return (s / total) if total > 0 else s


def factor_tilt(weights: pd.DataFrame, top_n: int | None = None) -> pd.DataFrame:
    """팩터별 비중(factor_weight) 내림차순 (factor, style, factor_weight).

    weight>0 필터를 dedup 이전에 적용 (aggregate_style_weights 와 동일 사유,
    2026-07-22 수정 — 구 코드는 첫 행이 중립 종목인 팩터를 누락).
    """
    nz = weights[weights["factor_weight"] > 0]
    uniq = nz[["factor", "style", "factor_weight"]].drop_duplicates(subset=["factor"])
    uniq = uniq.sort_values("factor_weight", ascending=False)
    if top_n:
        uniq = uniq.head(top_n)
    return uniq.reset_index(drop=True)


def longs_shorts_style_decomposition(weights: pd.DataFrame, n: int = 15,
                                     top_factors: int = 5,
                                     id_col: str = "ticker") -> pd.DataFrame:
    """상위 롱/숏 종목의 (ticker, style) 순기여 분해 + 호버용 팩터 상세.

    top_longs_shorts 와 동일한 종목 집합(순비중 상위 롱 n + 숏 n)에 대해,
    스타일별 mp_ls_weight 합(contrib)과 그 스타일 안에서 |기여| 상위
    top_factors 개 팩터 문자열(detail, "외 N개 합계" 포함)을 만든다.

    id_col: 종목 식별/라벨 컬럼 ("ticker" 또는 "isin" — ticker 는 결측 종목이
    빠지므로 대시보드는 isin 사용, 2026-08-26). 출력 컬럼명은 스키마 유지 위해
    "ticker" 로 고정한다 (값은 id_col 의 값).

    Returns:
        (ticker, style, contrib, net, detail) DataFrame.
        contrib 를 ticker 로 합치면 net(종목 순비중)과 일치한다.
    """
    net = weights.groupby(id_col)["mp_ls_weight"].sum()
    net = net[net != 0]
    sel = pd.concat([net.sort_values(ascending=False).head(n), net.sort_values().head(n)])
    sel = sel[~sel.index.duplicated()]

    sub = weights[weights[id_col].isin(sel.index)]
    per_f = (sub.groupby([id_col, "style", "factor"], observed=True)["mp_ls_weight"]
             .sum().reset_index())
    per_f = per_f[per_f["mp_ls_weight"] != 0]

    # (id, style) 그룹은 최대 2n x 스타일수 (~400개) — viz 레이어 소규모 루프 허용
    rows = []
    for (t, st), g in per_f.groupby([id_col, "style"], observed=True):
        g = g.reindex(g["mp_ls_weight"].abs().sort_values(ascending=False).index)
        top = g.head(top_factors)
        parts = [f"{r.factor} {r.mp_ls_weight:+.2%}" for r in top.itertuples()]
        rest = len(g) - len(top)
        if rest > 0:
            parts.append(f"외 {rest}개 {g['mp_ls_weight'].iloc[top_factors:].sum():+.2%}")
        rows.append({
            "ticker": t, "style": st, "contrib": float(g["mp_ls_weight"].sum()),
            "net": float(net[t]), "detail": "<br>".join(parts),
        })
    return pd.DataFrame(rows)


def top_longs_shorts(weights: pd.DataFrame, n: int = 15,
                     id_col: str = "ticker") -> pd.DataFrame:
    """종목별 순비중(mp_ls_weight 합) 상위 롱 n + 하위(숏) n -> (ticker, weight, side).

    id_col: 종목 식별/라벨 컬럼 (출력 컬럼명은 "ticker" 로 고정, 값은 id_col 값)."""
    net = weights.groupby(id_col)["mp_ls_weight"].sum()
    net = net[net != 0]
    longs = net.sort_values(ascending=False).head(n)
    shorts = net.sort_values().head(n)
    parts = [
        pd.DataFrame({"ticker": longs.index, "weight": longs.values, "side": "long"}),
        pd.DataFrame({"ticker": shorts.index, "weight": shorts.values, "side": "short"}),
    ]
    out = pd.concat(parts, ignore_index=True)
    return out.drop_duplicates(subset=["ticker"]).reset_index(drop=True)


def load_meta(path: Path) -> pd.DataFrame:
    """meta_data.csv -> 팩터 랭킹 테이블."""
    return pd.read_csv(path)


def factor_delta_decomposition(output_dir: Path, snap: str):
    """직전 스냅샷 대비 팩터별 명목 비중(캡 후) 변화 — 어떤 팩터가 +/- 였는지.

    mp_weight_history/style_cap_effect_*.csv 시계열에서 snap 직전 파일과 비교한다.
    신규 편입 팩터는 prev=0, 편출 팩터는 new=0 으로 잡힌다.

    Returns:
        (DataFrame(factor, style, prev, new, delta), prev_snap) — 직전 파일이
        없거나 변화가 전혀 없으면 None.
    """
    hist = Path(output_dir) / "mp_weight_history"
    files = sorted(hist.glob("style_cap_effect_*.csv"))
    names = [f.stem.replace("style_cap_effect_", "") for f in files]
    if snap not in names or names.index(snap) == 0:
        return None
    i = names.index(snap)
    cols = ["factor", "styleName", "fitted_weight"]
    cur = pd.read_csv(files[i])[cols]
    prev = pd.read_csv(files[i - 1])[cols]
    m = cur.merge(prev, on="factor", how="outer", suffixes=("", "_prev"))
    m["style"] = m["styleName"].fillna(m["styleName_prev"])
    m["new"] = m["fitted_weight"].fillna(0.0)
    m["prev"] = m["fitted_weight_prev"].fillna(0.0)
    m["delta"] = m["new"] - m["prev"]
    m = m[m["delta"].abs() > 1e-12]
    if m.empty:
        return None
    return m[["factor", "style", "prev", "new", "delta"]].reset_index(drop=True), names[i - 1]


def load_style_deltas(output_dir: Path, snapshot_date: str) -> pd.DataFrame | None:
    """mp_weight_history/style_totals_<date>.csv (전월대비 delta). 없으면 None (test 모드)."""
    p = Path(output_dir) / "mp_weight_history" / f"style_totals_{snapshot_date}.csv"
    if not p.exists():
        return None
    return pd.read_csv(p)


# ── 섹터 분해 (소스 parquet read-only join) ─────────────────────────────────

def load_sector_map(data_dir: Path, snapshot_date: str) -> dict:
    """소스 factor parquet에서 해당 스냅샷 날짜의 gvkeyiid -> sec 매핑.

    파이프라인을 건드리지 않고 기존 parquet 을 read-only 로 읽어 join 재료를 만든다.
    parquet 이 없거나 날짜/sec 가 없으면 빈 dict (섹터 차트는 생략됨).
    """
    try:
        from service.download.parquet_io import load_factor_parquet
        year = int(snapshot_date[:4])
        df = load_factor_parquet(data_dir, start_year=year, end_year=year)
    except (FileNotFoundError, ValueError, ImportError):
        return {}
    if "sec" not in df.columns or "gvkeyiid" not in df.columns:
        return {}
    same_day = pd.to_datetime(df["ddt"]).dt.strftime("%Y-%m-%d") == snapshot_date
    df = df[same_day]
    if df.empty:
        return {}
    uniq = df.drop_duplicates("gvkeyiid")
    return dict(zip(uniq["gvkeyiid"].astype(str), uniq["sec"].astype(str)))


def sector_net_weights(weights: pd.DataFrame, sector_map: dict) -> pd.Series:
    """종목 순비중(mp_ls_weight)을 섹터로 묶은 순노출. 매핑 없는 종목은 'Unknown'."""
    w = weights.copy()
    w["sec"] = w["gvkeyiid"].astype(str).map(sector_map).fillna("Unknown")
    s = w.groupby("sec")["mp_ls_weight"].sum()
    return s[s != 0].sort_values()


def sector_style_decomposition(weights: pd.DataFrame, sector_map: dict) -> pd.DataFrame:
    """섹터 순노출의 스타일별 분해 (sec, style, contrib, net) — 롱/숏 분해와 동일 구조.

    contrib 를 sec 로 합치면 net(섹터 순노출)과 일치한다. 스타일 컬럼이 없거나
    분해가 비면 빈 DataFrame (호출부는 단일 막대로 폴백).
    """
    if "style" not in weights.columns:
        return pd.DataFrame()
    w = weights.copy()
    w["sec"] = w["gvkeyiid"].astype(str).map(sector_map).fillna("Unknown")
    per = (w.groupby(["sec", "style"], observed=True)["mp_ls_weight"]
           .sum().reset_index(name="contrib"))
    per = per[per["contrib"] != 0]
    if per.empty:
        return per
    net = per.groupby("sec")["contrib"].sum()
    per["net"] = per["sec"].map(net)
    return per.reset_index(drop=True)


# ── 백테스트 가중치 추이 / 회전율 ──────────────────────────────────────────

def load_weight_history(path: Path) -> pd.DataFrame:
    """walk_forward_weight_history.csv -> date 인덱스 x factor 컬럼 DataFrame."""
    df = pd.read_csv(path, parse_dates=["date"])
    return df.set_index("date").sort_index()


def load_factor_contrib(path: Path) -> pd.DataFrame:
    """factor_contrib.csv -> date 인덱스 DataFrame (비중 x 당월수익, 행합 = cew_return).

    워크포워드 엔진이 각 OOS 월의 '당시 규칙' 기준으로 기록한 정확 기여도
    (2026-08-28 상설화). 사후 재구성은 최종 규칙 look-ahead 로 왜곡되므로 금지.
    """
    df = pd.read_csv(path, parse_dates=["date"])
    return df.set_index("date").sort_index()


def contrib_style_yearly(contrib: pd.DataFrame, style_map: dict) -> pd.DataFrame:
    """연도 x 스타일 기여 합 (%p). 스타일 미등록 팩터는 '기타'.

    컬럼은 전 기간 기여 합 내림차순 정렬 (읽는 순서 = 중요도).
    """
    c = contrib.apply(pd.to_numeric, errors="coerce").fillna(0.0)
    sty = c.T.groupby(c.columns.map(lambda f: style_map.get(f, "기타"))).sum().T
    yr = sty.groupby(sty.index.year).sum() * 100.0
    return yr[yr.sum().sort_values(ascending=False).index]


def contrib_top_factors_yearly(contrib: pd.DataFrame, n_top: int = 3,
                               n_bottom: int = 2) -> list[dict]:
    """연도별 상/하위 기여 팩터. [{year, total, top: [(f, %p)], bottom: [(f, %p)]}]"""
    c = contrib.apply(pd.to_numeric, errors="coerce")
    yearly = c.groupby(c.index.year).sum() * 100.0
    out = []
    for y, row in yearly.iterrows():
        r = row.dropna()
        r = r[r != 0.0]
        out.append({"year": int(y), "total": float(r.sum()),
                    "top": list(r.nlargest(n_top).items()),
                    "bottom": list(r.nsmallest(n_bottom).items())})
    return out


def compute_turnover(weight_history: pd.DataFrame) -> pd.Series:
    """월별 one-way 회전율 = 0.5 * sum(|w_t - w_{t-1}|). 첫 기간은 0."""
    wh = weight_history.apply(pd.to_numeric, errors="coerce").fillna(0.0).sort_index()
    turnover = 0.5 * wh.diff().abs().sum(axis=1)
    if len(turnover) > 0:
        turnover.iloc[0] = 0.0
    return turnover


def selection_churn_split(weight_history: pd.DataFrame) -> pd.DataFrame:
    """매 기간 편입(entries)/편출(exits) 팩터 수를 분리. 첫 기간 0.

    entries = 직전 비활성 -> 활성 된 팩터 수, exits = 활성 -> 비활성 된 팩터 수.
    (entries + exits == 선정 집합 대칭차집합 크기)
    """
    wh = weight_history.apply(pd.to_numeric, errors="coerce").fillna(0.0).sort_index()
    active = wh.gt(0)
    prev = active.shift(1, fill_value=False)
    entries = (active & ~prev).sum(axis=1).astype(float)
    exits = (~active & prev).sum(axis=1).astype(float)
    if len(entries) > 0:
        entries.iloc[0] = 0.0  # 첫 기간은 비교 대상 없음
        exits.iloc[0] = 0.0
    return pd.DataFrame({"entries": entries, "exits": exits})


def factor_style_map(factor_info_path: Path) -> dict:
    """factor_info.csv -> factorAbbreviation -> styleName 매핑."""
    df = pd.read_csv(factor_info_path)
    return dict(zip(df["factorAbbreviation"].astype(str), df["styleName"].astype(str)))


def factor_name_map(factor_info_path: Path) -> dict:
    """factor_info.csv -> factorAbbreviation -> factorName(전체명) 매핑."""
    df = pd.read_csv(factor_info_path)
    return dict(zip(df["factorAbbreviation"].astype(str), df["factorName"].astype(str)))


def factor_desc_map(desc_path: Path) -> dict:
    """factor_desc_kr.csv -> factorAbbreviation -> 1줄 한글 설명 매핑.

    성과 원천 표용 수기 큐레이션 (2026-08-28). 미등재 팩터는 설명 줄 생략 —
    표에 새 팩터가 등장하면 이 CSV에 행을 추가한다.
    """
    df = pd.read_csv(desc_path)
    return dict(zip(df["factorAbbreviation"].astype(str), df["desc_kr"].astype(str)))


def style_weight_history(weight_history: pd.DataFrame, factor_style: dict) -> pd.DataFrame:
    """팩터 가중치 이력을 스타일별로 합산 -> date x style DataFrame (스택 영역용)."""
    wh = weight_history.apply(pd.to_numeric, errors="coerce").fillna(0.0)
    buckets: dict[str, list] = {}
    for col in wh.columns:
        buckets.setdefault(factor_style.get(str(col), "Unknown"), []).append(col)
    data = {style: wh[cols].sum(axis=1) for style, cols in buckets.items()}
    return pd.DataFrame(data, index=wh.index)
