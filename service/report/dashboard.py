# -*- coding: utf-8 -*-
"""대시보드 조립 레이어.

기존 output CSV -> plotly 차트 -> 단일 자체완결 HTML 파일.
plotly.js 를 인라인으로 임베드해 오프라인에서도 단독으로 열린다.
파이프라인 코드는 건드리지 않는다(read-only).

진입점: build_dashboard(end_date=None) -> Path
"""
from __future__ import annotations

import logging
import re
from html import escape
from pathlib import Path

import pandas as pd

from config import PARAM, PIPELINE_PARAMS
from service.paths import dated, latest
from service.report import dashboard_charts as ch
from service.report import dashboard_data as dd
from service.report.dashboard_charts import STRAT_LABEL

logger = logging.getLogger(__name__)

_PLOTLY_CFG = {"displayModeBar": False, "responsive": True}

_PAGE_CSS = """
:root {
  --canvas:#0b0e11; --card:#1e2329; --elev:#2b3139; --hair:#2b3139;
  --primary:#fcd535; --on-primary:#181a20; --body:#eaecef; --on-dark:#ffffff;
  --muted:#707a8a; --muted-strong:#929aa5; --up:#0ecb81; --down:#f6465d;
  --sans:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Malgun Gothic',sans-serif;
  --mono:'JetBrains Mono','Consolas',ui-monospace,monospace;
  color-scheme: dark;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--canvas); color: var(--body);
  font-family: var(--sans); }
.wrap { max-width: 1200px; margin: 0 auto; padding: 24px 20px 48px; }
header { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap;
  margin-bottom: 20px; }
header .title { font-size: 24px; font-weight: 600; letter-spacing: -0.3px; color: var(--on-dark); }
header .date { font-size: 13px; font-weight: 600; color: var(--primary); background: var(--card);
  padding: 4px 12px; border-radius: 8px; font-family: var(--mono); }
header .gen { font-size: 12px; color: var(--muted); margin-left: auto; }
h2 { font-size: 18px; font-weight: 600; color: var(--on-dark); margin: 32px 0 14px;
  border-bottom: 1px solid var(--hair); padding-bottom: 8px; }
.kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 10px; margin-bottom: 14px; }
.kpi { background: var(--card); border: 1px solid var(--hair); border-radius: 8px; padding: 12px 14px; }
.kpi-label { font-size: 12px; color: var(--muted); }
.kpi-val { font-size: 22px; font-weight: 600; margin-top: 2px; color: var(--on-dark);
  font-family: var(--mono); letter-spacing: -0.3px; }
.grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
  gap: 14px; margin-bottom: 14px; }
.card { background: var(--card); border: 1px solid var(--hair); border-radius: 12px;
  padding: 8px 10px; overflow: hidden; }
.card.full { margin-bottom: 14px; }
.note { font-size: 13px; color: var(--muted); padding: 8px 0; }
.note-toggle { margin: 2px 0 6px; }
.note-toggle summary { cursor: pointer; font-size: 14px; color: var(--muted);
  list-style: none; opacity: .8; padding: 2px 6px; display: inline-block; }
.note-toggle summary:hover { opacity: 1; color: var(--muted-strong); }
.note-toggle summary::before { content: '▸ '; }
.note-toggle[open] summary::before { content: '▾ '; }
.note-toggle .note { padding: 4px 0 0 14px; }
.sec-toggle { margin: 32px 0 14px; }
.sec-toggle summary { cursor: pointer; list-style: none; font-size: 18px;
  font-weight: 600; color: var(--on-dark); border-bottom: 1px solid var(--hair);
  padding-bottom: 8px; }
.sec-toggle summary::before { content: '▸ '; color: var(--muted); }
.sec-toggle[open] summary::before { content: '▾ '; color: var(--muted); }
.sec-toggle[open] summary { margin-bottom: 14px; }
.plotly-graph-div { width: 100% !important; }
.diag-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.diag-table th { text-align: left; color: var(--muted); font-weight: 600; font-size: 12px;
  border-bottom: 1px solid var(--hair); padding: 8px 10px; }
.diag-table td { padding: 7px 10px; border-bottom: 1px solid var(--hair); vertical-align: top; }
.diag-table tbody tr:last-child td { border-bottom: none; }
.diag-table tr:hover td { background: var(--elev); }
.diag-cat { color: var(--primary); font-weight: 600; white-space: nowrap; }
.diag-val { font-family: var(--mono); color: var(--on-dark); white-space: nowrap; text-align: right; }
.diag-val.diag-span { text-align: center; color: var(--muted-strong); }
.diag-interp { color: var(--muted-strong); }
.dd-cap { font-size: 13px; font-weight: 600; color: var(--primary); padding: 2px 10px 10px; }
.dd-num { font-family: var(--mono); color: var(--on-dark); white-space: nowrap; }
.contrib-line { padding: 2px 0; line-height: 1.45; }
.contrib-line + .contrib-line { border-top: 1px solid rgba(255,255,255,0.04); }
.contrib-name { color: var(--muted); font-size: 12px; }
.contrib-desc { color: var(--muted); font-size: 12px; padding: 1px 0 2px; }
.contrib-table { table-layout: fixed; }
.contrib-table td { vertical-align: top; }
/* 라이트 테마 (우측 상단 토글, 기본은 다크) */
html.light {
  --canvas:#f4f5f7; --card:#ffffff; --elev:#eef0f3; --hair:#e3e6ea;
  --primary:#b98700; --on-primary:#ffffff; --body:#242a31; --on-dark:#14181d;
  --muted:#7c8694; --muted-strong:#5a6470;
  color-scheme: light;
}
.theme-btn { background: var(--card); border: 1px solid var(--hair); color: var(--body);
  border-radius: 8px; padding: 4px 12px; font-size: 12px; font-weight: 600;
  font-family: var(--sans); cursor: pointer; }
.theme-btn:hover { background: var(--elev); }
"""

# 일부 브라우저에서 plotly 초기 렌더 폭이 컨테이너와 어긋나 잘리는 경우 대비:
# 로드/리사이즈 시 각 차트를 컨테이너 폭에 맞춰 다시 그린다.
_RESIZE_SCRIPT = (
    "<script>(function(){function fit(){if(!window.Plotly)return;"
    "document.querySelectorAll('.plotly-graph-div').forEach(function(d){"
    "try{Plotly.Plots.resize(d);}catch(e){}});}"
    "window.addEventListener('load',fit);window.addEventListener('resize',fit);"
    "setTimeout(fit,300);})();</script>"
)

# 다크/라이트 토글: html.light 클래스 + localStorage 유지 + plotly 축/폰트 색 재적용
# localStorage 는 반드시 try/catch — 샌드박스 뷰어(allow-same-origin 없는 iframe,
# 앱 내장 미리보기)에서 접근 자체가 SecurityError 를 던져 uncaught 오류가 됨 (2026-08-28)
_THEME_SCRIPT = (
    "<script>(function(){var KEY='dash-theme';"
    "function getLS(){try{return localStorage.getItem(KEY);}catch(e){return null;}}"
    "function setLS(v){try{localStorage.setItem(KEY,v);}catch(e){}}"
    "function themeCharts(light){if(!window.Plotly)return;"
    "var font=light?'#333d47':'#eaecef',grid=light?'#e3e6ea':'#283442';"
    "document.querySelectorAll('.plotly-graph-div').forEach(function(d){"
    "if(!d._fullLayout)return;var u={'font.color':font};"
    "Object.keys(d._fullLayout).forEach(function(k){if(/^[xy]axis\\d*$/.test(k)){"
    "u[k+'.gridcolor']=grid;u[k+'.linecolor']=grid;u[k+'.zerolinecolor']=grid;}});"
    "try{Plotly.relayout(d,u);}catch(e){}});}"
    "function apply(light){document.documentElement.classList.toggle('light',light);"
    "var b=document.getElementById('theme-btn');"
    "if(b)b.textContent=light?'다크 모드':'라이트 모드';themeCharts(light);}"
    "window.__toggleTheme=function(){"
    "var light=!document.documentElement.classList.contains('light');"
    "setLS(light?'light':'dark');apply(light);};"
    "window.addEventListener('load',function(){"
    "if(getLS()==='light')apply(true);});"
    "})();</script>"
)


def _fig_div(fig, include_js: bool = False) -> str:
    return fig.to_html(
        full_html=False,
        include_plotlyjs=("inline" if include_js else False),
        config=_PLOTLY_CFG,
    )


