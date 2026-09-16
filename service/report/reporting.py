# -*- coding: utf-8 -*-
"""Rich 콘솔 리포트 표현 계층 (print_*_report).

전부 부수효과(-> None, 콘솔 출력)이며 CSV/parquet 산출물 계약과 무관하다.
cp949 콘솔 호환을 위해 특수 유니코드 기호는 쓰지 않는다 (CLAUDE.md).
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def print_coverage_report(
    warnings_list: list[dict],
    factor_df: pd.DataFrame,
    mret_df: pd.DataFrame,
) -> None:
    """Rich 테이블로 커버리지 리포트를 터미널에 출력한다."""
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    console = Console()

    monthly_factors = factor_df.groupby("ddt")["factorAbbreviation"].nunique().sort_index()
    monthly_stocks = factor_df.groupby("ddt")["gvkeyiid"].nunique().sort_index()
    mret_stocks = mret_df.groupby("ddt")["gvkeyiid"].nunique().sort_index()
    monthly_null_pct = (
        factor_df.groupby("ddt")["val"].apply(lambda x: x.isna().mean()).sort_index()
        if "val" in factor_df.columns else pd.Series(dtype=float)
    )

    recent_n = min(12, len(monthly_factors))
    recent_dates = monthly_factors.index[-recent_n:]

    table = Table(title="Monthly Coverage (recent)", show_lines=False, pad_edge=False)
    table.add_column("Month", style="cyan", width=8)
    table.add_column("Factors", justify="right", width=8)
    table.add_column("+/-", justify="right", width=5)
    table.add_column("Stocks", justify="right", width=8)
    table.add_column("+/-", justify="right", width=6)
    table.add_column("M_RET", justify="right", width=8)
    table.add_column("NULL%", justify="right", width=6)
    table.add_column("Bar", width=20)

    max_stocks = monthly_stocks.max() if len(monthly_stocks) > 0 else 1

    for i, dt in enumerate(recent_dates):
        dt_str = pd.Timestamp(dt).strftime("%Y-%m")
        n_factors = monthly_factors.get(dt, 0)
        n_stocks = monthly_stocks.get(dt, 0)
        n_mret = mret_stocks.get(dt, 0) if dt in mret_stocks.index else 0

        idx_in_full = list(monthly_factors.index).index(dt)
        if idx_in_full > 0:
            prev_f = monthly_factors.iloc[idx_in_full - 1]
            prev_s = monthly_stocks.iloc[idx_in_full - 1]
            delta_f = n_factors - prev_f
            delta_s = n_stocks - prev_s
            df_str = f"{delta_f:+d}" if delta_f != 0 else ""
            ds_str = f"{delta_s:+d}" if delta_s != 0 else ""
            df_style = "red" if delta_f < 0 else "green" if delta_f > 0 else ""
            ds_style = "red" if delta_s < -10 else "green" if delta_s > 10 else ""
        else:
            df_str, ds_str = "", ""
            df_style, ds_style = "", ""

        null_pct = monthly_null_pct.get(dt, 0) if len(monthly_null_pct) > 0 else 0
        null_str = f"{null_pct:.0%}" if null_pct >= 0.99 else f"{null_pct:.1%}"
        null_style = "bold red" if null_pct >= 0.99 else "yellow" if null_pct > 0.25 else ""

        bar_len = int(n_stocks / max_stocks * 18) if max_stocks > 0 else 0
        bar = "#" * bar_len + "." * (18 - bar_len)

        table.add_row(
            dt_str,
            str(n_factors),
            Text(df_str, style=df_style),
            str(n_stocks),
            Text(ds_str, style=ds_style),
            str(n_mret),
            Text(null_str, style=null_style),
            Text(bar, style="blue"),
        )

    console.print(table)

    total_months = len(monthly_factors)
    summary = (
        f"Period: {pd.Timestamp(monthly_factors.index[0]).strftime('%Y-%m')} ~ "
        f"{pd.Timestamp(monthly_factors.index[-1]).strftime('%Y-%m')} "
        f"({total_months} months)\n"
        f"Factors: {monthly_factors.median():.0f} (median), "
        f"{monthly_factors.min()}~{monthly_factors.max()} (range)\n"
        f"Stocks: {monthly_stocks.median():.0f} (median), "
        f"{monthly_stocks.min()}~{monthly_stocks.max()} (range)"
    )
    console.print(Panel(summary, title="Summary", border_style="dim"))

    if warnings_list:
        warn_table = Table(title="Warnings", show_lines=False)
        warn_table.add_column("Level", width=6)
        warn_table.add_column("Type", width=22)
        warn_table.add_column("Message")

        for w in warnings_list:
            level_style = "bold red" if w["level"] == "ERROR" else "yellow"
            warn_table.add_row(
                Text(w["level"], style=level_style),
                w["type"],
                w["message"],
            )
        console.print(warn_table)
    else:
        console.print("[bold green]OK - all checks passed[/]")


def print_overfit_report(report: dict[str, Any]) -> None:
    """과적합 진단 리포트를 Rich 테이블로 콘솔 출력한다."""
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel

    console = Console()

    # ── 1순위: Funnel Value-Add ──
    funnel_table = Table(title="1순위: Funnel Value-Add Test (단계별 가치 창출)", show_header=True)
    funnel_table.add_column("Portfolio", style="bold")
    funnel_table.add_column("CAGR", justify="right")
    funnel_table.add_column("MDD", justify="right")
    funnel_table.add_column("Description")

    funnel_table.add_row(
        "A. EW_All", f"{report['funnel_ew_all_cagr']:.4%}",
        f"{report['funnel_ew_all_mdd']:.4%}",
        "전체 유효 팩터 동일가중",
    )
    funnel_table.add_row(
        "B. EW_Top50", f"{report['funnel_ew_top50_cagr']:.4%}",
        f"{report['funnel_ew_top50_mdd']:.4%}",
        "Top-50 후보군 동일가중",
    )
    funnel_table.add_row(
        "C. Constrained EW", f"{report['funnel_cew_cagr']:.4%}",
        f"{report['funnel_cew_mdd']:.4%}",
        "Top-N EW + style_cap 25%",
    )

    console.print(funnel_table)

    pattern = report["funnel_pattern"]
    # CONSTRAINT_DRAG 는 실패가 아니라 트레이드오프이므로 경고(yellow), FILTER_OVERFIT 만 위험(red)
    pattern_style = {"NORMAL": "green", "CONSTRAINT_DRAG": "yellow", "FILTER_OVERFIT": "red"}.get(pattern, "yellow")
    console.print(Panel(report["funnel_interpretation"], title=f"판별: {pattern}", style=pattern_style))

    # ── 진단 지표 테이블 ──
    table = Table(title="과적합 진단 지표 (OOS Walk-Forward)", show_header=True)
    table.add_column("Priority", style="bold", width=10)
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    table.add_column("Interpretation")

    # 2순위
    pct = report["oos_avg_percentile"]
    table.add_row(
        "2순위", "OOS Percentile",
        f"{pct:.2%}" if not np.isnan(pct) else "N/A",
        report["oos_percentile_interpretation"],
    )

    # 3순위
    sj = report["strict_jaccard"]
    table.add_row(
        "3순위", "Strict Jaccard",
        f"{sj:.4f}" if not np.isnan(sj) else "N/A",
        report["strict_jaccard_interpretation"],
    )

    # 4순위 (보조)
    spearman = report["is_oos_rank_spearman"]
    table.add_row(
        "4순위(보조)", "IS-OOS Rank Corr",
        f"{spearman:.4f}" if not np.isnan(spearman) else "N/A",
        report["rank_corr_interpretation"],
    )

    # 5순위 (보조)
    deflation = report["deflation_ratio"]
    table.add_row(
        "5순위(보조)", "Deflation Ratio",
        f"{deflation:.4f}" if not np.isnan(deflation) else "N/A",
        report["deflation_interpretation"],
    )

    console.print(table)

    # 성과 비교 테이블
    perf_table = Table(title="OOS 성과 비교 (Constrained EW vs. Pure EW)", show_header=True)
    perf_table.add_column("Metric", style="bold")
    perf_table.add_column("Constrained EW", justify="right")
    perf_table.add_column("EW_Top50 (1/N)", justify="right")

    perf_table.add_row("CAGR", f"{report['oos_cagr']:.4%}", f"{report['oos_ew_cagr']:.4%}")
    perf_table.add_row("Excess CAGR", f"{report['cew_vs_ew_excess_cagr']:.4%}", "-")
    perf_table.add_row("MDD", f"{report['oos_mdd']:.4%}", f"{report['oos_ew_mdd']:.4%}")
    perf_table.add_row("Sharpe", f"{report['oos_sharpe']:.4f}", f"{report['oos_ew_sharpe']:.4f}")
    perf_table.add_row("Win Rate", f"{report['cew_vs_ew_win_rate']:.2%}", "-")

    console.print(perf_table)

    # 경고 패널
    console.print(Panel(report["warning"], title="경고", style="yellow"))
    console.print(Panel(report["limitation"], title="한계점", style="dim"))
