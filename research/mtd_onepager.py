# -*- coding: utf-8 -*-
"""종목 기여(임시, BM 유니버스 제한) 기준 스타일/팩터 비중·기여 한 장 PDF (내부 보고용).

사용:  BENCHMARK=MXCN1A python research/mtd_onepager.py [YYYY-MM-DD] [--bbg 9.60] [--gross 0.19] [--1m | --from YYYY-MM-DD]   # 기본 어제, 날짜는 맨 앞
--1m 은 기준일 한 달 전 종가부터 (최근 1개월), --from 은 지정일 종가부터. 기간이 전월로 넘어가면 전월 데이터(그 달 북·교대일)를 이어 붙인다.
--gross 는 배포 gross 를 가정값으로 재스케일 (예: 0.19 = 롱/숏 각 9.5%). 파일명에 _gross19 접미사
출력:  output/{BM}/<MP 기준일>/mtd_pdf/mtd_performance_analysis_{KIC|BOK}[_bmuniv]_<asof>[_grossNN].pdf (+ .png 미리보기)
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
# 기간 옵션 (2026-10-21): --1m = 기준일 한 달 전 날짜의 종가부터, --from = 지정일 종가부터. 미지정이면 MTD (기존과 동일)
FROM = next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--from" and i + 1 < len(sys.argv)), None)
if "--1m" in sys.argv:
    FROM = str((pd.Timestamp(ASOF) - pd.DateOffset(months=1)).date())
Dp = m.build(pd.Timestamp(D["base"])) if FROM and FROM < D["base"] else None   # 기간이 전월로 넘어가면 전월 데이터도
# --gross 0.19: 배포 gross 를 가정값으로 재스케일 (모든 비중·기여가 선형이라 배율 하나로 처리)


def scale_gross(Dx):
    Kx = GROSS / Dx["gross_cur"]
    for s_ in Dx["stocks"]:
        for key in ("w_prev", "w_cur", "wb_prev", "wb_cur"): s_[key] *= Kx
        for key in ("style_prev", "style_cur", "sb_prev", "sb_cur"): s_[key] = {x: v * Kx for x, v in s_[key].items()}
        for key in ("f_prev", "f_cur"): s_[key] = [[i, w * Kx] for i, w in s_[key]]
    Dx["gross_cur"] *= Kx; Dx["gross_prev"] = (Dx["gross_prev"] or 0) * Kx
    for a in (Dx["bm_adj"].get("cur") or {}, Dx["bm_adj"].get("prev") or {}):
        for key in ("removed_long", "removed_short", "long_before", "short_before", "target"):
            if key in a: a[key] *= Kx


if GROSS:
    for Dx in (D, Dp):
        if Dx is not None: scale_gross(Dx)
dates = D["dates"]; idx = len(dates) - 1
k = sum(d <= D["rebalance"]["actual_date"] for d in dates) - 1   # 교대일이 기준일(월말) 이하이면 -1 = 당월 북이 월초부터 (g[0]=1 이라 stitch 식 그대로 성립)
RB_TXT = (f"{dates[0]} 시가(월초 당월 북 적용)" if k < 0 else f"{D['rebalance']['actual_date']} 종가 교대{' 가정' if RB else ''}")
# 구간 = (데이터, 시작 인덱스(-1 = 월초), 교대 인덱스, 끝 인덱스). 기여 = 끝까지 누적 - 시작까지 누적 (같은 이어붙이기 식)
if Dp is not None:
    dp_ = Dp["dates"]
    segs = [(Dp, sum(d <= FROM for d in dp_) - 1, sum(d <= Dp["rebalance"]["actual_date"] for d in dp_) - 1, len(dp_) - 1),
            (D, -1, k, idx)]
else:
    segs = [(D, (sum(d <= FROM for d in dates) - 1) if FROM else -1, k, idx)]
S0 = segs[0]
START = S0[0]["dates"][S0[1]] if S0[1] >= 0 else S0[0]["base"]   # 기간 시작 종가일
PERIOD = "최근 1개월" if "--1m" in sys.argv else ("기간" if FROM else "MTD")
styles = D["styles"] + [x for x in (Dp["styles"] if Dp else []) if x not in D["styles"]]
fstyle = {**(Dp["factor_style"] if Dp else {}), **D["factor_style"]}; fname = {**(Dp["factor_name"] if Dp else {}), **D["factor_name"]}

st, fc = {}, {}


def add(dct, key, field, v):
    dct.setdefault(key, dict(long=0.0, short=0.0, c=0.0))[field] += v


tot = 0.0; tot_mp = 0.0
stk_d = {}   # (isin, ticker) -> [stock(당월 북 우선), 기여, 기간 성장률] — 상하위 종목 차트용 (2026-10-21)
for Dx, i0, kx, ix in segs:
    cur_seg = Dx is D   # 롱/숏 노출은 당월(현재) 북만
    fx = Dx["factors"]
    for s, ret in zip(Dx["stocks"], Dx["ret"]):
        g = np.cumprod(np.r_[1.0, 1.0 + np.array(ret)])

        def cum(wp, wc, t):   # t 날 종가까지 이어붙인 누적 기여 (t<=교대일이면 전월 북만)
            if t < 0: return 0.0
            return wp * (g[t + 1] - 1) if t <= kx else wp * (g[kx + 1] - 1) + wc * (g[t + 1] / g[kx + 1] - 1)
        seg = lambda wp, wc: cum(wp, wc, ix) - cum(wp, wc, i0)
        c_s = seg(s["wb_prev"], s["wb_cur"])
        tot += c_s; tot_mp += seg(s["w_prev"], s["w_cur"])
        e = stk_d.setdefault((s["isin"], s["ticker"]), [s, 0.0, 1.0])
        if cur_seg: e[0] = s
        e[1] += c_s; e[2] *= g[ix + 1] / g[i0 + 1]
        if cur_seg:
            for key, v in s["sb_cur"].items(): add(st, key, "long" if v > 0 else "short", v)
        for key in set(s["sb_prev"]) | set(s["sb_cur"]):
            add(st, key, "c", seg(s["sb_prev"].get(key, 0.0), s["sb_cur"].get(key, 0.0)))
        rp = (s["wb_prev"] / s["w_prev"]) if s["w_prev"] else 0.0; rc = (s["wb_cur"] / s["w_cur"]) if s["w_cur"] else 0.0
        fp = {fx[i]: w * rp for i, w in s["f_prev"]}; fcu = {fx[i]: w * rc for i, w in s["f_cur"]}
        if cur_seg:
            for f, v in fcu.items(): add(fc, f, "long" if v > 0 else "short", v)
        for f in set(fp) | set(fcu):
            add(fc, f, "c", seg(fp.get(f, 0.0), fcu.get(f, 0.0)))
stk = [(s_, c_, gr_ - 1) for s_, c_, gr_ in stk_d.values()]

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

FUND = {"MXWO": "KIC", "MXCN1A": "BOK"}  # 파일명 펀드 표기 (2026-10-21)
TITLE_NAME = {"MXWO": "QMIND KIC", "MXCN1A": "QMind BOK"}  # PDF 제목 표기 (사용자 지정 2026-09-21); 파일명·폴더는 유니버스 코드 유지
fig.text(0.07, 0.955, f"{TITLE_NAME.get(BM, f'QMind {BM}')} MP 성과 분석", fontsize=14, weight="bold", color=INK)
if Dp is not None:
    RB_P = Dp["rebalance"]["actual_date"]
    fig.text(0.07, 0.933, f"{START} → {ASOF} 종가 · 실현 기준(리밸런싱 {RB_P}, {RB_TXT}) · 배포 비중(gross {Dp['gross_cur']*100:.0f}% / {D['gross_cur']*100:.0f}%)",
             fontsize=8.5, color=MUTED)
else:
    fig.text(0.07, 0.933, f"{START} → {ASOF} 종가 · 실현 기준(리밸런싱 {RB_TXT}) · 배포 비중(gross {D['gross_cur']*100:.0f}%)", fontsize=8.5, color=MUTED)
fig.text(0.07, 0.905, f"{PERIOD} 기여  {bp(tot)}", fontsize=20, weight="bold", color=UP if tot > 0 else DOWN)

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

def contrib_chart(ax, labels, vals, colors, split_after=None, fs=7.0, vfs=6.8):
    """왼쪽 막대 차트 공통: 위가 1등. 막대 끝에 bp 라벨. split_after 행 뒤에 점선. fs/vfs = 라벨/값 글자 크기."""
    n_ = len(vals); ys = range(n_)
    ax.barh(ys, vals, color=colors, height=0.62)
    ax.set_yticks(list(ys)); ax.set_yticklabels(labels, fontsize=fs, linespacing=1.15); ax.invert_yaxis()
    ax.axvline(0, color=INK, lw=0.6)
    lim = max(abs(min(vals)), abs(max(vals)), 1e-9) * 1.35
    for k_, v in enumerate(vals):
        ax.text(v + (lim * 0.02 if v >= 0 else -lim * 0.02), k_, f"{v:+.1f}", va="center", ha="left" if v >= 0 else "right", fontsize=vfs, color=INK)
    ax.set_xlim(-lim, lim); ax.set_ylim(n_ - 0.4, -0.6)
    ax.tick_params(axis="x", labelsize=vfs); ax.set_xlabel("기여 (bp)", fontsize=7.5, color=MUTED); clean(ax)
    if split_after is not None: ax.axhline(split_after - 0.5, color=LINE, lw=0.8, ls="--")

def right_notes(ax, texts, header, fs=7.0):
    tr = blend(ax.transAxes, ax.transData)
    ax.text(1.06, -0.75, header, transform=tr, fontsize=min(6.6, fs), color=MUTED, va="bottom")
    for k_, t_ in enumerate(texts):
        ax.text(1.06, k_, t_, transform=tr, fontsize=fs, color=INK, va="center", family="monospace")

# ── 공통 범례 (제목 아래 한 줄)
fig.legend(handles=[Patch(color=scol_(x), label=x) for x in styles if x in alloc_st], loc="upper left", bbox_to_anchor=(0.07, 0.885),
           ncol=8, fontsize=6.6, frameon=False, handlelength=1.0, columnspacing=1.0, handletextpad=0.5)

# ── 1) 스타일: 기여 내림차순, 왼쪽 막대 / 오른쪽 비중
sv = sdf.sort_values("c", ascending=False)
fig.text(0.07, 0.845, "팩터 스타일별 기여와 비중", fontsize=10.5, weight="bold", color=INK)
ax1 = fig.add_axes([0.19, 0.625, 0.22, 0.19])
contrib_chart(ax1, list(sv.index), list(sv.c * 1e4), [scol_(x) for x in sv.index])
# 배분 파이: 배분 내림차순, 12시부터 시계방향 · 라벨은 조각 바깥에 지시선으로 (이름 / "배분% (롱숏평균%)"), 같은 쪽 라벨은 겹치지 않게 아래로 밀어냄
pv = sdf.assign(alloc=[alloc_st.get(x, 0.0) for x in sdf.index]).sort_values("alloc", ascending=False)
axp = fig.add_axes([0.51, 0.595, 0.44, 0.245]); axp.set_aspect("equal")
wedges, _ = axp.pie(pv.alloc, colors=[scol_(x) for x in pv.index], startangle=90, counterclock=False, radius=0.75,
                    wedgeprops=dict(width=0.34, edgecolor="white", linewidth=1.0))
axp.set_xlim(-2.1, 2.1); axp.set_ylim(-1.15, 1.15)
labs = []
for w_, (s_, r) in zip(wedges, pv.iterrows()):
    ang = np.deg2rad((w_.theta1 + w_.theta2) / 2)
    labs.append(dict(name=s_, txt=f"{r.alloc*100:.1f}% ({(r.long - r.short) / 2 * 100:.1f}%)", x=np.cos(ang), y=np.sin(ang)))
for side in (1, -1):
    grp = sorted([l_ for l_ in labs if (l_["x"] >= 0) == (side > 0)], key=lambda l_: l_["y"], reverse=True)
    ys = [l_["y"] * 1.05 for l_ in grp]
    for q in range(1, len(ys)):                       # 위에서부터 최소 간격 확보
        ys[q] = min(ys[q], ys[q - 1] - 0.30)
    if ys and ys[-1] < -1.05: ys = [y_ + (-1.05 - ys[-1]) for y_ in ys]
    for l_, ty in zip(grp, ys):
        axp.annotate(f"{l_['name']}\n{l_['txt']}", xy=(l_["x"] * 0.76, l_["y"] * 0.76), xytext=(side * 1.12, ty),
                     ha="left" if side > 0 else "right", va="center", fontsize=6.5, color=INK, linespacing=1.25,
                     arrowprops=dict(arrowstyle="-", color=MUTED, lw=0.5, shrinkA=0, shrinkB=1))
fig.text(0.485, 0.621, "팩터 배분 비중 (합 100%) · 괄호 = 배포 롱숏 평균", fontsize=6.8, color=MUTED)   # 파이 참고 (파이와 합계 사이)
mp_long = sum(max(s_["wb_cur"], 0) for s_ in D["stocks"]); mp_short = sum(min(s_["wb_cur"], 0) for s_ in D["stocks"])
# 합계 두 줄: 라벨/비중/기여 열 정렬 (라벨 = 비례 폰트, 숫자 = 고정폭) · 합계는 롱/숏 각각 표시
alloc_tot = sum(alloc_st.values())
for y_, lab, a_, lo_, sh_, c_ in [(0.603, "합계 (netting 전)", f"{alloc_tot*100:.1f}%", sdf.long.sum(), -sdf.short.sum(), sdf.c.sum()),
                                  (0.589, "합계 (netting 후 = MP)", "", mp_long, -mp_short, tot)]:
    fig.text(0.485, y_, lab, fontsize=7.6, weight="bold", color=INK)   # 파이 아래 (비중 합계이므로 우측 열)
    # 한글은 고정폭 폰트에 글리프가 없어 라벨(롱/숏)과 숫자를 나눠 찍는다 (열 x 고정)
    kw = dict(fontsize=7.6, weight="bold", color=INK)
    fig.text(0.64, y_, f"{a_:>6}", family="monospace", **kw)
    fig.text(0.692, y_, "(롱", **kw); fig.text(0.714, y_, f"{lo_*100:5.1f}%", family="monospace", **kw)
    fig.text(0.767, y_, "/ 숏", **kw); fig.text(0.792, y_, f"{sh_*100:5.1f}%)", family="monospace", **kw)
    fig.text(0.865, y_, f"기여 {bp(c_)}", fontsize=7.6, weight="bold", color=cc(c_))

# ── 2) 팩터: 기여 상·하위 10, 같은 구조
top = fdf.sort_values("c", ascending=False).head(10); bot = fdf.sort_values("c", ascending=True).head(10)
sel = pd.concat([top, bot.iloc[::-1]]).drop_duplicates()
n_top = len(top)
fig.text(0.07, 0.535, "상하위 TOP 10 팩터 기여", fontsize=10.5, weight="bold", color=INK)
ax2 = fig.add_axes([0.215, 0.06, 0.165, 0.44])   # 아래쪽 좌측 절반 (우측은 종목 기여, 2026-10-21)
contrib_chart(ax2, [f"{f_}\n{fname.get(f_, '')[:24]}" for f_ in sel.index], list(sel.c * 1e4), [scol_(x) for x in sel["style"]],
              split_after=n_top, fs=6.2, vfs=6.2)
right_notes(ax2, [wfmt(FW.get(f_, 0.0), r.long, r.short) for f_, r in sel.iterrows()], "비중 (롱숏 평균)", fs=6.2)

# ── 3) 종목: 기여 상·하위 10 (페이지 합계와 같은 이어붙이기·같은 비중), 라벨 = Bloomberg 티커 / 짧은 이름, 막대 색 = 롱/숏
LONG_C, SHORT_C = "#44546a", "#b8c0cc"
def _side(s_):
    return s_["wb_cur"] if s_["wb_cur"] else s_["wb_prev"]   # 전월 북에만 있던 종목은 전월 비중으로
sk = pd.DataFrame([{"tk": s_.get("bbg_ticker") or str(s_["ticker"]).replace(" Equity", "").strip() or s_["isin"],
                    "nm": (s_.get("short_name") or s_["name"])[:18], "c": c_, "w": _side(s_), "r": r_} for s_, c_, r_ in stk])
sk = sk[sk.c != 0]
ts_ = sk.sort_values("c", ascending=False).head(10); bs_ = sk.sort_values("c").head(10)
ss_ = pd.concat([ts_, bs_.iloc[::-1]])
fig.text(0.535, 0.535, "상하위 TOP 10 종목 기여", fontsize=10.5, weight="bold", color=INK)
fig.legend(handles=[Patch(color=LONG_C, label="롱"), Patch(color=SHORT_C, label="숏")], loc="upper left", bbox_to_anchor=(0.80, 0.546),
           ncol=2, fontsize=6.4, frameon=False, handlelength=1.0, columnspacing=1.0, handletextpad=0.4)
ax3 = fig.add_axes([0.665, 0.06, 0.155, 0.44])
contrib_chart(ax3, [f"{t_}\n{n_}" for t_, n_ in zip(ss_.tk, ss_.nm)], list(ss_.c * 1e4), [LONG_C if w_ > 0 else SHORT_C for w_ in ss_.w],
              split_after=len(ts_), fs=6.2, vfs=6.2)
right_notes(ax3, [f"{w_*1e4:+5.1f} {r_*100:+6.1f}%" for w_, r_ in zip(ss_.w, ss_.r)], f"비중bp · {PERIOD} 수익률", fs=6.2)
out_dir = m.OUTPUT_DIR / D["book_date"] / "mtd_pdf"; out_dir.mkdir(parents=True, exist_ok=True)
PFX = "performance_analysis_1M" if "--1m" in sys.argv else (f"performance_analysis_from{START}" if FROM else "mtd_performance_analysis")
out = str(out_dir / f"{PFX}_{FUND.get(BM, BM)}{'_bmuniv' if A else ''}_{ASOF}{f'_gross{GROSS*100:.0f}' if GROSS else ''}{f'_rb{RB[5:7]}{RB[8:10]}' if RB else ''}.pdf")
fig.savefig(out, format="pdf", metadata={"Title": f"{TITLE_NAME.get(BM, BM)} MP Performance Analysis {ASOF}"}); fig.savefig(out.replace(".pdf", ".png"), dpi=130)
print("saved", out)