def _collapsible(title: str, inner: str) -> str:
    """참고성 섹션용 접힘 래퍼 — 제목(h2 스타일 summary)만 보이고 클릭 시 내용 표시."""
    return (f'<details class="sec-toggle"><summary>{title}</summary>'
            f'{inner}</details>')


def _grid2(cards: list[str]) -> str:
    """카드 div 리스트를 2열 그리드 행들로 묶는다 (홀수는 마지막 단독)."""
    rows = []
    for i in range(0, len(cards), 2):
        rows.append('<div class="grid2">' + "".join(cards[i:i + 2]) + "</div>")
    return "".join(rows)


def _kpi_cards(kpis: dict) -> str:
    def fnum(v, fmt):
        try:
            return format(v, fmt)
        except (ValueError, TypeError):
            return "-"

    items = [
        ("CAGR", fnum(kpis["cagr"], ".2%")),
        ("최대 낙폭 (Maximum Drawdown)", fnum(kpis["mdd"], ".2%")),
        ("Sharpe", fnum(kpis["sharpe"], ".2f")),
        ("Calmar", fnum(kpis["calmar"], ".2f")),
    ]
    # 배포 기준일 때만 TE/IR 노출 (2026-08-19)
    if kpis.get("tracking_error") is not None:
        items += [("실현 TE", fnum(kpis["tracking_error"], ".2%")),
                  ("IR", fnum(kpis["info_ratio"], ".2f"))]
    items.append(("Funnel", kpis.get("funnel_pattern") or "-"))
    return "".join(
        f'<div class="kpi"><div class="kpi-label">{lbl}</div>'
        f'<div class="kpi-val">{val}</div></div>'
        for lbl, val in items
    )


# EW_All / EW_Top50 / Constrained EW 접두 지표는 EW/Top50/CEW 3열로 피벗한다.
# (Funnel Value-Add 의 CAGR/MDD 비교 — funnel 의 핵심이라 나란히 보여 가독성↑)
_DIAG_VARIANTS = [("Constrained EW ", "cew"), ("EW_Top50 ", "top50"), ("EW_All ", "ew")]


def _fmt_perf(v: float, is_pct: bool) -> str:
    """성과 값 포맷 (nan -> '-', 비율 -> %, 그 외 소수 2자리)."""
    if v != v:  # nan
        return "-"
    return f"{v:.2%}" if is_pct else f"{v:.2f}"


def _oos_rows(curves) -> list[dict]:
    """OOS 성과(CAGR/MDD/Sharpe/Calmar)를 EW/Top50/CEW 3열로 비교하는 진단 표용 행 리스트.

    walk_forward_results.csv 의 곡선에서 직접 계산 — overfit_diagnostics.csv 와 동일 공식
    (research §7.3 검증)이라 진단값과 일치하며, CSV 엔 없는 EW_All/EW_Top50 의 Sharpe/Calmar
    까지 메운다. EW=EW_All(전체 유효 팩터 동일가중), Top50=Top-50 후보군 동일가중, CEW=최종 MP.
    곡선/컬럼이 없으면 빈 리스트(호출부는 CSV 원본 표기로 폴백).
    """
    if curves is None:
        return []
    specs = [("ew", "ew_all_return", "ew_all_cumulative"),
             ("top50", "ew_top50_return", "ew_top50_cumulative"),
             ("cew", "cew_return", "cew_cumulative")]
    perf = {}
    for key, rc, cc in specs:
        if rc not in curves.columns or cc not in curves.columns:
            return []
        perf[key] = dd.compute_series_perf(curves, rc, cc)

    metrics = [("CAGR", "cagr", True), ("최대 낙폭 (Maximum Drawdown)", "mdd", True),
               ("Sharpe", "sharpe", False), ("Calmar", "calmar", False)]
    return [
        {"cat": f"OOS 성과 (EW/Top50/{STRAT_LABEL})", "metric": label, "single": None, "interp": "",
         "ew": _fmt_perf(perf["ew"][m], pct),
         "top50": _fmt_perf(perf["top50"][m], pct),
         "cew": _fmt_perf(perf["cew"][m], pct)}
        for label, m, pct in metrics
    ]


_OOS_CSV_CATS = ("OOS 성과 - Constrained EW", "OOS 성과 - EW")


def _diagnostics_table(output_dir: Path, curves=None, end_date=None) -> str:
    """overfit_diagnostics.csv 전체를 분류별로 묶은 다크 테마 HTML 표로 렌더(read-only).

    parse_diagnostics 는 KPI 추출용이라 Interpretation/행순서를 버린다. 전체 표는
    CSV(세로형 Category/Metric/Value/Interpretation)를 직접 읽어 순서대로 렌더한다.
    그 외 단일값 행은 3열을 colspan 으로 합쳐 가운데 표시한다.
    Interpretation 에 '<','>' (예: 'A < B < C')가 섞이므로 셀은 모두 escape 한다.

    curves(walk_forward_results.csv)가 있으면 OOS 성과를 곡선에서 직접 계산한 단일
    "OOS 성과" 블록(CAGR/MDD/Sharpe/Calmar x EW/Top50/CEW)으로 통합한다. funnel 이
    이미 OOS CAGR/MDD 를 보여주므로 중복을 막고자, 이때 funnel 의 EW_All/EW_Top50/
    Constrained EW 변형행과 CSV OOS 섹션은 숨긴다(패턴 판정 행은 유지). 곡선이 없으면
    (test 모드 등) 변형행을 그대로 EW/Top50/CEW 로 피벗하는 폴백을 쓴다.
    """
    p = latest(output_dir / "overfit_diagnostics.csv", end_date)
    if not p.exists():
        return ""
    df = pd.read_csv(p, encoding="utf-8-sig").fillna("")

    oos_rows = _oos_rows(curves)       # 곡선 기반 OOS 블록 (없으면 [])
    out_rows: list[dict] = []          # 순서 보존된 출력 행
    pivot_idx: dict[tuple, int] = {}   # (cat, base_metric) -> out_rows 인덱스
    oos_inserted = False
    for _, r in df.iterrows():
        cat = str(r["Category"]).strip()
        metric = str(r["Metric"]).strip()
        value = str(r["Value"]).strip()
        interp = str(r["Interpretation"]).strip()

        if oos_rows:
            # CSV OOS 섹션 위치에 곡선 기반 OOS 블록을 삽입하고 원본 행은 숨김
            if cat in _OOS_CSV_CATS:
                if not oos_inserted:
                    out_rows.extend(oos_rows)
                    oos_inserted = True
                continue
            # funnel 의 변형 CAGR/MDD 는 OOS 블록으로 흡수 -> 숨김 (패턴 행은 유지)
            if any(metric.startswith(pfx) for pfx, _ in _DIAG_VARIANTS):
                continue

        matched = next((v for v in _DIAG_VARIANTS if metric.startswith(v[0])), None)
        if matched:
            prefix, col = matched
            key = (cat, metric[len(prefix):].strip())  # base metric (CAGR/MDD)
            if key not in pivot_idx:
                pivot_idx[key] = len(out_rows)
                # 피벗 행은 EW/Top50/CEW 헤더가 자체 설명하므로 해석 생략
                out_rows.append({"cat": cat, "metric": key[1], "single": None,
                                 "ew": "", "top50": "", "cew": "", "interp": ""})
            out_rows[pivot_idx[key]][col] = value
        else:
            out_rows.append({"cat": cat, "metric": metric, "single": value, "interp": interp})

    if oos_rows and not oos_inserted:  # CSV 에 OOS 섹션이 없던 경우(구버전) 끝에 추가
        out_rows.extend(oos_rows)

    trs, last_cat = [], None
    for row in out_rows:
        cat_cell = escape(row["cat"]) if row["cat"] != last_cat else ""
        last_cat = row["cat"]
        if row["single"] is not None:
            val_cells = f'<td class="diag-val diag-span" colspan="3">{escape(row["single"])}</td>'
        else:
            val_cells = (
                f'<td class="diag-val">{escape(row["ew"])}</td>'
                f'<td class="diag-val">{escape(row["top50"])}</td>'
                f'<td class="diag-val">{escape(row["cew"])}</td>'
            )
        trs.append(
            f'<tr><td class="diag-cat">{cat_cell}</td>'
            f'<td>{escape(_expand_dd(row["metric"]))}</td>'
            f'{val_cells}'
            f'<td class="diag-interp">{escape(row["interp"])}</td></tr>'
        )
    return (
        '<div class="card full"><table class="diag-table">'
        f'<thead><tr><th>분류</th><th>지표</th><th>EW</th><th>Top50</th><th>{STRAT_LABEL}</th><th>해석</th></tr></thead>'
        f'<tbody>{"".join(trs)}</tbody></table></div>'
    )


