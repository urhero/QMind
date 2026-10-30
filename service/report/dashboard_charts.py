# -*- coding: utf-8 -*-
"""대시보드 차트 레이어. DataFrame -> plotly Figure.

스타일 색은 service/report/style_colors.py 단일 출처 (PDF 북과 공유).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio

from config import PARAM
from service.report.dashboard_data import compute_drawdown
from service.report.style_colors import STYLE_COLORS, _DEFAULT_COLOR

# 본전략 표기 '<유니버스>전략' (예: MXWO전략) — 구 표기 CEW 대체 (2026-08-28 사용자 지정).
# dashboard.py 도 이 상수를 공유한다.
STRAT_LABEL = f"{PARAM['benchmark']}전략"

# 전략별 색/라벨 (누적수익 비교). 본전략은 브랜드 옐로로 가장 굵게.
_STRAT = [
    ("ew_all", "#707a8a", "전체 EW", 1.6),
    ("ew_top50", "#2dbdb6", "Top50 EW", 1.8),
    ("ew", "#3b82f6", "선정 EW", 1.8),
    ("cew", "#fcd535", STRAT_LABEL, 2.8),
]

# 트레이딩 시맨틱: 상승/롱 = green, 하락/숏 = red (Binance)
_LONG_COLOR = "#0ecb81"
_SHORT_COLOR = "#f6465d"
_POS_COLOR = "#0ecb81"
_NEG_COLOR = "#f6465d"
_MUTED = "#707a8a"

# plotly_dark 기반 커스텀 템플릿: 막대 테두리 제거 + 모서리 둥글게
# (3번 섹션 HTML 막대의 border-radius 4px 와 통일, 2026-08-25 사용자 지정)
_TPL = go.layout.Template(pio.templates["plotly_dark"])
_TPL.data.bar = [go.Bar(marker_line_width=0)]
_TPL.data.histogram = [go.Histogram(marker_line_width=0)]
_TPL.layout.barcornerradius = 4

# 다크 캔버스: paper/plot 투명 -> 카드 표면(#1e2329)이 비치게, 본문은 Binance body 색.
_DARK = dict(
    template=_TPL,
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#eaecef",
              family="Inter, -apple-system, 'Segoe UI', Roboto, sans-serif", size=12),
)
_BASE_LAYOUT = dict(margin=dict(l=60, r=20, t=50, b=45), **_DARK)


def _style_color(style: str) -> str:
    return STYLE_COLORS.get(style, _DEFAULT_COLOR)


# ── 백테스트 ───────────────────────────────────────────────────────────────

_MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def monthly_returns_heatmap_fig(table: pd.DataFrame) -> go.Figure:
    """연 x 월 수익률 히트맵 (%, 상승=green/하락=red 다이버징, 'Year' 열 포함)."""
    cols = list(range(1, 13)) + ["Year"]
    z = table.reindex(columns=cols)
    years = [str(y) for y in z.index]
    zv = z.values.astype(float) * 100.0
    # Dec 와 Year 사이 빈 스페이서 열 -> Year 가 연간 합산열임을 시각 구분
    zv = np.insert(zv, 12, np.nan, axis=1)
    text = [["" if v != v else f"{v:.2f}" for v in row] for row in zv]
    fig = go.Figure(go.Heatmap(
        z=zv, x=_MONTH_ABBR + ["", "Year"], y=years, zmid=0,
        colorscale=[[0.0, _NEG_COLOR], [0.5, "#2b3139"], [1.0, _POS_COLOR]],
        text=text, texttemplate="%{text}", textfont=dict(size=10),
        hovertemplate="%{y} %{x}: %{z:.2f}%<extra></extra>",
        xgap=2, ygap=2, colorbar=dict(title="%", thickness=10),
    ))
    fig.update_layout(title=f"수익률 (%, {STRAT_LABEL})",
                      height=max(240, 26 * len(years) + 130), **_BASE_LAYOUT)
    fig.update_yaxes(autorange="reversed")  # 최근 연도 위로
    return fig


def contrib_style_heatmap_fig(sty_yr: pd.DataFrame, basis: str = "팩터단") -> go.Figure:
    """연도 x 스타일 기여 히트맵 (%p, 정확 분해 — 2026-08-28 상설화).

    basis: 표시 기준 라벨 ("배포 기준" = 월별 실측 배수 적용 후)."""
    styles = list(sty_yr.columns)
    years = [str(y) for y in sty_yr.index]
    zv = sty_yr.values.astype(float)
    text = [["" if v != v else f"{v:+.2f}" for v in row] for row in zv]
    fig = go.Figure(go.Heatmap(
        z=zv, x=styles, y=years, zmid=0,
        colorscale=[[0.0, _NEG_COLOR], [0.5, "#2b3139"], [1.0, _POS_COLOR]],
        # hover 는 z 재포맷 대신 부호 포함 text 재사용 — 히트맵 z-포맷 경로는
        # d3 '+' 부호 플래그를 'bad format' 경고로 거부한다 (2026-08-28 실측;
        # bar/scatter 의 '+.2%' 는 무관). 내장 뷰어에선 이 경고가 crash 로 번짐.
        text=text, texttemplate="%{text}", textfont=dict(size=10),
        hovertemplate="%{y} %{x}: %{text}%p<extra></extra>",
        xgap=2, ygap=2, colorbar=dict(title="%p", thickness=10),
    ))
    fig.update_layout(title=f"연도별 스타일 기여 (%p, {basis})",
                      height=max(240, 26 * len(years) + 150), **_BASE_LAYOUT)
    fig.update_yaxes(autorange="reversed")  # 최근 연도 위로
    return fig


# 롤링 지표(TE/Sharpe) 공통 윈도우 색 — 12/24/36/48개월
_WINDOW_COLORS = {12: "#56B6C2", 24: "#f97316", 36: "#5B8DEF", 48: "#C77DDA"}


def rolling_sharpe_fig(r: pd.Series,
                       windows: tuple[int, ...] = (12, 24, 36, 48)) -> go.Figure:
    """롤링 12/24/36/48개월 연환산 Sharpe (실현 TE 차트와 동일 윈도우/색).

    데이터가 window 보다 짧아 전부 NaN 인 창은 생략한다."""
    fig = go.Figure()
    for w in windows:
        rs = (r.rolling(w).mean() / r.rolling(w).std()) * (12 ** 0.5)
        if rs.notna().sum() == 0:
            continue
        fig.add_trace(go.Scatter(
            x=rs.index, y=rs, name=f"{w}M",
            line=dict(color=_WINDOW_COLORS.get(w, _MUTED),
                      width=2.2 if w == 12 else 1.6),
            hovertemplate="%{x|%Y-%m}<br>Sharpe(" + str(w) + "M) %{y:.2f}<extra></extra>",
        ))
    fig.add_hline(y=0, line=dict(color=_MUTED, width=1, dash="dot"))
    fig.update_layout(
        title=f"롤링 Sharpe (12/24/36/48개월, {STRAT_LABEL})", height=360,
        margin=dict(l=60, r=30, t=50, b=95), **_DARK,
        legend=dict(orientation="h", yanchor="top", y=-48.0 / (360 - 145), x=0),
    )
    return fig


def build_vol_regime_chart(df: pd.DataFrame) -> go.Figure:
    """실현변동성(18M)/중위 실현변동성 라인(좌축) + 참고 배수 k(우축)."""
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df.index, y=df["realized_vol"], name="실현변동성 (18M)",
        line=dict(color="#3b82f6", width=2),
        hovertemplate="%{x|%Y-%m}<br>실현 %{y:.2%}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=df.index, y=df["median_vol"], name="중위 실현변동성 (확장창)",
        line=dict(color=_MUTED, width=1.6, dash="dot"),
        hovertemplate="%{x|%Y-%m}<br>중위 %{y:.2%}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=df.index, y=df["k"], name="참고 배수 k", yaxis="y2",
        line=dict(color="#fcd535", width=2),
        hovertemplate="%{x|%Y-%m}<br>k %{y:.2f}<extra></extra>",
    ))
    fig.update_layout(
        title="전략 실현변동성 국면 (18M) + 참고 배수 k", height=360,
        margin=dict(l=60, r=55, t=50, b=70), **_DARK,
        yaxis=dict(title="연환산 변동성", tickformat=".0%"),
        yaxis2=dict(title="k", overlaying="y", side="right", rangemode="tozero", showgrid=False),
        legend=dict(orientation="h", yanchor="bottom", y=-0.28, x=0, font=dict(size=11)),
    )
    return fig


def equity_curve_fig(curves: pd.DataFrame, shade: str = "monthly") -> go.Figure:
    """누적 수익 곡선 (2026-08-28 사용자 지정 범례 배치).

    범례 1행(legend)  : 본전략 · BM(MXWO, 실선 그린 — 구 BM+MP 오버레이 색 승계)
    범례 2행(legend2) : 전체/Top50/선정 EW — 전부 기본 숨김(legendonly)
    BM+MP 오버레이 곡선은 삭제 (BM 이 그 색·역할을 이어받음). BM 은 스케일이
    10배 이상 달라 보조축(y2) 유지.
    """
    fig = go.Figure()
    strat = dict((k, (c, l, w)) for k, c, l, w in _STRAT)
    color, label, width = strat["cew"]
    fig.add_trace(go.Scatter(
        x=curves.index, y=curves["cew_cumulative"], name=label,
        line=dict(color=color, width=width),
        hovertemplate="%{x|%Y-%m}<br>" + label + " %{y:.4f}<extra></extra>",
    ))
    has_bm = "bm_cumulative" in curves.columns
    if has_bm:
        fig.add_trace(go.Scatter(
            x=curves.index, y=curves["bm_cumulative"], name="BM (MXWO)", yaxis="y2",
            line=dict(color="#0ecb81", width=2.2),
            hovertemplate="%{x|%Y-%m}<br>BM %{y:.4f}<extra></extra>",
        ))
    for key in ("ew_all", "ew_top50", "ew"):
        col = f"{key}_cumulative"
        if col not in curves.columns:
            continue
        c, l, w = strat[key]
        fig.add_trace(go.Scatter(
            x=curves.index, y=curves[col], name=l, legend="legend2",
            line=dict(color=c, width=w), visible="legendonly",
            hovertemplate="%{x|%Y-%m}<br>" + l + " %{y:.4f}<extra></extra>",
        ))
    # 배경 음영 (2026-08-28 사용자 지정): 시장중립 오버레이라 액티브수익 = 본전략
    # 수익. +5bp 초과 = 아웃퍼폼(녹) / -5bp 미만 = 언더퍼폼(적) / |r| <= 5bp =
    # 유사(회). 인접한 같은 구간은 하나의 띠로 병합.
    # shade="monthly" = 당월 수익 기준, "ytd" = 연초 이후 누적수익 기준 (매년 리셋).
    r_m = curves["cew_return"].astype(float)
    if shade == "ytd":
        basis = (1 + r_m).groupby(r_m.index.year).cumprod() - 1.0
        basis_label = "본전략 YTD 수익 기준"
    else:
        basis = r_m
        basis_label = "본전략 월수익 기준"
    idx = curves.index
    def _cls(v):
        return "out" if v > 0.0005 else ("under" if v < -0.0005 else "flat")
    _SHADE = {"out": "rgba(14,203,129,0.10)", "under": "rgba(246,70,93,0.10)",
              "flat": "rgba(139,149,165,0.07)"}
    i = 0
    while i < len(idx):
        c = _cls(basis.iloc[i])
        j = i
        while j + 1 < len(idx) and _cls(basis.iloc[j + 1]) == c:
            j += 1
        x0 = idx[i - 1] if i > 0 else idx[i] - pd.DateOffset(months=1)
        fig.add_vrect(x0=x0, x1=idx[j], fillcolor=_SHADE[c],
                      layer="below", line_width=0)
        i = j + 1
    fig.add_annotation(
        xref="paper", yref="paper", x=0, y=1.08, showarrow=False,
        text="음영: <span style='color:#0ecb81'>아웃퍼폼(&gt;+5bp)</span> · "
             "<span style='color:#f6465d'>언더퍼폼(&lt;-5bp)</span> · "
             f"<span style='color:#8b95a5'>유사(±5bp 이내)</span> — {basis_label}",
        font=dict(size=11), align="left",
    )

    title = "누적 수익 곡선" if shade == "monthly" else "누적 수익 곡선 (YTD 음영)"
    layout = dict(
        title=title, height=410,
        legend=dict(orientation="h", yanchor="bottom", y=-0.24, x=0),
        legend2=dict(orientation="h", yanchor="bottom", y=-0.36, x=0),
        yaxis_title="전략 누적 (시작=1)", **_BASE_LAYOUT,
    )
    layout["margin"] = dict(l=60, r=60, t=50, b=95)
    if has_bm:
        layout["yaxis2"] = dict(title="BM 기준 누적 (시작=1)", overlaying="y",
                                side="right", showgrid=False)
    fig.update_layout(**layout)
    return fig


def drawdown_fig(curves: pd.DataFrame) -> go.Figure:
    dd = compute_drawdown(curves["cew_cumulative"])
    fig = go.Figure(go.Scatter(
        x=dd.index, y=dd, fill="tozeroy", name=f"{STRAT_LABEL} 낙폭",
        line=dict(color=_NEG_COLOR, width=1.4),
        hovertemplate="%{x|%Y-%m}<br>%{y:.2%}<extra></extra>",
    ))
    # .0% 는 눈금 간격이 1% 미만일 때 같은 라벨(-1% x2)이 반복됨 -> 소수 1자리
    fig.update_layout(title=f"낙폭 (drawdown, {STRAT_LABEL})", height=320,
                      yaxis_tickformat=".1%", **_BASE_LAYOUT)
    return fig


def monthly_dist_fig(curves: pd.DataFrame) -> go.Figure:
    r = curves["cew_return"].astype(float)
    fig = go.Figure(go.Histogram(
        x=r, nbinsx=40, marker_color="#3b82f6",
        hovertemplate="%{x:.2%} 구간<br>%{y}개월<extra></extra>",
    ))
    fig.add_vline(x=0, line_dash="dash", line_color=_MUTED)
    fig.update_layout(title=f"월별 수익 분포 ({STRAT_LABEL})", height=320,
                      xaxis_tickformat=".2%", bargap=0.03,
                      yaxis_title="개월 수", **_BASE_LAYOUT)
    return fig


# ── 현재 포트 / 배팅 ───────────────────────────────────────────────────────

def style_allocation_fig(style_weights: pd.Series, style_cap: float = 0.25) -> go.Figure:
    s = style_weights.sort_values(ascending=True)
    fig = go.Figure(go.Bar(
        x=s.values, y=s.index, orientation="h",
        marker_color=[_style_color(st) for st in s.index],
        hovertemplate="%{y}<br>%{x:.2%}<extra></extra>",
    ))
    fig.add_vline(x=style_cap, line_dash="dash", line_color=_NEG_COLOR,
                  annotation_text=f"cap {style_cap:.0%}", annotation_position="top")
    fig.update_layout(title="스타일 배분 (style_cap 라인)", height=360,
                      xaxis_tickformat=".0%", yaxis=dict(automargin=True), **_BASE_LAYOUT)
    return fig


def longs_shorts_fig(ls_df: pd.DataFrame, decomp_df: pd.DataFrame | None = None) -> go.Figure:
    d = ls_df.sort_values("weight")
    if decomp_df is None or decomp_df.empty:
        # 분해 데이터 없으면 기존 단일 막대 (하위호환)
        colors = [_LONG_COLOR if s == "long" else _SHORT_COLOR for s in d["side"]]
        fig = go.Figure(go.Bar(
            x=d["weight"], y=d["ticker"], orientation="h", marker_color=colors,
            hovertemplate="%{y}<br>%{x:.3%}<extra></extra>",
        ))
        fig.update_layout(title="종목별 순비중 상위 롱/숏", height=max(360, 22 * len(d) + 80),
                          xaxis_tickformat=".2%", yaxis=dict(automargin=True), **_BASE_LAYOUT)
        return fig

    # 스타일 스택 분해: 양/음 기여가 0 양쪽으로 갈려 '줄다리기'가 보인다 (barmode=relative).
    ticker_order = d["ticker"].tolist()  # 순비중 오름차순 (기존 정렬 유지)
    fig = go.Figure()
    style_order = (decomp_df.groupby("style")["contrib"]
                   .apply(lambda s: s.abs().sum()).sort_values(ascending=False).index)
    for st in style_order:
        s = decomp_df[decomp_df["style"] == st]
        fig.add_trace(go.Bar(
            x=s["contrib"], y=s["ticker"], orientation="h", name=st,
            marker=dict(color=_style_color(st), line=dict(width=0)),
            customdata=s["detail"],
            hovertemplate="%{y} · " + st + " %{x:+.2%}<br>%{customdata}<extra></extra>",
        ))
    # 순비중(합산) 마커 — 스택 위에서 종목별 최종 순노출 위치 표시
    nets = decomp_df.drop_duplicates("ticker").set_index("ticker")["net"].reindex(ticker_order)
    fig.add_trace(go.Scatter(
        x=nets.values, y=nets.index, mode="markers", name="순비중",
        marker=dict(symbol="diamond", size=7, color="#eaecef",
                    line=dict(width=1, color="#181a20")),
        hovertemplate="%{y} 순비중 %{x:+.2%}<extra></extra>",
    ))
    # 범례가 x축 눈금과 겹치지 않게: 높이 기준 px -> paper 비율로 축 아래 48px 에 고정
    # (y=-0.18 같은 고정 비율은 차트 높이에 따라 겹침 발생)
    h = max(400, 22 * len(ticker_order) + 170)
    fig.update_layout(
        title="종목별 순비중 상위 롱/숏 (스타일 분해, ◆=순비중)",
        barmode="relative", height=h,
        xaxis_tickformat=".2%",
        yaxis=dict(automargin=True, categoryorder="array", categoryarray=ticker_order),
        legend=dict(orientation="h", yanchor="top", y=-48.0 / (h - 160), x=0),
        margin=dict(l=60, r=20, t=50, b=110), **_DARK)
    return fig


def leaderboard_fig(meta: pd.DataFrame, selected: set,
                    tilt_df: pd.DataFrame | None = None) -> go.Figure:
    """팩터 리더보드: 미선정=회색 소점, 선정=스타일 색(현재 포트 섹션과 동일) +
    비중 비례 크기(면적 비례 -> sqrt 스케일, 지름 4~11px)."""
    m = meta.copy()
    m["selected"] = m["factorAbbreviation"].isin(selected)
    w_map = ({} if tilt_df is None or tilt_df.empty
             else dict(zip(tilt_df["factor"], tilt_df["factor_weight"])))

    fig = go.Figure()

    sub = m[~m["selected"]]
    fig.add_trace(go.Scatter(
        x=sub["tstat"], y=sub["cagr"], mode="markers", name="미선정",
        marker=dict(color=_MUTED, size=4, line=dict(width=0)),
        text=sub["factorAbbreviation"], customdata=sub["styleName"],
        hovertemplate="%{text} (%{customdata})<br>tstat %{x:.2f}<br>CAGR %{y:.2%}<extra></extra>",
    ))

    sel = m[m["selected"]].copy()
    sel["weight"] = sel["factorAbbreviation"].map(w_map).fillna(0.0)
    w_max = float(sel["weight"].max()) if len(sel) else 0.0
    # 면적이 비중에 비례하도록 지름은 sqrt 스케일 (비중 정보 없으면 일률 6px)
    smin, smax = 4.0, 11.0
    if w_max > 0:
        sel["size"] = smin + (sel["weight"] / w_max) ** 0.5 * (smax - smin)
    else:
        sel["size"] = 6.0
    # 스타일 범례는 스타일 합산 비중 내림차순 (스타일 배분 차트와 같은 순서감)
    style_order = (sel.groupby("styleName")["weight"].sum()
                   .sort_values(ascending=False).index)
    for st in style_order:
        s = sel[sel["styleName"] == st]
        fig.add_trace(go.Scatter(
            x=s["tstat"], y=s["cagr"], mode="markers", name=st,
            marker=dict(color=_style_color(st), size=s["size"], line=dict(width=0)),
            text=s["factorAbbreviation"], customdata=s["weight"],
            hovertemplate="%{text} (" + st + ")<br>비중 %{customdata:.2%}<br>tstat %{x:.2f}<br>CAGR %{y:.2%}<extra></extra>",
        ))

    # 범례를 x축 눈금 아래 48px 에 고정 (겹침 방지 — longs_shorts_fig 와 동일 처리)
    fig.update_layout(title="팩터 리더보드 (tstat vs CAGR, 크기=비중)", height=470,
                      xaxis_title="tstat", yaxis_title="CAGR", yaxis_tickformat=".0%",
                      legend=dict(orientation="h", yanchor="top", y=-48.0 / (470 - 160), x=0),
                      margin=dict(l=60, r=20, t=50, b=110), **_DARK)
    return fig


def sector_net_fig(sector_series: pd.Series,
                   decomp_df: pd.DataFrame | None = None) -> go.Figure:
    s = sector_series.sort_values()
    if decomp_df is None or decomp_df.empty:
        # 분해 데이터 없으면 기존 단일 막대 (하위호환)
        colors = [_POS_COLOR if v >= 0 else _NEG_COLOR for v in s.values]
        fig = go.Figure(go.Bar(
            x=s.values, y=s.index, orientation="h", marker_color=colors,
            hovertemplate="%{y}<br>%{x:.3%}<extra></extra>",
        ))
        fig.add_vline(x=0, line_color=_MUTED, line_width=1)
        fig.update_layout(title="섹터별 순비중 (롱-숏 순노출)", height=360,
                          xaxis_tickformat=".2%", yaxis=dict(automargin=True), **_BASE_LAYOUT)
        return fig

    # 스타일 스택 분해 — 종목별 롱/숏 차트와 동일한 스타일 색/구조 (2026-08-26)
    sec_order = s.index.tolist()  # 순노출 오름차순
    fig = go.Figure()
    style_order = (decomp_df.groupby("style")["contrib"]
                   .apply(lambda x: x.abs().sum()).sort_values(ascending=False).index)
    for st in style_order:
        d = decomp_df[decomp_df["style"] == st]
        fig.add_trace(go.Bar(
            x=d["contrib"], y=d["sec"], orientation="h", name=st,
            marker=dict(color=_style_color(st), line=dict(width=0)),
            hovertemplate="%{y} · " + st + " %{x:+.2%}<extra></extra>",
        ))
    nets = decomp_df.drop_duplicates("sec").set_index("sec")["net"].reindex(sec_order)
    fig.add_trace(go.Scatter(
        x=nets.values, y=nets.index, mode="markers", name="순노출",
        marker=dict(symbol="diamond", size=7, color="#eaecef",
                    line=dict(width=1, color="#181a20")),
        hovertemplate="%{y} 순노출 %{x:+.2%}<extra></extra>",
    ))
    # 반폭 카드라 범례(스타일 7~8개)가 3줄로 감김 -> 하단 여백 넉넉히 (x축 침범 방지)
    h = max(430, 26 * len(sec_order) + 210)
    fig.update_layout(
        title="섹터별 순비중 (스타일 분해, ◆=순노출)",
        barmode="relative", height=h, xaxis_tickformat=".2%",
        yaxis=dict(automargin=True, categoryorder="array", categoryarray=sec_order),
        legend=dict(orientation="h", yanchor="top", y=-52.0 / (h - 200), x=0,
                    font=dict(size=11)),
        margin=dict(l=60, r=20, t=50, b=150), **_DARK)
    return fig


def turnover_fig(turnover: pd.Series, churn_split: pd.DataFrame | None = None) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=turnover.index, y=turnover.values, name="회전율 (one-way)",
        marker_color="#3b82f6", offsetgroup="turnover",
        hovertemplate="%{x|%Y-%m}<br>%{y:.2%}<extra>회전율</extra>",
    ))
    avg = float(turnover[turnover > 0].mean()) if (turnover > 0).any() else 0.0
    fig.add_hline(y=avg, line_dash="dash", line_color=_MUTED,
                  annotation_text=f"평균 {avg:.1%}", annotation_position="top left")

    if churn_split is None:
        fig.update_layout(title="팩터 회전율 (one-way, 리밸런싱 시점)", height=320,
                          yaxis_tickformat=".0%", **_BASE_LAYOUT)
        return fig

    # 편입/편출을 보조 우측 y축에 쌓은(stacked) 막대로, 회전율 막대와 나란히 표시
    fig.add_trace(go.Bar(
        x=churn_split.index, y=churn_split["entries"], name="편입",
        marker_color=_POS_COLOR, yaxis="y2", offsetgroup="churn", legendgroup="churn",
        hovertemplate="%{x|%Y-%m}<br>편입 %{y:.0f}개<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        x=churn_split.index, y=churn_split["exits"], name="편출",
        marker_color=_SHORT_COLOR, yaxis="y2", offsetgroup="churn", legendgroup="churn",
        hovertemplate="%{x|%Y-%m}<br>편출 %{y:.0f}개<extra></extra>",
    ))
    fig.update_layout(
        title="팩터 회전율(좌, %) + 선정 편입·편출(우, 개)", height=380,
        margin=dict(l=60, r=55, t=50, b=70), **_DARK,
        barmode="relative", bargap=0.2,
        yaxis=dict(title="회전율", tickformat=".0%"),
        yaxis2=dict(title="편입·편출 팩터 수", overlaying="y", side="right",
                    rangemode="tozero", showgrid=False),
        legend=dict(orientation="h", yanchor="top", y=-0.16, x=0, font=dict(size=11)),
    )
    return fig


def style_weight_evolution_fig(style_hist: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    # 평균 비중 큰 스타일이 아래로 깔리도록 정렬
    order = style_hist.mean().sort_values(ascending=False).index
    for st in order:
        fig.add_trace(go.Scatter(
            x=style_hist.index, y=style_hist[st], name=st, mode="lines",
            stackgroup="one", line=dict(width=0.5, color=_style_color(st)),
            hovertemplate="%{x|%Y-%m}<br>" + str(st) + " %{y:.1%}<extra></extra>",
        ))
    fig.update_layout(
        title="스타일 비중 추이", height=430,
        margin=dict(l=60, r=20, t=50, b=95), **_DARK,
        yaxis_tickformat=".0%",
        legend=dict(orientation="h", yanchor="top", y=-0.13, x=0, font=dict(size=11)),
    )
    return fig


def style_delta_fig(deltas: pd.DataFrame) -> go.Figure:
    """전월 대비 스타일 비중 변화 (팩터 단위 데이터가 없을 때의 폴백)."""
    d = deltas.sort_values("delta")
    colors = [_POS_COLOR if v >= 0 else _NEG_COLOR for v in d["delta"]]
    fig = go.Figure(go.Bar(
        x=d["style"], y=d["delta"], marker_color=colors,
        hovertemplate="%{x}<br>%{y:+.2%}<extra></extra>",
    ))
    fig.update_layout(title="전월 대비 스타일 비중 변화", height=340,
                      yaxis_tickformat=".1%", xaxis_tickangle=-30, **_BASE_LAYOUT)
    return fig


def factor_delta_fig(d: pd.DataFrame, prev_snap: str, snap: str) -> go.Figure:
    """전월 대비 팩터별 비중(캡 후) 증감 — 스타일별 그룹 + 그룹 머리에 스타일
    순변화 합계 바 (2026-08-28 사용자 지정).

    d: dashboard_data.factor_delta_decomposition 결과 (factor/style/prev/new/delta).
    """
    def status(prev: float, new: float) -> str:
        if prev == 0:
            return "신규 편입"
        if new == 0:
            return "편출"
        return "비중 조정"

    style_net = d.groupby("style")["delta"].sum().sort_values(ascending=False)
    fig = go.Figure()
    y_order = []  # 위 -> 아래 순서로 쌓고 마지막에 뒤집어 categoryarray 로 사용
    for st in style_net.index:
        g = d[d["style"] == st].sort_values("delta", ascending=False)
        color = _style_color(st)
        total_label = f"<b>{st} 순변화</b>"
        y_order.append(total_label)
        fig.add_trace(go.Bar(
            x=[style_net[st]], y=[total_label], orientation="h",
            marker=dict(color=color, opacity=0.45),
            text=[f"{style_net[st]*100:+.2f}%p"], textposition="outside",
            textfont=dict(size=10),
            hovertemplate=f"{st} 순변화 %{{x:+.2%}}<extra></extra>",
            showlegend=False,
        ))
        y_order.extend(g["factor"].tolist())
        fig.add_trace(go.Bar(
            x=g["delta"], y=g["factor"], orientation="h",
            marker_color=color,
            customdata=[(p, nw, status(p, nw))
                        for p, nw in zip(g["prev"], g["new"])],
            hovertemplate=("%{y} (" + st + ")<br>%{customdata[2]}: "
                           "%{customdata[0]:.2%} -> %{customdata[1]:.2%} "
                           "(%{x:+.2%})<extra></extra>"),
            showlegend=False,
        ))
    fig.add_vline(x=0, line_color=_MUTED, line_width=1)
    fig.update_layout(
        title=f"전월 대비 팩터 비중 변화 ({prev_snap} -> {snap}, 캡 후, 스타일별 묶음)",
        height=max(360, 16 * len(y_order) + 110), xaxis_tickformat=".2%",
        # categoryarray 는 아래 -> 위 순서라 위 -> 아래로 쌓은 리스트를 뒤집는다
        yaxis=dict(automargin=True, tickfont=dict(size=10),
                   categoryorder="array", categoryarray=list(reversed(y_order))),
        **_BASE_LAYOUT)
    return fig


def deploy_exposure_fig(df: pd.DataFrame) -> go.Figure:
    """배포 노출 고정 현황 (2026-08-19): 배수 전 book gross(좌축) + 적용 배수(우축).

    목표 노출을 고정하면 배수가 netting 변동을 그대로 흡수하므로, 두 선이
    거울처럼 반대로 움직이는 것이 정상이다 (gross 낮은 달 = 배수 큼).
    """
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df.index, y=df["book_gross_before"], name="배수 전 book gross",
        line=dict(color="#3b82f6", width=2),
        hovertemplate="%{x|%Y-%m}<br>gross %{y:.1%}<extra></extra>",
    ))
    # Gross Active Risk 목표 궤적 (계단식) — bbg ex-ante TE 를 일정하게
    # 유지하기 위한 Active Risk 조정 이력을 그대로 보여준다 (2026-08-21).
    if "target_gross" in df.columns and df["target_gross"].notna().any():
        fig.add_trace(go.Scatter(
            x=df.index, y=df["target_gross"], name="Gross Active Risk 목표",
            line=dict(color="#22c55e", width=2.4, shape="hv"),
            hovertemplate="%{x|%Y-%m}<br>목표 gross %{y:.0%}<extra></extra>",
        ))
    fig.add_trace(go.Scatter(
        x=df.index, y=df["long_exposure"] * 2, name="실제 배포 gross",
        line=dict(color="#0ecb81", width=1.6, dash="dot"),
        hovertemplate="%{x|%Y-%m}<br>배포 gross %{y:.1%}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=df.index, y=df["multiplier"], name="적용 배수", yaxis="y2",
        line=dict(color="#fcd535", width=2),
        hovertemplate="%{x|%Y-%m}<br>배수 %{y:.3f}<extra></extra>",
    ))
    fig.update_layout(
        title="Gross Active Risk · 배수 추이", height=360,
        margin=dict(l=60, r=55, t=50, b=70), **_DARK,
        yaxis=dict(title="gross 노출", tickformat=".0%", rangemode="tozero"),
        yaxis2=dict(title="배수", overlaying="y", side="right", rangemode="tozero", showgrid=False),
        legend=dict(orientation="h", yanchor="bottom", y=-0.28, x=0, font=dict(size=11)),
    )
    return fig


def rolling_te_fig(r: pd.Series,
                   windows: tuple[int, ...] = (12, 24, 36, 48)) -> go.Figure:
    """실현 TE 추이: 롤링 12/24/36/48개월 표준편차의 연환산 (시장중립 -> 액티브=오버레이).

    데이터가 window 보다 짧아 전부 NaN 인 창은 생략한다."""
    fig = go.Figure()
    for w in windows:
        te = r.rolling(w).std() * (12 ** 0.5)
        if te.notna().sum() == 0:
            continue
        fig.add_trace(go.Scatter(
            x=te.index, y=te, name=f"{w}M",
            line=dict(color=_WINDOW_COLORS.get(w, _MUTED),
                      width=2.2 if w == 24 else 1.6),
            hovertemplate="%{x|%Y-%m}<br>TE(" + str(w) + "M) %{y:.2%}<extra></extra>",
        ))
    full = float(r.std() * (12 ** 0.5))
    fig.add_hline(y=full, line=dict(color=_MUTED, width=1.4, dash="dot"),
                  annotation_text=f"전기간 {full:.2%}", annotation_position="top left")
    fig.update_layout(
        title="실현 Tracking Error (12/24/36/48개월 롤링, 연환산)", height=360,
        margin=dict(l=60, r=30, t=50, b=95), **_DARK,
        yaxis=dict(title="TE", tickformat=".1%", rangemode="tozero"),
        legend=dict(orientation="h", yanchor="top", y=-48.0 / (360 - 145), x=0),
    )
    return fig
