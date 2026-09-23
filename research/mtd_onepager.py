# -*- coding: utf-8 -*-
"""종목 기여(임시, BM 유니버스 제한) 기준 스타일/팩터 비중·기여 한 장 PDF (내부 보고용).

사용:  BENCHMARK=MXCN1A python research/mtd_onepager.py [YYYY-MM-DD] [--bbg 9.60] [--gross 0.19]   # 기본 어제
--gross 는 배포 gross 를 가정값으로 재스케일 (예: 0.19 = 롱/숏 각 9.5%). 파일명에 _gross19 접미사
출력:  output/{BM}/<MP 기준일>/mtd_pdf/mtd_style_factor[_bmuniv]_<asof>[_grossNN].pdf (+ .png 미리보기)
전제:  data/{BM}/bm_universe.csv 가 있으면 BM 유니버스 제한본(_bmuniv), 없으면(MXWO) 원 MP 기준. 일별 캐시는 mtd_dashboard 와 공유.
--bbg 는 Bloomberg PORT Active CTR(bp) 를 머리글에 병기.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent)); sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import mtd_dashboard as m

args = [a for a in sys.argv[1:] if not a.startswith("--")]
BBG = next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--bbg" and i + 1 < len(sys.argv)), None)
ASOF = args[0] if args else str((pd.Timestamp.today().normalize() - pd.Timedelta(days=1)).date())
GROSS = next((float(sys.argv[i + 1]) for i, a in enumerate(sys.argv) if a == "--gross" and i + 1 < len(sys.argv)), None)
RB = next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--rb" and i + 1 < len(sys.argv)), None)   # 교대일 가정 (로그 미변경)
D = m.build(pd.Timestamp(ASOF))
if RB: D["rebalance"] = {**D["rebalance"], "actual_date": RB, "source": "assumed"}
BM = D["universe"]
# --gross 0.19: 배포 gross 를 가정값으로 재스케일 (모든 비중·기여가 선형이라 배율 하나로 처리)
K = (GROSS / D["gross_cur"]) if GROSS else 1.0
if GROSS:
    for s_ in D["stocks"]:
        for key in ("w_prev", "w_cur", "wb_prev", "wb_cur"): s_[key] *= K
        for key in ("style_prev", "style_cur", "sb_prev", "sb_cur"): s_[key] = {x: v * K for x, v in s_[key].items()}
        for key in ("f_prev", "f_cur"): s_[key] = [[i, w * K] for i, w in s_[key]]
    D["gross_cur"] *= K; D["gross_prev"] = (D["gross_prev"] or 0) * K
    for a in (D["bm_adj"].get("cur") or {}, D["bm_adj"].get("prev") or {}):
        for key in ("removed_long", "removed_short", "long_before", "short_before", "target"):
            if key in a: a[key] *= K
dates = D["dates"]; k = dates.index(D["rebalance"]["actual_date"]); idx = len(dates) - 1
styles = D["styles"]; factors = D["factors"]; fstyle = D["factor_style"]; fname = D["factor_name"]

st = {s: dict(long=0.0, short=0.0, c=0.0) for s in styles}
fc = {f: dict(long=0.0, short=0.0, c=0.0) for f in factors}
tot = 0.0; tot_mp = 0.0
for s, ret in zip(D["stocks"], D["ret"]):
    g = np.cumprod(np.r_[1.0, 1.0 + np.array(ret)])
    stitch = lambda wp, wc: wp * (g[k + 1] - 1) + wc * (g[idx + 1] / g[k + 1] - 1)
    tot += stitch(s["wb_prev"], s["wb_cur"]); tot_mp += stitch(s["w_prev"], s["w_cur"])
    for key, v in s["sb_cur"].items():   # 활성 북 = 당월 (기준일 > 리밸런싱일)
        if v > 0: st[key]["long"] += v
        else: st[key]["short"] += v
    for key in set(s["sb_prev"]) | set(s["sb_cur"]):
        st[key]["c"] += stitch(s["sb_prev"].get(key, 0.0), s["sb_cur"].get(key, 0.0))
    rp = (s["wb_prev"] / s["w_prev"]) if s["w_prev"] else 0.0; rc = (s["wb_cur"] / s["w_cur"]) if s["w_cur"] else 0.0
    fp = {factors[i]: w * rp for i, w in s["f_prev"]}; fcu = {factors[i]: w * rc for i, w in s["f_cur"]}
    for f, v in fcu.items():
        if v > 0: fc[f]["long"] += v
        else: fc[f]["short"] += v
    for f in set(fp) | set(fcu):
        fc[f]["c"] += stitch(fp.get(f, 0.0), fcu.get(f, 0.0))

sdf = pd.DataFrame(st).T; sdf["gross"] = sdf.long - sdf.short; sdf = sdf[(sdf.gross > 0) | (sdf.c.abs() > 1e-12)].sort_values("gross", ascending=False)
fdf = pd.DataFrame(fc).T; fdf["gross"] = fdf.long - fdf.short; fdf = fdf[(fdf.gross > 0) | (fdf.c.abs() > 1e-12)]; fdf["style"] = fdf.index.map(fstyle)  # 전월 북에만 있던 팩터(당월 gross 0)의 교대 전 기여 포함
top = fdf.sort_values("gross", ascending=False).head(10); bot = fdf.sort_values("gross", ascending=True).head(10).iloc[::-1]
A = D["bm_adj"].get("cur") or {}; Ap = D["bm_adj"].get("prev") or {}
print(f"MTD 임시 {tot*1e4:.2f} bp / 원 MP {tot_mp*1e4:.2f} bp | style c sum {sdf.c.sum()*1e4:.2f} | factor c sum {fdf.c.sum()*1e4:.2f}")

# ── PDF
for f in ["Malgun Gothic", "NanumGothic", "Pretendard"]:
    if any(f == x.name for x in font_manager.fontManager.ttflist):
        plt.rcParams["font.family"] = f; break
plt.rcParams["axes.unicode_minus"] = False
INK, MUTED, UP, DOWN, LINE = "#14171c", "#6b7280", "#168a54", "#c23d4a", "#d9dde3"
fig = plt.figure(figsize=(8.27, 11.69)); fig.patch.set_facecolor("white")
pct = lambda x, d=2: f"{x*100:+.{d}f}%"; pu = lambda x, d=2: f"{x*100:.{d}f}%"; bp = lambda x: f"{x*1e4:+.1f}bp"

TITLE_NAME = {"MXWO": "QMIND KIC", "MXCN1A": "QMind BOK"}  # PDF 제목 표기 (사용자 지정 2026-09-21); 파일명·폴더는 유니버스 코드 유지
fig.text(0.07, 0.955, f"{TITLE_NAME.get(BM, f'QMind {BM}')} MP 성과 분석", fontsize=14, weight="bold", color=INK)
fig.text(0.07, 0.933, f"{D['base']} → {ASOF} 종가 · 실현 기준(리밸런싱 {D['rebalance']['actual_date']} 종가 교대{' 가정' if RB else ''}) · 배포 비중(gross {D['gross_cur']*100:.0f}%) · USD · 기여 = NAV 대비", fontsize=8.5, color=MUTED)
fig.text(0.07, 0.905, f"MTD 기여  {bp(tot)}", fontsize=20, weight="bold", color=UP if tot > 0 else DOWN)

def table(ax, title, header, rows, colw, colors=None, total_rows=0):
    ax.axis("off"); ax.set_title(title, loc="left", fontsize=10.5, weight="bold", color=INK, pad=6)
    n = len(rows); y0 = 1.0; h = min(0.075, 0.95 / (n + 1.5))
    x = np.cumsum([0] + colw[:-1])
    for j, hd in enumerate(header):
        ax.text(x[j] + (colw[j] if j else 0), y0, hd, ha="right" if j else "left", va="top", fontsize=7.5, color=MUTED, transform=ax.transAxes)
    ax.plot([0, 1], [y0 - h * 0.85] * 2, color=LINE, lw=0.8, transform=ax.transAxes)
    for i, r in enumerate(rows):
        y = y0 - h * (i + 1.7); tot = i >= n - total_rows
        if tot and i == n - total_rows: ax.plot([0, 1], [y + h * 0.18] * 2, color=INK, lw=0.8, transform=ax.transAxes)
        for j, v in enumerate(r):
            c = INK
            if colors and colors[i][j]: c = colors[i][j]
            ax.text(x[j] + (colw[j] if j else 0), y, v, ha="right" if j else "left", va="top", fontsize=7.6 if not tot else 7.8,
                    weight="bold" if tot else "normal", color=c, transform=ax.transAxes, family="monospace" if j else None)

cc = lambda v: UP if v > 0 else DOWN if v < 0 else INK
STYLE_COLORS = {"Valuation": "#2f5fd1", "Earnings Quality": "#c94f9a", "Historical Growth": "#17957a", "Capital Efficiency": "#c98a1e",
                "Analyst Expectations": "#b8552f", "Price Momentum": "#7b5cc7", "Volatility": "#8a8f98", "Size": "#3f9fc2"}
scol_ = lambda st_: STYLE_COLORS.get(st_, "#9aa0a6")
from matplotlib.transforms import blended_transform_factory as blend
from matplotlib.patches import Patch
# 비중 표기: "배분% (롱% / 숏%)" — 배분 = 팩터 배분 비율(합 100%), 괄호 = 배포 롱/숏 (사용자 지정 2026-09-23)
FW = D["factor_weight"]
alloc_st = {}
for f_, w_ in FW.items():
    alloc_st[fstyle.get(f_, "")] = alloc_st.get(fstyle.get(f_, ""), 0.0) + w_
wfmt = lambda a, lo, sh: f"{a*100:5.1f}% ({(lo - sh) / 2 * 100:4.1f}%)"   # 괄호 = 롱·|숏| 평균 (한쪽 다리 크기, 2026-09-23 지정)

def clean(ax):
    for sp in ["top", "right", "left"]: ax.spines[sp].set_visible(False)
    ax.spines["bottom"].set_color(LINE); ax.xaxis.grid(True, color=LINE, lw=0.5); ax.set_axisbelow(True); ax.tick_params(length=0)

def contrib_chart(ax, labels, vals, colors, split_after=None):
    """왼쪽 막대 차트 공통: 위가 1등. 막대 끝에 bp 라벨. split_after 행 뒤에 점선."""
    n_ = len(vals); ys = range(n_)
    ax.barh(ys, vals, color=colors, height=0.62)
    ax.set_yticks(list(ys)); ax.set_yticklabels(labels, fontsize=7.0, linespacing=1.15); ax.invert_yaxis()
    ax.axvline(0, color=INK, lw=0.6)
    lim = max(abs(min(vals)), abs(max(vals)), 1e-9) * 1.35
    for k_, v in enumerate(vals):
        ax.text(v + (lim * 0.02 if v >= 0 else -lim * 0.02), k_, f"{v:+.1f}", va="center", ha="left" if v >= 0 else "right", fontsize=6.8, color=INK)
    ax.set_xlim(-lim, lim); ax.set_ylim(n_ - 0.4, -0.6)
    ax.tick_params(axis="x", labelsize=6.8); ax.set_xlabel("기여 (bp)", fontsize=7.5, color=MUTED); clean(ax)
    if split_after is not None: ax.axhline(split_after - 0.5, color=LINE, lw=0.8, ls="--")

def right_notes(ax, texts, header):
    tr = blend(ax.transAxes, ax.transData)
    ax.text(1.06, -0.75, header, transform=tr, fontsize=6.6, color=MUTED, va="bottom")
    for k_, t_ in enumerate(texts):
        ax.text(1.06, k_, t_, transform=tr, fontsize=7.0, color=INK, va="center", family="monospace")

# ── 공통 범례 (제목 아래 한 줄)
fig.legend(handles=[Patch(color=scol_(x), label=x) for x in styles if x in alloc_st], loc="upper left", bbox_to_anchor=(0.07, 0.885),
           ncol=8, fontsize=6.6, frameon=False, handlelength=1.0, columnspacing=1.0, handletextpad=0.5)

# ── 1) 스타일: 기여 내림차순, 왼쪽 막대 / 오른쪽 비중
sv = sdf.sort_values("c", ascending=False)
fig.text(0.07, 0.845, "스타일별 비중과 기여", fontsize=10.5, weight="bold", color=INK)
fig.text(0.07, 0.831, "기준일까지 누적 기여(bp), 기여 내림차순 · 오른쪽 = 팩터 배분 비중 (배포 롱숏 평균)", fontsize=7.5, color=MUTED)
ax1 = fig.add_axes([0.31, 0.625, 0.42, 0.19])
contrib_chart(ax1, list(sv.index), list(sv.c * 1e4), [scol_(x) for x in sv.index])
right_notes(ax1, [wfmt(alloc_st.get(s_, 0.0), r.long, r.short) for s_, r in sv.iterrows()], "비중 (롱숏 평균)")
mp_long = sum(max(s_["wb_cur"], 0) for s_ in D["stocks"]); mp_short = sum(min(s_["wb_cur"], 0) for s_ in D["stocks"])
# 합계 두 줄: 라벨/비중/기여 열 정렬 (라벨 = 비례 폰트, 숫자 = 고정폭) · 합계는 롱/숏 각각 표시
alloc_tot = sum(alloc_st.values())
for y_, lab, a_, lo_, sh_, c_ in [(0.585, "합계 (netting 전)", f"{alloc_tot*100:.1f}%", sdf.long.sum(), -sdf.short.sum(), sdf.c.sum()),
                                  (0.571, "합계 (netting 후 = MP)", "", mp_long, -mp_short, tot)]:
    fig.text(0.07, y_, lab, fontsize=7.6, weight="bold", color=INK)
    # 한글은 고정폭 폰트에 글리프가 없어 라벨(롱/숏)과 숫자를 나눠 찍는다 (열 x 고정)
    kw = dict(fontsize=7.6, weight="bold", color=INK)
    fig.text(0.225, y_, f"{a_:>6}", family="monospace", **kw)
    fig.text(0.277, y_, "(롱", **kw); fig.text(0.299, y_, f"{lo_*100:5.1f}%", family="monospace", **kw)
    fig.text(0.352, y_, "/ 숏", **kw); fig.text(0.377, y_, f"{sh_*100:5.1f}%)", family="monospace", **kw)
    fig.text(0.45, y_, f"기여 {bp(c_)}", fontsize=7.6, weight="bold", color=cc(c_))

# ── 2) 팩터: 기여 상·하위 10, 같은 구조
top = fdf.sort_values("c", ascending=False).head(10); bot = fdf.sort_values("c", ascending=True).head(10)
sel = pd.concat([top, bot.iloc[::-1]]).drop_duplicates()
n_top = len(top)
fig.text(0.07, 0.535, "팩터 기여 상하위 10", fontsize=10.5, weight="bold", color=INK)
fig.text(0.07, 0.521, "기준일까지 누적 기여(bp), 점선 위 상위 · 아래 하위 · 오른쪽 = 팩터 배분 비중 (배포 롱숏 평균)", fontsize=7.5, color=MUTED)
ax2 = fig.add_axes([0.31, 0.06, 0.42, 0.44])
contrib_chart(ax2, [f"{f_}\n{fname.get(f_, '')[:30]}" for f_ in sel.index], list(sel.c * 1e4), [scol_(x) for x in sel["style"]], split_after=n_top)
right_notes(ax2, [wfmt(FW.get(f_, 0.0), r.long, r.short) for f_, r in sel.iterrows()], "비중 (롱숏 평균)")
out_dir = m.OUTPUT_DIR / D["book_date"] / "mtd_pdf"; out_dir.mkdir(parents=True, exist_ok=True)
out = str(out_dir / f"mtd_style_factor{'_bmuniv' if A else ''}_{ASOF}{f'_gross{GROSS*100:.0f}' if GROSS else ''}{f'_rb{RB[5:7]}{RB[8:10]}' if RB else ''}.pdf")
fig.savefig(out, format="pdf"); fig.savefig(out.replace(".pdf", ".png"), dpi=130)
print("saved", out)