def _expand_dd(s: str) -> str:
    """지표 표기의 DD 약어를 전체 표기로 (2026-08-28 사용자 지정 — 대시보드 표시 전용)."""
    return re.sub(r"\bMDD\b", "최대 낙폭 (Maximum Drawdown)", s)


_DD_CURVE_SPECS = [("EW(전체)", "ew_all_cumulative"),
                   ("Top50", "ew_top50_cumulative"),
                   ("{strat}(최종)", "cew_cumulative")]


def _dd_episode_row(e: dict) -> str:
    def ym(d):
        return d.strftime("%Y-%m") if d is not None else "ONGOING"

    def mo(v):
        return f"{v}m" if v is not None else "ONGOING"

    # diag-val(우측 정렬)을 쓰면 좌측 정렬 헤더와 어긋나 값이 오른쪽으로 치우쳐
    # 보임 -> 낙폭 표는 헤더와 같은 좌측 정렬의 dd-num 사용 (2026-08-27)
    return (
        f'<tr><td class="dd-num">{e["depth"]:.2%}</td>'
        f'<td>{ym(e["peak"])}</td><td>{ym(e["trough"])}</td>'
        f'<td class="dd-num">{mo(e["peak_to_trough"])}</td>'
        f'<td>{ym(e["recovery"])}</td>'
        f'<td class="dd-num">{mo(e["trough_to_recovery"])}</td>'
        f'<td class="dd-num">{mo(e["total"])}</td></tr>'
    )


def _contrib_table(rows: list[dict], total_label: str = "기여 합 (%p)",
                   name_map: dict | None = None, style_map: dict | None = None,
                   desc_map: dict | None = None) -> str:
    """연도별 상위/하위 기여 팩터 표 (성과 원천 섹션, %p).

    합 열은 팩터 숫자들의 총합 — 실측 net 기준으로 잔차까지 배분된 경우
    total_label='실측 net 합' 로 실제 계좌 수익과 일치한다 (2026-08-29).
    팩터는 한 줄씩 — 약어 + 기여 + 스타일 칩 + 전체 팩터명, 다음 줄에 1줄
    한글 설명 (desc_map, 2026-08-28 사용자 지정; 미등재 팩터는 설명 생략).
    """
    from service.report.style_colors import STYLE_COLORS, _DEFAULT_COLOR
    nmap, smap, dmap = name_map or {}, style_map or {}, desc_map or {}

    def fline(f: str, v: float) -> str:
        sty = smap.get(f, "")
        c = STYLE_COLORS.get(sty, _DEFAULT_COLOR)
        chip = (f' <span class="chip" style="background:{c}22;color:{c}">{escape(sty)}</span>'
                if sty else '')
        nm = nmap.get(f, "")
        name = f' <span class="contrib-name">{escape(nm)}</span>' if nm and nm != f else ''
        d = dmap.get(f, "")
        desc = f'<div class="contrib-desc">{escape(d)}</div>' if d else ''
        return (f'<div class="contrib-line"><b>{escape(f)}</b> '
                f'<span class="dd-num">{v:+.2f}</span>{chip}{name}{desc}</div>')

    trs = []
    for r in rows:
        top = "".join(fline(f, v) for f, v in r["top"])
        bot = "".join(fline(f, v) for f, v in r["bottom"])
        trs.append(
            f'<tr><td class="diag-cat">{r["year"]}</td>'
            f'<td class="dd-num">{r["total"]:+.2f}</td>'
            f'<td>{top}</td><td>{bot}</td></tr>'
        )
    # 고정 레이아웃 + 좁은 수치 열 -> 팩터 열이 폭을 가져가 팩터당 1~2줄 유지
    return (
        '<div class="card full"><table class="diag-table contrib-table">'
        '<colgroup><col style="width:6%"><col style="width:10%"><col><col></colgroup>'
        '<thead><tr>'
        f'<th>연도</th><th>{total_label}</th><th>상위 기여 팩터</th><th>하위 기여 팩터</th>'
        '</tr></thead><tbody>' + "".join(trs) + '</tbody></table></div>'
    )


def _drawdown_episodes_section(curves) -> tuple[str, str]:
    """곡선별 낙폭 episode(0.5% 이상) 표. (본전략 HTML, 비교곡선 HTML) 튜플 —
    본전략만 기본 표시하고 EW 계열은 접힘용으로 분리 (2026-08-28 사용자 지정)."""
    main, others = [], []
    for label, cum_col in _DD_CURVE_SPECS:
        label = label.format(strat=STRAT_LABEL)
        if cum_col not in curves.columns:
            continue
        eps = dd.compute_drawdown_episodes(curves[cum_col], min_depth=0.005)
        if not eps:
            continue
        mdd = min(e["depth"] for e in eps)
        rows = "".join(_dd_episode_row(e) for e in eps)
        block = (
            '<div class="card full">'
            f'<div class="dd-cap">{escape(label)} - {len(eps)} episodes, '
            f'최대 낙폭 (Maximum Drawdown) {mdd:.2%}</div>'
            '<table class="diag-table"><thead><tr>'
            '<th>낙폭 (drawdown)</th><th>peak</th><th>trough</th><th>peak→trough</th>'
            '<th>recovery</th><th>trough→recovery</th><th>total</th>'
            '</tr></thead><tbody>'
            f'{rows}</tbody></table></div>'
        )
        (main if cum_col == "cew_cumulative" else others).append(block)
    return "".join(main), "".join(others)


def _is_label() -> tuple[str, str]:
    """학습(IS) 표기 (제목용, 본문용). 롤링 창이면 '롤링 N개월', 아니면
    'YYYY-MM부터 확장창' (config backtest_start)."""
    w = PIPELINE_PARAMS.get("is_window_months")
    if w:
        return f"학습 롤링 {int(w)}개월", f"학습(IS) 롤링 {int(w)}개월 창"
    start = pd.Timestamp(PIPELINE_PARAMS["backtest_start"]).strftime("%Y-%m")
    return f"학습 {start}~ 확장창", f"학습(IS) {start}부터 확장창"


def _backtest_stats_card(curves) -> str:
    """확장 성과 통계(QC Key Statistics 스타일) + 벤치마크(선정 EW) 대비 지표를 KPI 그리드로."""
    s = dd.extended_stats(curves)
    rel = dd.relative_metrics(curves, bench_col="ew_return")

    def pct(v):
        return "-" if v != v else f"{v:.2%}"

    def dec(v):
        return "-" if v != v else f"{v:.2f}"

    items = [
        ("연환산 변동성", pct(s["ann_vol"])),
        ("Sortino", dec(s["sortino"])),
        ("최고 월", pct(s["best_month"])),
        ("최저 월", pct(s["worst_month"])),
        ("상승월 비율", pct(s["pct_positive"])),
        ("평균 월수익", pct(s["avg_month"])),
        ("월수익 왜도", dec(s["skew"])),
        ("최장 연속손실", f'{s["max_loss_streak"]}M'),
    ]
    if rel:
        items += [
            ("Beta (vs 선정EW)", dec(rel["beta"])),
            ("Alpha 연 (vs 선정EW)", pct(rel["alpha_ann"])),
            ("정보비율", dec(rel["info_ratio"])),
            ("추적오차 연", pct(rel["tracking_error"])),
        ]
    cells = "".join(
        f'<div class="kpi"><div class="kpi-label">{escape(lbl)}</div>'
        f'<div class="kpi-val">{escape(val)}</div></div>'
        for lbl, val in items
    )
    return f'<div class="kpi-grid">{cells}</div>'


def _vol_regime_note(summary: dict) -> str:
    """변동성 국면 요약 텍스트 - Bloomberg multiplier/TE 타깃 정성 참고용 설명."""
    text = (
        f'현재 실현변동성 {summary["realized_vol"]:.1%} '
        f'(역대 {summary["percentile"]:.0%} 백분위, '
        f'레인지 {summary["min_vol"]:.1%}~{summary["max_vol"]:.1%}), '
        f'중위 {summary["median_vol"]:.1%}, '
        f'참고 배수 k={summary["k"]:.2f} (cap {summary["k_cap"]:.1f}). '
        'Bloomberg multiplier/TE 타깃 정할 때 정성 참고용 - 예: 평소 TE 타깃 x k'
    )
    return escape(text)


