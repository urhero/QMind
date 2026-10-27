# -*- coding: utf-8 -*-
"""종목 기여(임시, BM 유니버스 제한) 기준 스타일/팩터 비중·기여 한 장 PDF (내부 보고용).

사용:  BENCHMARK=MXCN1A python research/mtd_onepager.py [YYYY-MM-DD] [--bbg 9.60] [--gross 0.19] [--1d | --1m | --from YYYY-MM-DD]   # 기본 어제, 날짜는 맨 앞
--1d 는 직전 거래일 종가부터 (최근 1일), --1m 은 기준일 한 달 전 종가부터 (최근 1개월), --from 은 지정일 종가부터. 기간이 전월로 넘어가면 전월 데이터(그 달 북·교대일)를 이어 붙인다.
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
if "--1d" in sys.argv:   # 직전 거래일 종가부터 (월 첫 거래일이면 전월말 = base, 2026-10-23)
    FROM = D["dates"][-2] if len(D["dates"]) > 1 else D["base"]
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
PERIOD = "최근 1일" if "--1d" in sys.argv else "최근 1개월" if "--1m" in sys.argv else ("기간" if FROM else "MTD")
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
if "--client" in sys.argv:   # 수익자 첨부용: 손익 수치를 색 없이 검정으로 (2026-10-26)
    UP = DOWN = INK
fig = plt.figure(figsize=(8.27, 11.69)); fig.patch.set_facecolor("white")
pct = lambda x, d=2: f"{x*100:+.{d}f}%"; pu = lambda x, d=2: f"{x*100:.{d}f}%"; bp = lambda x: f"{x*1e4:+.1f}bp"

FUND = {"MXWO": "KIC", "MXCN1A": "BOK"}  # 파일명 펀드 표기 (2026-10-21)
TITLE_NAME = {"MXWO": "QMIND KIC", "MXCN1A": "QMind BOK"}  # PDF 제목 표기 (사용자 지정 2026-09-21); 파일명·폴더는 유니버스 코드 유지
fig.text(0.07, 0.955, f"{TITLE_NAME.get(BM, f'QMind {BM}')} MP 성과 분석", fontsize=14, weight="bold", color=INK)
if "--client" in sys.argv:   # 수익자 첨부용: 내부 운용 정보(리밸런싱 교대·배포 gross) 생략 (2026-10-23)
    fig.text(0.07, 0.933, f"{START} → {ASOF} 종가 기준.", fontsize=8.5, color=MUTED)
elif Dp is not None:
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
                "Analyst Expectations": "#b8552f", "Price Momentum": "#9bbf2a", "Volatility": "#8a8f98", "Size": "#3f9fc2"}
scol_ = lambda st_: STYLE_COLORS.get(st_, "#9aa0a6")
from matplotlib.transforms import blended_transform_factory as blend
import matplotlib.transforms as mtransforms
STYLE_ABBR = {"Analyst Expectations": "AE", "Capital Efficiency": "CE", "Earnings Quality": "EQ", "Historical Growth": "HG",
              "Price Momentum": "PM", "Size": "SZ", "Valuation": "VA", "Volatility": "VO"}   # 흑백 인쇄용 약어 (2026-10-22)
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

def contrib_chart(ax, labels, vals, colors, split_after=None, fs=7.0, vfs=6.8, xlabel="기여 (bp)", sym=True, marks=None):
    """왼쪽 막대 차트 공통: 위가 1등. 막대 끝에 bp 라벨. split_after 행 뒤에 점선. fs/vfs = 라벨/값 글자 크기."""
    n_ = len(vals); ys = range(n_)
    ax.barh(ys, vals, color=colors, height=0.62)
    ax.set_yticks(list(ys)); ax.set_yticklabels(labels, fontsize=fs, linespacing=1.15); ax.invert_yaxis()
    ax.axvline(0, color=INK, lw=0.6)
    lim = max(abs(min(vals)), abs(max(vals)), 1e-9) * 1.35
    for k_, v in enumerate(vals):
        ax.text(v + (lim * 0.02 if v >= 0 else -lim * 0.02), k_, f"{v:+.1f}", va="center", ha="left" if v >= 0 else "right", fontsize=vfs, color=INK)
    # sym=False: 한쪽 부호뿐이면 그쪽만 그린다 (비중 막대용)
    lo_, hi_ = (-lim, lim) if sym or (min(vals) < 0 < max(vals)) else ((0, lim) if min(vals) >= 0 else (-lim, 0))
    ax.set_xlim(lo_, hi_); ax.set_ylim(n_ - 0.4, -0.6)
    ax.tick_params(axis="x", labelsize=vfs); ax.set_xlabel(xlabel, fontsize=7.5, color=MUTED); clean(ax)
    if split_after is not None: ax.axhline(split_after - 0.5, color=LINE, lw=0.8, ls="--")
    if marks:   # 스타일 색 네모: 라벨과 축 사이 고정 열에 세로 정렬 (막대가 짧아도 스타일이 보이게, 2026-10-22)
        ax.tick_params(axis="y", pad=fs + 6)   # 라벨을 네모 폭만큼 왼쪽으로 띄움
        trb = mtransforms.offset_copy(blend(ax.transAxes, ax.transData), fig=fig, x=-2, units="points")
        for k_, col_ in enumerate(marks):
            ax.text(0, k_, "■", color=col_, fontsize=fs + 0.5, va="center", ha="right", transform=trb)

def right_notes(ax, texts, header, fs=7.0):
    # 주석은 축 오른쪽 끝에서 고정 14pt 떨어뜨림 (축 폭 비율로 두면 좁은 차트에서 막대 끝 숫자와 겹침, 2026-10-22)
    tr = mtransforms.offset_copy(blend(ax.transAxes, ax.transData), fig=fig, x=14, units="points")
    ax.text(1.0, -0.75, header, transform=tr, fontsize=min(6.6, fs), color=MUTED, va="bottom")
    for k_, t_ in enumerate(texts):
        ax.text(1.0, k_, t_, transform=tr, fontsize=fs, color=INK, va="center", family="monospace")

# ── 배치 (2026-10-22 재배치) ─────────────────────────────────────────────────────────────
#   위   = [좌: 배분 도넛(라벨 = 색 네모 + "스타일 (약어)", 범례 겸) / 합계 두 줄 / 스타일별 기여]  [우: 팩터 기여 상하위 10 (두 줄 라벨)]
#   아래 = [좌: 종목 기여 상하위 10]  [우: 액티브 비중 상하위 10]
#   가로는 그린 뒤 실측으로 칸 [LM, CL] / [CR, RM] 에 맞춘다 (라벨 길이가 날짜·펀드마다 달라서). 세로는 고정 좌표.
LM, RM, CL, CR = 0.07, 0.93, 0.475, 0.525
FS = 6.0
LONG_C, SHORT_C = "#44546a", "#b8c0cc"

# ① 배분 도넛 — 범례 겸용: 배분 내림차순, 12시부터 시계방향, 라벨은 조각 바깥 지시선
pv = sdf.assign(alloc=[alloc_st.get(x, 0.0) for x in sdf.index]).sort_values("alloc", ascending=False)
axp = fig.add_axes([LM, 0.712, CL - LM, 0.175]); axp.set_aspect("equal")
wedges, _ = axp.pie(pv.alloc, colors=[scol_(x) for x in pv.index], startangle=90, counterclock=False, radius=0.75,
                    wedgeprops=dict(width=0.34, edgecolor="white", linewidth=1.0))
axp.set_xlim(-2.45, 2.45); axp.set_ylim(-1.18, 1.18)
labs = []
for w_, (s_, r) in zip(wedges, pv.iterrows()):
    ang = np.deg2rad((w_.theta1 + w_.theta2) / 2)
    labs.append(dict(name=f"{s_} ({STYLE_ABBR.get(s_, '?')})", col=scol_(s_), txt=f"{r.alloc*100:.1f}% ({(r.long - r.short) / 2 * 100:.1f}%)",
                     x=np.cos(ang), y=np.sin(ang)))
anns = []
for side in (1, -1):
    grp = sorted([l_ for l_ in labs if (l_["x"] >= 0) == (side > 0)], key=lambda l_: l_["y"], reverse=True)
    ys = [min(l_["y"] * 1.05, 0.98) for l_ in grp]
    for q in range(1, len(ys)):                       # 위에서부터 최소 간격 확보
        ys[q] = min(ys[q], ys[q - 1] - 0.36)
    if ys and ys[-1] < -1.08: ys = [y_ + (-1.08 - ys[-1]) for y_ in ys]
    for l_, ty in zip(grp, ys):
        a_ = axp.annotate(f"{l_['name']}\n{l_['txt']}", xy=(l_["x"] * 0.76, l_["y"] * 0.76), xytext=(side * 1.16, ty),
                          ha="left" if side > 0 else "right", va="center", fontsize=6.3, color=INK, linespacing=1.25,
                          arrowprops=dict(arrowstyle="-", color=MUTED, lw=0.5, shrinkB=1,   # 지시선은 글자 가장자리에서 끝남 (가운데까지 관통 X, 2026-10-22)
                                          relpos=(0, 0.5) if side > 0 else (1, 0.5), shrinkA=9 if side > 0 else 2))   # 오른쪽은 색 네모 폭만큼 더 띄움
        anns.append((a_, l_["col"]))
fig.canvas.draw(); _rr = fig.canvas.get_renderer(); _toD = axp.transData.inverted()
for a_, col_ in anns:   # 라벨 첫 줄 앞에 색 네모 (데이터 좌표라 축이 옮겨져도 라벨에 붙어 있음)
    bb_ = matplotlib.text.Text.get_window_extent(a_, _rr)   # 지시선 제외, 글자 영역만
    xd, yd = _toD.transform((bb_.x0 - 2, bb_.y1 - bb_.height / 4))
    axp.text(xd, yd, "■", color=col_, fontsize=7.0, ha="right", va="center")
fig.text(LM, 0.700, "팩터 배분 비중 (합 100%) · 괄호 = 배포 롱숏 평균", fontsize=6.8, color=MUTED)
mp_long = sum(max(s_["wb_cur"], 0) for s_ in D["stocks"]); mp_short = sum(min(s_["wb_cur"], 0) for s_ in D["stocks"])
# 합계 두 줄: 라벨/비중/기여 열 정렬 (라벨 = 비례 폰트, 숫자 = 고정폭) · 합계는 롱/숏 각각 표시
alloc_tot = sum(alloc_st.values())
for y_, lab, a_, lo_, sh_, c_ in [(0.684, "합계 (netting 전)", f"{alloc_tot*100:.1f}%", sdf.long.sum(), -sdf.short.sum(), sdf.c.sum()),
                                  (0.671, "합계 (netting 후 = MP)", "", mp_long, -mp_short, tot)]:
    kw = dict(fontsize=7.2, weight="bold", color=INK)
    fig.text(LM, y_, lab, **kw)
    # 한글은 고정폭 폰트에 글리프가 없어 라벨(롱/숏)과 숫자를 나눠 찍는다 (열 x 고정)
    fig.text(LM + 0.108, y_, f"{a_:>6}", family="monospace", **kw)
    fig.text(LM + 0.166, y_, "(롱", **kw); fig.text(LM + 0.185, y_, f"{lo_*100:5.1f}%", family="monospace", **kw)
    fig.text(LM + 0.232, y_, "/ 숏", **kw); fig.text(LM + 0.254, y_, f"{sh_*100:5.1f}%)", family="monospace", **kw)
    fig.text(CL, y_, f"기여 {bp(c_)}", fontsize=7.2, weight="bold", color=cc(c_), ha="right")

# ② 스타일별 기여 (도넛 아래)
sv = sdf.sort_values("c", ascending=False)
fig.text(LM, 0.640, "팩터 스타일별 기여", fontsize=10.5, weight="bold", color=INK)
ax1 = fig.add_axes([0.20, 0.505, 0.22, 0.12])
contrib_chart(ax1, [f"{x} ({STYLE_ABBR.get(x, '?')})" for x in sv.index], list(sv.c * 1e4), [scol_(x) for x in sv.index], fs=6.5, vfs=6.5)

# ③ 팩터 기여 상하위 10 (오른쪽, 위로 길게 — 두 줄 라벨: "■ 약어 (스타일)" / 팩터명)
top = fdf.sort_values("c", ascending=False).head(10); bot = fdf.sort_values("c", ascending=True).head(10)
sel = pd.concat([top, bot.iloc[::-1]]).drop_duplicates()
n_top = len(top)
fig.text(CR, 0.872, "상하위 Top 10 팩터 기여", fontsize=10.5, weight="bold", color=INK)
ax2 = fig.add_axes([0.70, 0.505, 0.13, 0.335])
contrib_chart(ax2, [f"{f_} ({STYLE_ABBR.get(r['style'], '?')})\n{fname.get(f_, '')[:24]}" for f_, r in sel.iterrows()], list(sel.c * 1e4),
              [scol_(x) for x in sel["style"]], split_after=n_top, fs=FS, vfs=FS, marks=[scol_(x) for x in sel["style"]])
right_notes(ax2, [wfmt(FW.get(f_, 0.0), r.long, r.short) for f_, r in sel.iterrows()], "비중 (롱숏 평균)", fs=FS)

# ④ 종목 기여 상하위 10 (아래 왼쪽) — 페이지 합계와 같은 이어붙이기·같은 비중, 라벨 = Bloomberg 티커 + 짧은 이름, 막대 색 = 롱/숏
def _side(s_):
    return s_["wb_cur"] if s_["wb_cur"] else s_["wb_prev"]   # 전월 북에만 있던 종목은 전월 비중으로
def _tk(s_):
    return s_.get("bbg_ticker") or str(s_["ticker"]).replace(" Equity", "").strip() or s_["isin"]
def _nm(s_):
    return (s_.get("short_name") or s_["name"])[:18]
sk = pd.DataFrame([{"tk": _tk(s_), "nm": _nm(s_), "c": c_, "w": _side(s_), "r": r_} for s_, c_, r_ in stk])
sk = sk[sk.c != 0]
ts_ = sk.sort_values("c", ascending=False).head(10); bs_ = sk.sort_values("c").head(10)
ss_ = pd.concat([ts_, bs_.iloc[::-1]])
fig.text(LM, 0.445, "상하위 Top 10 종목 기여", fontsize=10.5, weight="bold", color=INK)
fig.legend(handles=[Patch(color=LONG_C, label="롱"), Patch(color=SHORT_C, label="숏")], loc="upper right", bbox_to_anchor=(CL, 0.456),
           ncol=2, fontsize=6.4, frameon=False, handlelength=1.0, columnspacing=1.0, handletextpad=0.4)
ax3 = fig.add_axes([0.20, 0.07, 0.155, 0.35])
contrib_chart(ax3, [f"{t_}  {n_}" for t_, n_ in zip(ss_.tk, ss_.nm)], list(ss_.c * 1e4), [LONG_C if w_ > 0 else SHORT_C for w_ in ss_.w],
              split_after=len(ts_), fs=FS, vfs=FS)
right_notes(ax3, [f"{w_*1e4:+5.1f} {r_*100:+6.1f}%" for w_, r_ in zip(ss_.w, ss_.r)], f"비중bp · {PERIOD} 수익률", fs=FS)

# ⑤ 베팅 종목 — 현재 북 배포 비중 기준 롱 상위 / 숏 상위 (아래 오른쪽, 위아래로)
cur_ids = {id(x) for x in D["stocks"]}
bk = pd.DataFrame([{"tk": _tk(s_), "nm": _nm(s_), "w": s_["wb_cur"], "r": r_, "c": c_} for s_, c_, r_ in stk if id(s_) in cur_ids])
# 롱 상위 10 위 / 숏 상위 10 아래를 한 차트로 (왼쪽 종목 기여와 같은 틀, 2026-10-22)
dd = pd.concat([bk[bk.w > 0].nlargest(10, "w"), bk[bk.w < 0].nsmallest(10, "w")])
fig.text(CR, 0.445, "상하위 Top 10 액티브 비중", fontsize=10.5, weight="bold", color=INK)
axb = fig.add_axes([0.70, 0.07, 0.13, 0.35])
contrib_chart(axb, [f"{t_}  {n_}" for t_, n_ in zip(dd.tk, dd.nm)], list(dd.w * 1e4), [LONG_C if w_ > 0 else SHORT_C for w_ in dd.w],
              split_after=int((dd.w > 0).sum()), fs=FS, vfs=FS, xlabel="현재 북 배포 비중 (bp)")
right_notes(axb, [f"{r_*100:+6.1f}% {c_*1e4:+5.1f}" for r_, c_ in zip(dd.r, dd.c)], f"{PERIOD} 수익률 · 기여bp", fs=FS)
bet_axes = [axb]

# ── 가로 정렬: 실측(tight bbox) 으로 각 차트의 [라벨 ~ 오른쪽 주석] 을 칸에 맞춤 (막대 폭이 여유를 흡수)
_inv = fig.transFigure.inverted()


def _bb(objs):
    rr = fig.canvas.get_renderer()
    bbs = [_inv.transform(o.get_tightbbox(rr)) for o in objs]
    return min(b[0][0] for b in bbs), min(b[0][1] for b in bbs), max(b[1][0] for b in bbs), max(b[1][1] for b in bbs)


def _fit_x(ax, c0, c1):
    for _ in range(3):   # 폭을 바꾸면 라벨 위치가 조금 움직임 -> 반복 수렴
        fig.canvas.draw(); x0, _y0, x1, _y1 = _bb([ax]); p = ax.get_position()
        ax.set_position([p.x0 + (c0 - x0), p.y0, max(p.width + (c1 - c0) - (x1 - x0), 0.05), p.height])


for a_, c0_, c1_ in [(ax1, LM, CL), (ax2, CR, RM), (ax3, LM, CL)] + [(b_, CR, RM) for b_ in bet_axes]:
    _fit_x(a_, c0_, c1_)
fig.canvas.draw(); _x0, _, _x1, _ = _bb([axp]); _p = axp.get_position()
axp.set_position([_p.x0 + (LM - _x0) + max(0.0, ((CL - LM) - (_x1 - _x0)) / 2), _p.y0, _p.width, _p.height])   # 도넛: 왼쪽 칸 가운데 (넘치면 왼쪽 여백 기준)
out_dir = m.OUTPUT_DIR / D["book_date"] / "mtd_pdf"; out_dir.mkdir(parents=True, exist_ok=True)
PFX = "performance_analysis_1D" if "--1d" in sys.argv else "performance_analysis_1M" if "--1m" in sys.argv else (f"performance_analysis_from{START}" if FROM else "mtd_performance_analysis")
out = str(out_dir / f"{PFX}_{FUND.get(BM, BM)}{'_bmuniv' if A else ''}_{ASOF}{f'_gross{GROSS*100:.0f}' if GROSS else ''}{f'_rb{RB[5:7]}{RB[8:10]}' if RB else ''}{'_client' if '--client' in sys.argv else ''}.pdf")
fig.savefig(out, format="pdf", metadata={"Title": f"{TITLE_NAME.get(BM, BM)} MP Performance Analysis {ASOF}"}); fig.savefig(out.replace(".pdf", ".png"), dpi=130)
print("saved", out)