def _vol_regime_section(curves) -> str:
    """변동성 국면 차트 + 요약 카드 (행 부족 등으로 계산 불가하면 빈 문자열 - 섹션 생략)."""
    result = dd.build_vol_regime(curves)
    if result is None:
        return ""
    vr_df, summary = result
    return (
        f'<div class="card full">{_fig_div(ch.build_vol_regime_chart(vr_df))}</div>'
        f'<div class="card full"><div class="note">{_vol_regime_note(summary)}</div></div>'
    )


def _deployed_curves(curves: pd.DataFrame, series: pd.DataFrame) -> pd.DataFrame:
    """곡선을 배포 기준(실제 적용 규모)으로 환산한다 (2026-08-19).

    - 전략 곡선(cew): 종목단 재구성 실측 net_return 으로 **교체** (netting 반영 —
      배수만 곱한 근사가 아니라 실제 배포 북의 수익).
    - 비교 곡선(EW 계열): 같은 월별 배수 경로를 곱해 축을 맞춘다 (근사 — 이들은
      종목단 netting 을 측정하지 않으므로 상대 비교 용도로만 유효).
    """
    mult = series["multiplier"].reindex(curves.index)
    out = curves.copy()
    out["cew_return"] = series["net_return"].reindex(curves.index)
    # 목표 노출(Gross Active Risk) 궤적 — 시점별 조정 이력을 곡선 옆에 보존
    if "target_gross" in series.columns:
        out["target_gross"] = series["target_gross"].reindex(curves.index)
    for c in ("ew_return", "ew_all_return", "ew_top50_return"):
        if c in out.columns:
            out[c] = out[c] * mult
    out = out.dropna(subset=["cew_return"])
    for rc, cc in (("cew_return", "cew_cumulative"), ("ew_return", "ew_cumulative"),
                   ("ew_all_return", "ew_all_cumulative"),
                   ("ew_top50_return", "ew_top50_cumulative")):
        if rc in out.columns and cc in out.columns:
            out[cc] = (1 + out[rc].fillna(0)).cumprod()
    return out


def _attach_benchmark(curves: pd.DataFrame) -> pd.DataFrame:
    """BM 월수익(data/{BENCHMARK}/bm_returns.csv)을 붙이고 BM / BM+MP 오버레이 누적을 만든다 (2026-08-21).

    MP 는 시장중립 오버레이이므로 실제 운용 수익 = BM + MP. 파일이 없으면 무동작.
    """
    path = dd.UNIVERSE_DATA_DIR / "bm_returns.csv"
    if not path.exists() or "cew_return" not in curves.columns:
        return curves
    bm = pd.read_csv(path, parse_dates=["date"]).set_index("date")["bm_return"]
    out = curves.copy()
    out["bm_return"] = bm.reindex(out.index)
    if out["bm_return"].isna().all():
        return curves
    out["bm_mp_return"] = out["bm_return"] + out["cew_return"]
    out["bm_cumulative"] = (1 + out["bm_return"].fillna(0)).cumprod()
    out["bm_mp_cumulative"] = (1 + out["bm_mp_return"].fillna(0)).cumprod()
    return out


def _load_deploy_series(output_dir: Path, end_date=None) -> pd.DataFrame | None:
    """배포 기준 종목단 시계열 로드. 없으면 None (구 산출물에서도 대시보드 유지)."""
    path = latest(output_dir / "stock_level_series.csv", end_date)
    if not path.exists():
        return None
    df = pd.read_csv(path, parse_dates=["date"]).set_index("date")
    return df if not df.empty else None


def _deploy_section(output_dir: Path, end_date=None) -> str:
    """노출 · 적용 배수 · 실현 TE 섹션 (2026-08-19). 성과 KPI 는 메인 섹션이 담당."""
    df = _load_deploy_series(output_dir, end_date)
    if df is None:
        return ""
    tgt = float(df["long_exposure"].median() * 2)
    fixed_flag = df["long_exposure"].round(6).nunique() == 1
    note = (
        f"롱 +{df['long_exposure'].mean():.1%} / 숏 -{df['long_exposure'].mean():.1%} "
        f"(목표 gross {tgt:.0%}{' · 매월 정확히 고정됨' if fixed_flag else ''}) · "
        f"적용 배수 {df['multiplier'].min():.3f}~{df['multiplier'].max():.3f} "
        f"(중앙 {df['multiplier'].median():.3f}) · "
        f"배수 전 book gross {df['book_gross_before'].min():.0%}~{df['book_gross_before'].max():.0%} "
        "— 배수가 이 변동을 흡수해 노출을 고정한다."
    )
    # 실현 TE 차트는 백테스트 섹션(월별 수익 분포 아래)으로 이동 (2026-08-26)
    return (
        '<h2>노출 · 적용 배수</h2>'
        f'<div class="card full"><div class="note">{note}</div></div>'
        f'<div class="card full">{_fig_div(ch.deploy_exposure_fig(df))}</div>'
    )


def _build_backtest_section(output_dir: Path, end_date=None) -> tuple[list[str], bool]:
    """백테스트 섹션 HTML 조각 리스트와 'plotly.js 포함 여부' 반환."""
    wf_path = latest(output_dir / "walk_forward_results.csv", end_date)
    if not wf_path.exists():
        return (['<div class="note">walk_forward_results.csv 없음 - 백테스트 섹션 생략. '
                 '(python main.py backtest ... 실행 필요)</div>'], [], False)

    curves = dd.load_backtest_curves(wf_path)
    diag = dd.parse_diagnostics(latest(output_dir / "overfit_diagnostics.csv", end_date))

    # 배포 기준 전환 (2026-08-19): 종목단 실측 시계열이 있으면 곡선을 실제 적용
    # 규모로 환산한다 -> KPI/수익곡선/낙폭/히트맵/롤링Sharpe/낙폭구간이 모두 따라감.
    # diag(과적합 진단 파일)는 factor-level 미스케일 값이라 KPI 오버라이드에서 제외.
    deploy = _load_deploy_series(output_dir, end_date)
    if deploy is not None:
        curves = _deployed_curves(curves, deploy)
        curves = _attach_benchmark(curves)
        kpis = dd.build_kpis(curves, None)
        r = curves["cew_return"].dropna()
        te = float(r.std() * (12 ** 0.5))
        kpis["tracking_error"] = te
        kpis["info_ratio"] = (kpis["cagr"] / te) if te else float("nan")
        kpis["exposure"] = float(deploy["long_exposure"].mean())
    else:
        kpis = dd.build_kpis(curves, diag)

    start = curves.index.min().strftime("%Y-%m")
    end = curves.index.max().strftime("%Y-%m")
    is_title, is_body = _is_label()
    title_range = f"{is_title} · OOS {start}~{end}" if is_title else f"OOS {start}~{end}"
    is_note = (f"{is_body} · 성과는 OOS {start}~ 집계 · " if is_body else "")
    parts = [
        f'<h2>1. 백테스트 — 배포 기준 ({title_range}, OOS {kpis["n_months"]}개월)</h2>'
        if deploy is not None else
        f'<h2>1. 백테스트 ({title_range}, OOS {kpis["n_months"]}개월)</h2>',
        f'<div class="kpi-grid">{_kpi_cards(kpis)}</div>',
        # 집계 기준 + 상세 통계 카드 전체를 기본 숨김 — summary 는 화살표만
        # (2026-08-07 사용자 지정)
        f'<details class="note-toggle"><summary></summary>'
        f'<div class="note">{is_note}상세 통계/벤치마크 = 선정 EW(1/N)'
        + (f' · <b>배포 기준</b>: 전략 곡선은 종목단 재구성 실측(롱 +{kpis["exposure"]:.1%}/숏 -{kpis["exposure"]:.1%}), '
           '비교 곡선(EW 계열)은 동일 배수 경로를 곱한 근사' if deploy is not None else '')
        + '</div>'
        f'{_backtest_stats_card(curves)}</details>',
        f'<div class="card full">{_fig_div(ch.equity_curve_fig(curves), include_js=True)}</div>',
        # 동일 곡선 + YTD 수익 기준 음영 버전 (2026-08-28 사용자 지정)
        f'<div class="card full">{_fig_div(ch.equity_curve_fig(curves, shade="ytd"))}</div>',
    ]
    # 월별 수익률 히트맵 (QC 스타일 연x월 그리드)
    mret = dd.monthly_returns_table(curves)
    if not mret.empty:
        parts.append(f'<div class="card full">{_fig_div(ch.monthly_returns_heatmap_fig(mret))}</div>')

    # 차트 순서 (2026-08-28 사용자 지정 2차): 스타일 비중 추이는 롤링 Sharpe
    # 아래, 낙폭은 팩터 회전율 아래.
    wh_path = latest(output_dir / "walk_forward_weight_history.csv", end_date)
    wh_charts = None
    if wh_path.exists():
        wh = dd.load_weight_history(wh_path)
        fi_path = dd.DATA_DIR / "factor_info.csv"
        fmap = dd.factor_style_map(fi_path) if fi_path.exists() else {}
        style_hist = dd.style_weight_history(wh, fmap)
        turnover = dd.compute_turnover(wh)
        churn_split = dd.selection_churn_split(wh)
        wh_charts = (style_hist, turnover, churn_split)

    parts.append(_grid2([
        f'<div class="card">{_fig_div(ch.monthly_dist_fig(curves))}</div>',
    ]))
    # 실현 TE — 월별 수익 분포 아래 (2026-08-26 사용자 지정, 배포 기준일 때만 의미)
    r = curves["cew_return"].dropna()
    if deploy is not None and len(r) >= 12:
        parts.append(f'<div class="card full">{_fig_div(ch.rolling_te_fig(r))}</div>')
    # 롤링 Sharpe 12/24/36/48개월 (>=12개월 구간에서만)
    if len(r) >= 12:
        parts.append(f'<div class="card full">{_fig_div(ch.rolling_sharpe_fig(r))}</div>')

    if wh_charts is not None:
        style_hist, turnover, churn_split = wh_charts
        parts.append(f'<div class="card full">{_fig_div(ch.style_weight_evolution_fig(style_hist))}</div>')
        parts.append(f'<div class="card full">{_fig_div(ch.turnover_fig(turnover, churn_split))}</div>')
    # 낙폭 차트 — 팩터 회전율 아래 (2026-08-28 사용자 지정)
    parts.append(f'<div class="card full">{_fig_div(ch.drawdown_fig(curves))}</div>')

    dd_main, dd_others = _drawdown_episodes_section(curves)
    if dd_main:
        parts.append('<h2>낙폭 구간 분석 (0.5% 이상 episode, 깊은 순)</h2>')
        parts.append(dd_main)

    # 연도별 성과 원천 (팩터 기여) — 2026-08-28 사용자 지정 상설화.
    # 엔진이 '당시 규칙' 기준으로 기록한 정확 분해 (행합 = cew_return, 팩터단).
    contrib_path = latest(output_dir / "factor_contrib.csv", end_date)
    if contrib_path.exists():
        contrib = dd.load_factor_contrib(contrib_path)
        fi_path = dd.DATA_DIR / "factor_info.csv"
        fmap = dd.factor_style_map(fi_path) if fi_path.exists() else {}
        # 실측 net 기준 통일 (2026-08-29 사용자 지정): 월별 실측 배수를 곱해 배포
        # 규모로 스케일한 뒤, 종목단 실비용·세금 잔차(연 -0.03~-0.37%p)까지 그 달의
        # 팩터 비중 비례로 배분 — 표·히트맵의 팩터 숫자 합 = 실측 net 과 정확히
        # 일치한다 (비용은 포지션 크기에 비례한다는 배분 규칙).
        basis = "팩터단 — 배포 배수 적용 전"
        total_label = "기여 합 (%p)"
        if deploy is not None and "multiplier" in deploy.columns:
            m = deploy["multiplier"].reindex(contrib.index)
            wh_p = latest(output_dir / "walk_forward_weight_history.csv", end_date)
            if m.notna().all() and wh_p.exists():
                contrib = contrib.mul(m, axis=0)
                resid = deploy["net_return"].reindex(contrib.index) - contrib.sum(axis=1)
                w = (dd.load_weight_history(wh_p)
                     .reindex(index=contrib.index, columns=contrib.columns)
                     .apply(pd.to_numeric, errors="coerce").fillna(0.0))
                wnorm = w.div(w.sum(axis=1).replace(0.0, 1.0), axis=0)
                # 일반 덧셈 -> 미보유 월(NaN)은 NaN 유지 (배분은 보유 팩터에만)
                contrib = contrib + wnorm.mul(resid, axis=0)
                basis = "실측 net 기준"
                total_label = "실측 net 합 (%p)"
        parts.append('<h2>연도별 성과 원천 (팩터 기여)</h2>')
        parts.append(f'<div class="note">비중 × 당월 팩터수익의 정확 분해 (각 달의 실제 운용 규칙 기준) · '
                     f'{basis}' + (' — 실측 배수 적용 + 실비용·세금 잔차를 팩터 비중 비례 배분, '
                                   '팩터 숫자의 합 = 실제 계좌 수익' if basis == "실측 net 기준" else '') + '</div>')
        parts.append(f'<div class="card full">'
                     f'{_fig_div(ch.contrib_style_heatmap_fig(dd.contrib_style_yearly(contrib, fmap), basis.split(" — ")[0]))}</div>')
        nmap = dd.factor_name_map(fi_path) if fi_path.exists() else {}
        desc_path = dd.DATA_DIR / "factor_desc_kr.csv"
        dmap = dd.factor_desc_map(desc_path) if desc_path.exists() else {}
        parts.append(_contrib_table(dd.contrib_top_factors_yearly(contrib),
                                    total_label=total_label,
                                    name_map=nmap, style_map=fmap, desc_map=dmap))

    # 접힌 항목은 페이지 맨 아래로 모은다 (2026-08-28 사용자 지정)
    folded: list[str] = []
    vol_regime_section = _vol_regime_section(curves)
    if vol_regime_section:
        folded.append(_collapsible('변동성 국면 (multiplier 참고)', vol_regime_section))
    diag_tbl = _diagnostics_table(output_dir, curves, end_date)
    if diag_tbl:
        folded.append(_collapsible('과적합 진단 상세', diag_tbl))
    if dd_others:
        folded.append(_collapsible('낙폭 구간 분석 — 비교 곡선 (전체 EW · Top50 EW)', dd_others))

    return parts, folded, True


def _build_portfolio_section(output_dir: Path, end_date: str | None,
                             js_already: bool, data_dir: Path) -> tuple[list[str], list[str]]:
    weights_path = dd.find_latest_weights_file(output_dir, end_date)
    if weights_path is None:
        return (['<h2>2. 현재 포트 / 배팅</h2>'
                 '<div class="note">weights_factor_*.csv 없음 - '
                 '현재 포트 섹션 생략. (python main.py mp ... 실행 필요)</div>'], [])

    weights = dd.load_weights(weights_path)
    snap = dd.snapshot_date_from_path(weights_path) or "?"
    deltas = dd.load_style_deltas(output_dir, snap)
    style_w = dd.style_allocation(weights, deltas)
    # ISIN 기준 상하위 20종목 (ticker 는 결측 종목이 빠짐, 2026-08-26 사용자 지정)
    ls_df = dd.top_longs_shorts(weights, n=20, id_col="isin")
    tilt = dd.factor_tilt(weights)
    selected = dd.active_factors(weights)

    style_cap = float(PIPELINE_PARAMS["style_cap"])

    cards = [
        f'<div class="card">{_fig_div(ch.style_allocation_fig(style_w, style_cap), include_js=not js_already)}</div>'
    ]

    # 섹터 분해: 소스 parquet 을 read-only 로 읽어 gvkeyiid 로 join (파이프라인 무수정)
    if snap != "?":
        sector_map = dd.load_sector_map(data_dir, snap)
        if sector_map:
            sec_series = dd.sector_net_weights(weights, sector_map)
            if not sec_series.empty:
                sec_decomp = dd.sector_style_decomposition(weights, sector_map)
                cards.append(f'<div class="card">{_fig_div(ch.sector_net_fig(sec_series, sec_decomp))}</div>')

    ls_decomp = dd.longs_shorts_style_decomposition(weights, n=20, id_col="isin")
    cards.append(f'<div class="card">{_fig_div(ch.longs_shorts_fig(ls_df, ls_decomp))}</div>')
    # 팩터 틸트 차트는 제거하고 그 자리에 전월 대비 팩터 비중 변화 (2026-08-28
    # 사용자 지정 — 틸트는 3번 섹션 ERC 비중 순위 표와 내용 중복). 직전 스냅샷이
    # 없으면 스타일 단위 델타로 폴백.
    fd = dd.factor_delta_decomposition(output_dir, snap) if snap != "?" else None
    if fd is not None:
        fdf, prev_snap = fd
        cards.append(f'<div class="card">{_fig_div(ch.factor_delta_fig(fdf, prev_snap, snap))}</div>')
    elif deltas is not None and not deltas.empty:
        cards.append(f'<div class="card">{_fig_div(ch.style_delta_fig(deltas))}</div>')

    meta_path = latest(output_dir / "meta_data.csv", end_date)
    if meta_path.exists():
        meta = dd.load_meta(meta_path)
        cards.append(f'<div class="card">{_fig_div(ch.leaderboard_fig(meta, selected, tilt))}</div>')

    parts = [f'<h2>2. 현재 포트 / 배팅 (스냅샷 {snap})</h2>', _grid2(cards)]
    folded: list[str] = []

    clusters_main, clusters_folded = _factor_clusters_section(output_dir, snap)
    if clusters_main:
        parts.append(clusters_main)
    if clusters_folded:
        folded.append(clusters_folded)
    cap_html = _style_cap_section(output_dir, snap)
    if cap_html:
        parts.append(cap_html)
    regime_html = _correlation_regime_section(output_dir, end_date)
    if regime_html:
        folded.append(regime_html)
    return parts, folded


def _correlation_regime_section(output_dir: Path, end_date=None) -> str:
    """상관 국면 참고 섹션 (multiplier 참고용 — 자동 스케일링에 미사용).

    factor_returns_matrix.csv (walk-forward 저장) 기반:
      - 평균 쌍상관: rolling 12M 팩터 간 평균 상관 (급등 = 매크로 쏠림 장세)
      - 흡수률: rolling 12M cov 상위 5 고유값의 분산 설명 비중 (Kritzman 계열)
    CEW 연수익 음수인 해는 음영 처리해 국면 지표와 죽은 해의 겹침을 보여준다.
    """
    path = latest(output_dir / "factor_returns_matrix.csv", end_date)
    if not path.exists():
        return ""
    rets = pd.read_csv(path, index_col=0, parse_dates=True)
    rets = rets.iloc[1:]  # 기준점 0 행 제외
    if len(rets) < 24:
        return ""

    import numpy as np
    win = 12
    dates, mean_corr, absorption = [], [], []
    for i in range(win, len(rets) + 1):
        w = rets.iloc[i - win:i]
        w = w.loc[:, w.notna().all() & (w.std() > 0)]
        if w.shape[1] < 10:
            continue
        c = np.corrcoef(w.to_numpy(), rowvar=False)
        n = c.shape[0]
        mean_corr.append((c.sum() - n) / (n * (n - 1)))
        ev = np.linalg.eigvalsh(np.cov(w.to_numpy(), rowvar=False))
        absorption.append(float(ev[-5:].sum() / ev.sum()))
        dates.append(w.index[-1])
    if len(dates) < 12:
        return ""

    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.06,
                        row_heights=[0.62, 0.38],
                        specs=[[{"secondary_y": True}], [{}]])
    fig.add_trace(go.Scatter(x=dates, y=mean_corr, name="평균 쌍상관 (12M)",
                             line=dict(color="#5B8DEF", width=2)),
                  row=1, col=1, secondary_y=False)
    fig.add_trace(go.Scatter(x=dates, y=absorption, name="흡수률 (상위 5 고유값, 12M)",
                             line=dict(color="#E8944A", width=2)),
                  row=1, col=1, secondary_y=True)

    # 하단: 월별 전략(CEW) 수익률 바 + 연수익 음수 해 음영 (두 행 공통)
    wf = latest(output_dir / "walk_forward_results.csv", end_date)
    if wf.exists():
        cew = pd.read_csv(wf, index_col=0, parse_dates=True)["cew_return"].dropna()
        fig.add_trace(go.Bar(
            x=cew.index, y=cew * 100, name=f"{STRAT_LABEL} 월수익률(%)",
            marker_color=["#4FBF87" if v >= 0 else "#E06C75" for v in cew],
        ), row=2, col=1)
        yearly = cew.groupby(cew.index.year).apply(lambda x: (1 + x).prod() - 1)
        for yr, r in yearly.items():
            if r < 0:
                fig.add_vrect(x0=f"{yr}-01-01", x1=f"{yr}-12-31",
                              fillcolor="#E06C75", opacity=0.07, line_width=0,
                              row="all", col=1)

    fig.update_layout(template="plotly_dark", height=460,
                      # 다른 차트(_BASE_LAYOUT)와 동일 폰트 — 미지정 시 Open Sans 로 어긋남
                      font=dict(color="#eaecef", size=12,
                                family="Inter, -apple-system, 'Segoe UI', Roboto, sans-serif"),
                      barcornerradius=4,  # 막대 모서리/테두리 통일
                      margin=dict(l=40, r=40, t=30, b=30),
                      legend=dict(orientation="h", y=1.1), showlegend=True,
                      bargap=0.15,
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    fig.update_yaxes(title_text="평균 상관", row=1, col=1, secondary_y=False)
    fig.update_yaxes(title_text="흡수률", row=1, col=1, secondary_y=True)
    fig.update_yaxes(title_text="월수익률 %", row=2, col=1)

    note = ('<div class="note">multiplier 참고 지표 (자동 스케일링 미사용 — 변동성 국면 섹션과 동일 지위). '
            '상단: 평균 상관·흡수률 급등 = 팩터가 한 방향으로 쓸리는 매크로 장세로 L/S 분산 효과 약화. '
            f'하단: {STRAT_LABEL} 월수익률 (OOS 시작 2018-06 이후). '
            f'붉은 음영 = {STRAT_LABEL} 연수익 음수인 해. '
            '데이터: walk-forward 전기간 팩터 수익률 (마지막 IS 규칙 기준, 상관 구조 참고용).</div>')
    return _collapsible('상관 국면 (multiplier 참고)',
                        f'{note}<div class="card">{_fig_div(fig)}</div>')


def _style_cap_section(output_dir: Path, snap: str) -> str:
    """스타일 캡 25% 적용 전/후 스타일 배분 비교 (mp 저장 style_cap_effect CSV 기반)."""
    path = output_dir / "mp_weight_history" / f"style_cap_effect_{snap}.csv"
    if not path.exists():
        return ""
    df = pd.read_csv(path)
    if df.empty or (df["raw_weight"] - df["fitted_weight"]).abs().max() < 1e-12:
        return ""  # 캡 미발동(전후 동일)이면 생략

    style_cap = float(PIPELINE_PARAMS["style_cap"])

    g = df.groupby("styleName")[["raw_weight", "fitted_weight"]].sum()
    g = g.sort_values("raw_weight", ascending=False)
    scale = max(g["raw_weight"].max(), g["fitted_weight"].max(), style_cap) * 1.15

    rows = []
    for style, r in g.iterrows():
        pre, post = r["raw_weight"], r["fitted_weight"]
        delta = post - pre
        capped = pre > style_cap + 1e-9
        tag = ('<span class="capchip cut">캡 발동</span>' if capped
               else ('<span class="capchip up">재분배 수혜</span>' if delta > 1e-9 else ""))
        # 캡 발동 스타일은 post ~= cap 인데 부동소수 오차로 캡선을 1~2px 넘어 보일
        # 수 있어 시각 폭만 캡선에 클램프한다 (수치 표기는 원값 유지)
        post_w = min(post, style_cap)
        rows.append(
            f'<tr><td>{style} {tag}</td>'
            f'<td class="num">{pre*100:.2f}%</td>'
            f'<td class="capcell"><div class="capbars">'
            f'<div class="pre" style="width:{pre/scale*100:.2f}%"></div>'
            f'<div class="post" style="width:{post_w/scale*100:.2f}%"></div>'
            f'<div class="capline" style="left:{style_cap/scale*100:.2f}%"></div></div></td>'
            f'<td class="num">{post*100:.2f}%</td>'
            f'<td class="num" style="color:{"#E06C75" if delta < -1e-9 else "#4FBF87" if delta > 1e-9 else "inherit"}">'
            f'{delta*100:+.2f}%p</td></tr>'
        )

    css = (
        # 1번 섹션 진단 표(.diag-table)와 동일한 헤더/구분선 스타일
        '<style>.cap-table{width:100%;border-collapse:collapse;font-size:13px}'
        '.cap-table th{text-align:left;color:var(--muted);font-weight:600;font-size:12px;'
        'border-bottom:1px solid var(--hair);padding:8px 10px}'
        '.cap-table td{padding:7px 10px;border-bottom:1px solid var(--hair)}'
        '.cap-table tbody tr:last-child td{border-bottom:none}'
        '.cap-table tbody tr:hover td{background:var(--elev)}'
        '.cap-table .num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap;'
        'font-family:var(--mono);font-size:12px}'
        # td 에 직접 absolute 를 걸면 셀 패딩 박스 기준이 돼 쌍 내 간격(16px)이 행 간
        # 간격(5px)보다 넓어지는 버그 -> 내부 래퍼 div 기준으로 전/후 바를 붙인다
        '.capcell{width:42%}'
        '.capbars{position:relative;height:22px}'
        '.capbars .pre{position:absolute;top:2px;height:8px;background:#5B8DEF55;border-radius:3px}'
        '.capbars .post{position:absolute;bottom:2px;height:8px;background:#5B8DEF;border-radius:3px}'
        '.capbars .capline{position:absolute;top:-2px;bottom:-2px;width:2px;margin-left:-1px;'
        'background:#E06C75;opacity:.9}'
        '.capchip{padding:1px 6px;border-radius:8px;font-size:10px;font-weight:600;margin-left:4px;white-space:nowrap}'
        '.capchip.cut{background:#E06C7522;color:#E06C75}'
        '.capchip.up{background:#4FBF8722;color:#4FBF87}</style>'
    )
    note = (f'<div class="note">위 바(연한색)=캡 적용 전 ERC 원비중, 아래 바(진한색)=캡 적용 후, '
            f'빨간 선=캡 {style_cap*100:.0f}%. 캡 초과 스타일의 초과분이 나머지 스타일로 재분배된다.</div>')
    header = ('<tr><th>스타일</th><th style="text-align:right">캡 전</th><th></th>'
              '<th style="text-align:right">캡 후</th><th style="text-align:right">변화</th></tr>')
    return (f'<h2>4. 스타일 캡 {style_cap*100:.0f}% 적용 전/후</h2>{css}{note}'
            f'<div class="card full"><table class="cap-table"><thead>{header}</thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def _factor_clusters_section(output_dir: Path, snap: str) -> tuple[str, str]:
    """ERC 상관 무리 섹션: 어떤 팩터들이 한 배팅으로 묶였고 예산을 어떻게 나눴는지.

    mp 가 저장한 mp_weight_history/factor_clusters_{snap}.csv 기반 (없으면 생략).
    """
    path = output_dir / "mp_weight_history" / f"factor_clusters_{snap}.csv"
    if not path.exists():
        return "", ""
    df = pd.read_csv(path)
    if df.empty:
        return "", ""

    # 스타일 색 = 단일 출처 (차트/PDF 와 동일 매핑 — 등장 순서 무관 고정색)
    from service.report.style_colors import STYLE_COLORS, _DEFAULT_COLOR
    style_colors = {s: STYLE_COLORS.get(s, _DEFAULT_COLOR)
                    for s in df["styleName"].unique()}

    # 약어 -> 풀네임 (factor_info.csv, 없으면 약어만 표시)
    fi_path = dd.DATA_DIR / "factor_info.csv"
    full_names = {}
    if fi_path.exists():
        fi = pd.read_csv(fi_path)
        full_names = dict(zip(fi["factorAbbreviation"], fi["factorName"]))

    def _factor_cell(abbr: str) -> str:
        name = full_names.get(abbr, "")
        suffix = f' <span class="fullname">({escape(name)})</span>' if name else ""
        return (f'<td class="mono" title="{escape(abbr)}{" - " + escape(name) if name else ""}">'
                f'{escape(abbr)}{suffix}</td>')

    # 캡 전 ERC 원비중 (style_cap_effect CSV). 클러스터 CSV 의 weight 는 캡 적용
    # 후 최종 명목 비중(fitted_weight 와 일치)이라, 두 값을 나란히 보여준다
    # (2026-08-27 사용자 지정 — 구 헤더 "ERC 비중" 은 오표기였음)
    cap_path = output_dir / "mp_weight_history" / f"style_cap_effect_{snap}.csv"
    raw_map: dict = {}
    if cap_path.exists():
        capdf = pd.read_csv(cap_path)
        raw_map = dict(zip(capdf["factor"], capdf["raw_weight"]))

    # 전월 대비 증감 (캡 후 기준 — '캡 후' 컬럼과 동일 기준, 직전 파일 없으면 컬럼 생략)
    fd = dd.factor_delta_decomposition(output_dir, snap)
    delta_map: dict = dict(zip(fd[0]["factor"], fd[0]["delta"])) if fd is not None else {}
    has_delta = fd is not None

    def _delta_cell(factor: str) -> str:
        if not has_delta:
            return ''
        v = delta_map.get(factor, 0.0)
        color = "#4FBF87" if v > 1e-9 else "#E06C75" if v < -1e-9 else "var(--muted)"
        return f'<td class="num" style="color:{color}">{v*100:+.2f}%p</td>'

    def _erc_cell_flat(factor: str) -> str:
        if not raw_map:
            return ''
        raw = raw_map.get(factor)
        return (f'<td class="num">{raw*100:.2f}%</td>' if raw is not None
                else '<td class="num">-</td>')

    # 평면 순위표: 무리 구분 없이 캡 후 비중 내림차순 (2026-08-28 사용자 지정)
    flat = df.sort_values("weight", ascending=False)
    flat_rows = "".join(
        f'<tr><td class="num" style="width:34px;color:var(--muted)">{i}</td>'
        f'{_factor_cell(r.factor)}'
        f'<td><span class="chip" style="background:{style_colors.get(r.styleName, "#888")}22;'
        f'color:{style_colors.get(r.styleName, "#888")}">{r.styleName}</span></td>'
        f'{_erc_cell_flat(r.factor)}'
        f'<td class="num">{r.weight*100:.2f}%</td>'
        f'<td class="bar"><div style="width:{min(r.weight*100/0.07, 100):.0f}%;'
        f'background:{style_colors.get(r.styleName, "#888")}"></div></td>'
        f'{_delta_cell(r.factor)}</tr>'
        for i, r in enumerate(flat.itertuples(), 1)
    )
    erc_col_f = '<col class="c-w">' if raw_map else ''
    erc_th_f = '<th style="text-align:right">ERC 비중(캡 전)</th>' if raw_map else ''
    delta_col_f = '<col class="c-w">' if has_delta else ''
    delta_th_f = '<th style="text-align:right">전월 대비</th>' if has_delta else ''
    flat_table = (
        f'<div class="cluster"><table class="cl-table">'
        f'<colgroup><col style="width:34px"><col class="c-factor"><col class="c-style">'
        f'{erc_col_f}<col class="c-w"><col class="c-bar">{delta_col_f}</colgroup>'
        f'<thead><tr><th>#</th><th>팩터</th><th>스타일</th>'
        f'{erc_th_f}<th style="text-align:right">ERC 비중(캡 후)</th><th></th>'
        f'{delta_th_f}</tr></thead>'
        f'<tbody>{flat_rows}</tbody></table></div>'
    )

    groups = []
    for cid, g in df.groupby("cluster_id"):
        total_w = g["weight"].sum()
        n = len(g)
        title = (f'무리 {cid} · {n}개 팩터 · 합산 비중 {total_w*100:.1f}%'
                 if n > 1 else f'독립 · 비중 {total_w*100:.1f}%')
        avg_c = g["avg_corr_in_cluster"].replace("", pd.NA).dropna()
        if n > 1 and len(avg_c):
            title += f' · 무리 내 평균상관 {pd.to_numeric(avg_c).mean():.2f}'
        def _erc_cell(factor: str) -> str:
            if not raw_map:
                return ''
            raw = raw_map.get(factor)
            return (f'<td class="num">{raw*100:.2f}%</td>' if raw is not None
                    else '<td class="num">-</td>')

        rows = "".join(
            f'<tr>{_factor_cell(r.factor)}'
            f'<td><span class="chip" style="background:{style_colors.get(r.styleName, "#888")}22;'
            f'color:{style_colors.get(r.styleName, "#888")}">{r.styleName}</span></td>'
            f'{_erc_cell(r.factor)}'
            f'<td class="num">{r.weight*100:.2f}%</td>'
            f'<td class="bar"><div style="width:{min(r.weight*100/0.07, 100):.0f}%;'
            f'background:{style_colors.get(r.styleName, "#888")}"></div></td>'
            f'{_delta_cell(r.factor)}</tr>'
            for r in g.itertuples()
        )
        erc_col = '<col class="c-w">' if raw_map else ''
        erc_th = '<th style="text-align:right">ERC 비중(캡 전)</th>' if raw_map else ''
        delta_col = '<col class="c-w">' if has_delta else ''
        delta_th = '<th style="text-align:right">전월 대비</th>' if has_delta else ''
        groups.append(
            # 섹션 자체가 접힘이므로 내부 무리 박스는 펼침 (2026-08-28 사용자 지정
            # — 섹션을 열면 무리 구성이 바로 보이게)
            f'<details open class="cluster"><summary>{title}</summary>'
            f'<table class="cl-table">'
            f'<colgroup><col class="c-factor"><col class="c-style">'
            f'{erc_col}<col class="c-w"><col class="c-bar">{delta_col}</colgroup>'
            f'<thead><tr><th>팩터</th><th>스타일</th>'
            f'{erc_th}<th style="text-align:right">ERC 비중(캡 후)</th><th></th>'
            f'{delta_th}</tr></thead>'
            f'<tbody>{rows}</tbody></table></details>'
        )

    n_multi = int((df.groupby("cluster_id").size() > 1).sum())
    css = (
        # 무리 박스 = 다른 카드와 동일 스타일 (--card/--hair/12px 라운드),
        # cl-table 은 table-layout:fixed + 고정 컬럼폭 -> 무리 간 컬럼 위치 정렬
        '<style>.cluster{margin:8px 0 14px;background:var(--card);'
        'border:1px solid var(--hair);border-radius:12px;padding:8px 14px}'
        '.cluster summary{cursor:pointer;font-weight:600;padding:6px 0;color:var(--on-dark)}'
        '.cl-table{width:100%;border-collapse:collapse;font-size:13px;table-layout:fixed}'
        '.cl-table col.c-factor{width:40%}.cl-table col.c-style{width:15%}'
        '.cl-table col.c-w{width:11.5%}.cl-table col.c-bar{width:22%}'
        '.cl-table th{text-align:left;color:var(--muted);font-weight:600;font-size:12px;'
        'border-bottom:1px solid var(--hair);padding:6px 8px;white-space:nowrap}'
        '.cl-table td{padding:5px 8px;border-bottom:1px solid var(--hair)}'
        '.cl-table tbody tr:last-child td{border-bottom:none}'
        '.cl-table tbody tr:hover td{background:var(--elev)}'
        '.cl-table .num{text-align:right;font-variant-numeric:tabular-nums;'
        'font-family:var(--mono);font-size:12px}'
        '.cl-table .mono{font-family:var(--mono);font-size:12px;overflow:hidden;'
        'text-overflow:ellipsis;white-space:nowrap}'
        '.cl-table .fullname{font-family:var(--sans);font-size:11px;'
        'font-weight:400;color:var(--muted)}'
        '.cl-table .bar div{height:8px;border-radius:4px}'
        '.chip{padding:1px 8px;border-radius:10px;font-size:11px;font-weight:600}</style>'
    )
    note = (f'<div class="note">|상관| &gt; 0.5 인 팩터끼리 한 무리로 묶음 (표시용 계층 클러스터링). '
            f'ERC 는 무리 전체가 한 배팅처럼 리스크 예산을 나눠 갖도록 개별 비중을 조정한다 — '
            f'무리 {n_multi}개 + 독립 팩터. 합산 비중이 큰 무리 순. '
            f'ERC 비중(캡 전)=ERC 산출 원비중, ERC 비중(캡 후)=스타일 캡 재분배 후 '
            f'최종 명목 비중 (막대·합산 비중·2번 섹션 차트와 동일 기준). '
            f'전월 대비=직전 스냅샷 대비 캡 후 비중 증감.</div>')
    rank_note = ('<div class="note">전 팩터를 캡 후 최종 비중 내림차순으로 나열. '
                 'ERC 비중(캡 전)=ERC 산출 원비중, 캡 후=스타일 캡 재분배 후 '
                 '최종 명목 비중. 전월 대비=직전 스냅샷 대비 캡 후 증감.</div>')
    return (f'<h2>3. ERC 비중 순위 (캡 후 내림차순)</h2>'
            f'{css}{rank_note}{flat_table}',
            _collapsible('ERC 상관 무리 (어떤 팩터가 한 배팅으로 묶였나)',
                         f'{note}{"".join(groups)}'))


def build_dashboard(end_date: str | None = None, output_dir: Path | None = None,
                    data_dir: Path | None = None) -> Path:
    """대시보드 HTML 을 생성해 output/{BM}/<date>/dashboard_<date>.html 로 저장하고 경로 반환."""
    output_dir = Path(output_dir) if output_dir else dd.OUTPUT_DIR
    data_dir = Path(data_dir) if data_dir else dd.UNIVERSE_DATA_DIR

    bt_parts, bt_folded, js_in_bt = _build_backtest_section(output_dir, end_date)
    deploy_html = _deploy_section(output_dir, end_date)
    if deploy_html:
        bt_parts.append(deploy_html)
    pf_parts, pf_folded = _build_portfolio_section(output_dir, end_date, js_already=js_in_bt, data_dir=data_dir)

    # 파일명용 스냅샷 날짜: 가중치 파일 -> 백테스트 마지막 -> 'latest'
    wfile = dd.find_latest_weights_file(output_dir, end_date)
    snap = dd.snapshot_date_from_path(wfile) if wfile else None
    if snap is None:
        wf = latest(output_dir / "walk_forward_results.csv", end_date)
        if wf.exists():
            snap = dd.load_backtest_curves(wf).index.max().strftime("%Y-%m-%d")
    snap = snap or (end_date or "latest")

    header = (
        '<header>'
        f'<span class="title">{PARAM["benchmark"]} 유니버스 전략 포트폴리오 대시보드</span>'
        f'<span class="date">{snap}</span>'
        '<span class="gen">read-only viz - 기존 output CSV 기반</span>'
        '<button id="theme-btn" class="theme-btn" onclick="__toggleTheme()">라이트 모드</button>'
        '</header>'
    )
    # 접힌(참고성) 항목은 전부 페이지 맨 아래 (2026-08-28 사용자 지정)
    body = header + "".join(bt_parts) + "".join(pf_parts) + "".join(bt_folded + pf_folded)
    html = (
        '<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">'
        # uncaught 오류 진단: 발생 파일/라인/스택을 DASH-DIAG 접두로 콘솔 기록.
        # 내장 뷰어에서 재포장돼 뜨는 오류의 출처(페이지 내부 vs 뷰어 스크립트)를
        # 가르는 용도 — 페이지 동작에는 영향 없음 (2026-08-28)
        '<script>window.addEventListener("error",function(e){try{'
        'console.error("DASH-DIAG",e.message,"@",e.filename||"(no file)",'
        '(e.lineno||0)+":"+(e.colno||0),e.error&&e.error.stack)}catch(_){}});'
        # 내장 뷰어 콘솔 심: console.trace/warn 이 없는 환경에서 plotly Lib.warn 이
        # undefined.apply 로 crash 하는 것을 차단 (2026-08-28 실측 — 경고는 log 로 강등)
        'if(window.console){if(!console.trace)console.trace=function(){'
        '(console.log||function(){}).apply(console,arguments);};'
        'if(!console.warn)console.warn=function(){'
        '(console.log||function(){}).apply(console,arguments);};}</script>'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{PARAM["benchmark"]} 유니버스 전략 포트폴리오 대시보드 {snap}</title>'
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700'
        '&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">'
        f'<style>{_PAGE_CSS}</style></head><body>'
        f'<div class="wrap">{body}</div>{_RESIZE_SCRIPT}{_THEME_SCRIPT}</body></html>'
    )

    out_path = dated(output_dir / "dashboard.html", snap)
    out_path.write_text(html, encoding="utf-8")
    logger.info("Dashboard saved to %s", out_path)
    return out_path
