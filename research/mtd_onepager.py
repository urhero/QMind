# -*- coding: utf-8 -*-
"""종목 기여(임시, BM 유니버스 제한) 기준 스타일/팩터 비중·기여 한 장 PDF (내부 보고용).

사용:  BENCHMARK=MXCN1A python research/mtd_onepager.py [YYYY-MM-DD] [--bbg 9.60] [--gross 0.19] [--1d | --1m | --from YYYY-MM-DD]   # 기본 어제, 날짜는 맨 앞
--1d 는 직전 거래일 종가부터 (최근 1일), --1m 은 기준일 한 달 전 종가부터 (최근 1개월), --from 은 지정일 종가부터. 기간이 전월로 넘어가면 전월 데이터(그 달 북·교대일)를 이어 붙인다.
--gross 는 배포 gross 를 가정값으로 재스케일 (예: 0.19 = 롱/숏 각 9.5%). 파일명에 _gross19 접미사
출력:  output/{BM}/<MP 기준일>/mtd_pdf/mtd_performance_analysis_{KIC|BOK}[_bmuniv]_<asof>[_grossNN].pdf (+ .png 미리보기)
전제:  data/{BM}/bm_universe.csv 가 있으면 BM 유니버스 제한본(_bmuniv), 없으면(MXWO) 원 MP 기준. 일별 캐시는 mtd_dashboard 와 공유.
--bbg 는 bbg PORT Active CTR(bp) 를 머리글에 병기.
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
stk_d = {}   # (isin, ticker) -> [stock(당월 북 우선), 기여, 기간 성장률, 스타일별 기여] — 상하위 종목 차트용 (2026-10-21, 스타일별 2026-10-30)
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
        e = stk_d.setdefault((s["isin"], s["ticker"]), [s, 0.0, 1.0, {}])
        if cur_seg: e[0] = s
        e[1] += c_s; e[2] *= g[ix + 1] / g[i0 + 1]
        if cur_seg:
            for key, v in s["sb_cur"].items(): add(st, key, "long" if v > 0 else "short", v)
        for key in set(s["sb_prev"]) | set(s["sb_cur"]):
            cs_ = seg(s["sb_prev"].get(key, 0.0), s["sb_cur"].get(key, 0.0))
            add(st, key, "c", cs_); e[3][key] = e[3].get(key, 0.0) + cs_
        rp = (s["wb_prev"] / s["w_prev"]) if s["w_prev"] else 0.0; rc = (s["wb_cur"] / s["w_cur"]) if s["w_cur"] else 0.0
        fp = {fx[i]: w * rp for i, w in s["f_prev"]}; fcu = {fx[i]: w * rc for i, w in s["f_cur"]}
        if cur_seg:
            for f, v in fcu.items(): add(fc, f, "long" if v > 0 else "short", v)
        for f in set(fp) | set(fcu):
            add(fc, f, "c", seg(fp.get(f, 0.0), fcu.get(f, 0.0)))
stk = [(s_, c_, gr_ - 1, cst_) for s_, c_, gr_, cst_ in stk_d.values()]

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
from matplotlib.lines import Line2D
# 비중 표기: "배분% (롱% / 숏%)" — 배분 = 팩터 배분 비율(합 100%), 괄호 = 배포 롱/숏 (사용자 지정 2026-09-23)
FW = D["factor_weight"]
alloc_st = {}
for f_, w_ in FW.items():
    alloc_st[fstyle.get(f_, "")] = alloc_st.get(fstyle.get(f_, ""), 0.0) + w_

def clean(ax):
    for sp in ["top", "right", "left"]: ax.spines[sp].set_visible(False)
    ax.spines["bottom"].set_color(LINE); ax.xaxis.grid(True, color=LINE, lw=0.5); ax.set_axisbelow(True); ax.tick_params(length=0)

def contrib_chart(ax, labels, vals, colors, split_after=None, fs=7.0, vfs=6.8, xlabel="기여 (bp)", sym=True, marks=None, vfmt="{:+.1f}", lim_k=1.35,
                  stacks=None, order=None, fold=False):
    """왼쪽 막대 차트 공통: 위가 1등. 막대 끝에 bp 라벨. split_after 행 뒤에 점선. fs/vfs = 라벨/값 글자 크기.
    stacks = 행별 {스타일: 값} 이면 스타일 색 누적 막대 (양수는 0 오른쪽, 음수는 왼쪽으로 쌓음, order 순) + 순합 위치에 검은 눈금.
    labels=None 이면 행 라벨 없음 (옆 칸 두 번째 막대용).
    fold = 위 양수·아래 음수로 갈리는 칸: 음수 줄의 0선을 오른쪽(양수 줄 최대 길이 자리)으로 옮겨 왼쪽으로 뻗게 한다.
    줄마다 반대쪽이 비는 사분면이 없어져 같은 폭에 막대가 약 두 배 길어짐. 0선이 두 개라 눈금 숫자는 숨김 (2026-10-30 사용자 지정)."""
    n_ = len(vals); ys = range(n_); v = np.asarray(vals, dtype=float)
    S = None if stacks is None else [np.array([d.get(st_, 0.0) for d in stacks]) for st_ in order]
    pos = np.clip(v, 0, None) if S is None else sum(np.clip(x_, 0, None) for x_ in S)
    neg = np.clip(v, None, 0) if S is None else sum(np.clip(x_, None, 0) for x_ in S)
    off = np.where(v < 0, max(pos[v >= 0].max(initial=0.0), -neg[v < 0].min(initial=0.0)), 0.0) if fold else np.zeros(n_)
    if S is None:
        ax.barh(ys, v, left=off, color=colors, height=0.62)
    else:
        p_, q_ = off.copy(), off.copy()
        for st_, x_ in zip(order, S):
            ax.barh(ys, np.clip(x_, 0, None), left=p_, color=scol_(st_), height=0.62, lw=0); p_ += np.clip(x_, 0, None)
            ax.barh(ys, np.clip(x_, None, 0), left=q_, color=scol_(st_), height=0.62, lw=0); q_ += np.clip(x_, None, 0)
        for k_ in ys: ax.plot([off[k_] + v[k_]] * 2, [k_ - 0.38, k_ + 0.38], color=INK, lw=1.0, solid_capstyle="butt")
    ends = off + np.where(v >= 0, pos, neg)   # 라벨은 순합 부호 쪽 막대 바깥 끝
    ax.set_yticks(list(ys)); ax.set_yticklabels(labels if labels is not None else [], fontsize=fs, linespacing=1.15); ax.invert_yaxis()
    if fold:   # 0선 = 줄마다 자기 기준 위치 (위 양수 = 왼쪽, 아래 음수 = 오른쪽)
        for k_ in ys: ax.plot([off[k_]] * 2, [k_ - 0.5, k_ + 0.5], color=INK, lw=0.6)
    else:
        ax.axvline(0, color=INK, lw=0.6)
    lim = max(np.abs(np.r_[v, ends]).max(), 1e-9) * lim_k   # lim_k = 막대 끝 숫자 자리 (음수 숫자가 라벨에 붙으면 키움)
    ax._vlab = [ax.text(e_ + (lim * 0.02 if v_ >= 0 else -lim * 0.02), k_, vfmt.format(v_), va="center", ha="left" if v_ >= 0 else "right", fontsize=vfs,
                        color=INK) for k_, (v_, e_) in enumerate(zip(v, ends))]
    # sym=False: 막대가 있는 범위까지만 (빈 쪽은 안 그림, 2026-10-30 스타일별 기여). fold 도 같음. 넘치는 숫자 자리는 _fit_labels 가 넓힘
    if fold:
        lo_, hi_ = min(0.0, (off + neg).min()) - lim * 0.05, max(off.max(), (off + pos).max()) + lim * 0.05
    elif sym:
        lo_, hi_ = -lim, lim
    else:
        lo_, hi_ = min(0.0, (off + neg).min()) * lim_k, max(0.0, (off + pos).max()) * lim_k
    ax.set_xlim(lo_, hi_); ax.set_ylim(n_ - 0.4, -0.6)
    ax.tick_params(axis="x", labelsize=vfs); ax.set_xlabel(xlabel, fontsize=7.5, color=MUTED); clean(ax)
    if fold:   # 눈금 숫자는 숨기되 자리는 남겨 옆 칸과 축 이름 높이를 맞춤
        ax.set_xticks([0.0]); ax.tick_params(axis="x", labelcolor="none"); ax.xaxis.grid(False)
    if split_after is not None: ax.axhline(split_after - 0.5, color=LINE, lw=0.8, ls="--")
    if marks:   # 스타일 색 네모: 라벨과 축 사이 고정 열에 세로 정렬 (막대가 짧아도 스타일이 보이게, 2026-10-22)
        ax.tick_params(axis="y", pad=fs + 6)   # 라벨을 네모 폭만큼 왼쪽으로 띄움
        trb = mtransforms.offset_copy(blend(ax.transAxes, ax.transData), fig=fig, x=-2, units="points")
        for k_, col_ in enumerate(marks):
            ax.text(0, k_, "■", color=col_, fontsize=fs + 0.5, va="center", ha="right", transform=trb)

def right_notes(ax, texts, header, fs=7.0, footer=None):
    # 주석은 축 오른쪽 끝에서 고정 14pt 떨어뜨림 (축 폭 비율로 두면 좁은 차트에서 막대 끝 숫자와 겹침, 2026-10-22)
    tr = mtransforms.offset_copy(blend(ax.transAxes, ax.transData), fig=fig, x=14, units="points")
    ax.text(1.0, -0.75, header, transform=tr, fontsize=min(6.6, fs), color=MUTED, va="bottom")
    for k_, t_ in enumerate(texts):
        ax.text(1.0, k_, t_, transform=tr, fontsize=fs, color=INK, va="center", family="monospace")
    if footer:   # 열 아래 = 줄 순서 기준 (머리글과 같은 글꼴, 2026-10-30)
        ax.text(1.0, len(texts) - 0.35, footer, transform=tr, fontsize=min(6.6, fs), color=MUTED, va="top")

# ── 배치 (2026-10-22 재배치) ─────────────────────────────────────────────────────────────
#   위   = [좌: 배분 도넛(라벨 = 색 네모 + "스타일 (약어)", 범례 겸) / 합계 두 줄 / 스타일별 기여]  [우: 팩터 기여 상하위 10 (두 줄 라벨)]
#   아래 = [좌: 종목 기여 상하위 10]  [우: 액티브 비중 상하위 10]
#   가로는 그린 뒤 실측으로 칸 [LM, CL] / [CR, RM] 에 맞춘다 (라벨 길이가 날짜·펀드마다 달라서). 세로는 고정 좌표.
LM, RM, CL, CR = 0.07, 0.93, 0.475, 0.525
FS = 6.0

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
fig.text(LM, 0.700, "팩터 배분 비중 (롱숏 평균)", fontsize=6.8, color=MUTED)
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
contrib_chart(ax1, [f"{x} ({STYLE_ABBR.get(x, '?')})" for x in sv.index], list(sv.c * 1e4), [scol_(x) for x in sv.index], fs=6.5, vfs=6.5, sym=False)

# ③ 팩터 기여 상하위 10 (오른쪽, 위로 길게 — 두 줄 라벨: "■ 약어 (스타일)" / 팩터명)
top = fdf.sort_values("c", ascending=False).head(10); bot = fdf.sort_values("c", ascending=True).head(10)
sel = pd.concat([top, bot.iloc[::-1]]).drop_duplicates()
n_top = len(top)
fig.text(CR, 0.872, "상하위 Top 10 팩터 기여", fontsize=10.5, weight="bold", color=INK)
# 두 칸 막대 (아래 두 차트와 같은 틀, 2026-11-02 사용자 지정): 왼쪽 = 배분 비중, 오른쪽 = 기여(fold). 오른쪽 숫자 = 롱숏 평균. 줄 순서 = 기여 내림차순
ax2w = fig.add_axes([0.70, 0.505, 0.06, 0.335]); ax2 = fig.add_axes([0.77, 0.505, 0.06, 0.335])
contrib_chart(ax2w, [f"{f_} ({STYLE_ABBR.get(r['style'], '?')})\n{fname.get(f_, '')[:24]}" for f_, r in sel.iterrows()], [FW.get(f_, 0.0) * 100 for f_ in sel.index],
              [scol_(x) for x in sel["style"]], split_after=n_top, fs=FS, vfs=FS, marks=[scol_(x) for x in sel["style"]], xlabel="배분 비중 (%)", sym=False, vfmt="{:.1f}", lim_k=1.1)
contrib_chart(ax2, None, list(sel.c * 1e4), [scol_(x) for x in sel["style"]], split_after=n_top, fs=FS, vfs=FS, lim_k=1.1, fold=True)
right_notes(ax2, [f"{(r.long - r.short) / 2 * 100:4.1f}%" for f_, r in sel.iterrows()], "롱숏 평균", fs=FS, footer="기여 내림차순")

# ④ 종목 기여 상하위 10 (아래 왼쪽) — 페이지 합계와 같은 이어붙이기·같은 비중, 라벨 = bbg 티커 + 짧은 이름, 막대 = 스타일 색 누적 (2026-10-30)
def _side(s_):
    return s_["wb_cur"] if s_["wb_cur"] else s_["wb_prev"]   # 전월 북에만 있던 종목은 전월 비중으로
def _side_st(s_):
    return s_["sb_cur"] if s_["wb_cur"] else s_["sb_prev"]   # 같은 기준의 스타일별 비중 (합 = _side)
ST_ORDER = list(pv.index)   # 누적 순서 = 도넛(배분 내림차순)


def style_legend(x_right):   # 아래 두 차트 막대 = 스타일 색. 약어 범례 + 순합 눈금
    h_ = [Patch(color=scol_(x), label=STYLE_ABBR.get(x, x)) for x in ST_ORDER]
    h_.append(Line2D([0], [0], color=INK, lw=1.0, marker="|", markersize=7, ls="None", label="순합"))
    fig.legend(handles=h_, loc="upper right", bbox_to_anchor=(x_right, 0.458), ncol=(len(h_) + 1) // 2, fontsize=6.0, frameon=False,
               handlelength=0.9, handleheight=0.7, columnspacing=0.7, handletextpad=0.3, labelspacing=0.25)
def _tk(s_):
    return s_.get("bbg_ticker") or str(s_["ticker"]).replace(" Equity", "").strip() or s_["isin"]
def _nm(s_):
    return (s_.get("short_name") or s_["name"])[:18]
sk = pd.DataFrame([{"tk": _tk(s_), "nm": _nm(s_), "c": c_, "w": _side(s_), "r": r_, "wst": _side_st(s_), "cst": cst_} for s_, c_, r_, cst_ in stk])
sk = sk[sk.c != 0]
ts_ = sk.sort_values("c", ascending=False).head(10); bs_ = sk.sort_values("c").head(10)
ss_ = pd.concat([ts_, bs_.iloc[::-1]])
fig.text(LM, 0.445, "상하위 Top 10 종목 기여", fontsize=10.5, weight="bold", color=INK)
style_legend(CL)
# 두 칸 막대 (액티브 비중과 같은 틀, 2026-11-02 사용자 지정): 왼쪽 = 비중, 오른쪽 = 기여(fold), 둘 다 스타일 색 누적. 오른쪽 숫자 = 수익률. 줄 순서 = 기여 내림차순
ax3w = fig.add_axes([0.20, 0.07, 0.09, 0.35]); ax3 = fig.add_axes([0.30, 0.07, 0.09, 0.35])
contrib_chart(ax3w, [f"{t_}  {n_[:15]}" for t_, n_ in zip(ss_.tk, ss_.nm)], list(ss_.w * 1e4), None, split_after=len(ts_), fs=FS, vfs=5.6, xlabel="비중 (bp)", lim_k=1.1,
              stacks=[{k: v * 1e4 for k, v in d.items()} for d in ss_.wst], order=ST_ORDER)
contrib_chart(ax3, None, list(ss_.c * 1e4), None, split_after=len(ts_), fs=FS, vfs=5.6,
              stacks=[{k: v * 1e4 for k, v in d.items()} for d in ss_.cst], order=ST_ORDER, lim_k=1.1, fold=True)
right_notes(ax3, [f"{r_*100:+6.1f}%" for r_ in ss_.r], f"{PERIOD} 수익률", fs=FS, footer="기여 내림차순")

# ⑤ 베팅 종목 — 현재 북 배포 비중 기준 롱 상위 / 숏 상위 (아래 오른쪽, 위아래로)
cur_ids = {id(x) for x in D["stocks"]}
bk = pd.DataFrame([{"tk": _tk(s_), "nm": _nm(s_), "w": s_["wb_cur"], "r": r_, "c": c_, "wst": s_["sb_cur"], "cst": cst_}
                   for s_, c_, r_, cst_ in stk if id(s_) in cur_ids])
# 롱 상위 10 위 / 숏 상위 10 아래를 한 차트로 (왼쪽 종목 기여와 같은 틀, 2026-10-22)
dd = pd.concat([bk[bk.w > 0].nlargest(10, "w"), bk[bk.w < 0].nsmallest(10, "w").iloc[::-1]])   # 숏도 내림차순 (가장 큰 숏이 맨 아래, 다른 차트와 같은 순서, 2026-10-30)
fig.text(CR, 0.445, "상하위 Top 10 액티브 비중", fontsize=10.5, weight="bold", color=INK)
style_legend(RM)
# 두 칸 막대 (2026-10-30 사용자 지정): 첫 칸 = 현재 북 비중(제목 값), 둘째 칸 = 기여, 둘 다 스타일 색 누적. 오른쪽 숫자 = 수익률. 줄 순서 = 비중 순
axb = fig.add_axes([0.70, 0.07, 0.09, 0.35]); axbc = fig.add_axes([0.80, 0.07, 0.09, 0.35])
_n_long = int((dd.w > 0).sum())
contrib_chart(axb, [f"{t_}  {n_[:15]}" for t_, n_ in zip(dd.tk, dd.nm)], list(dd.w * 1e4), None, split_after=_n_long, fs=FS, vfs=5.6,
              xlabel="비중 (bp)", stacks=[{k: v * 1e4 for k, v in d.items()} for d in dd.wst], order=ST_ORDER, lim_k=1.1, fold=True)
contrib_chart(axbc, None, list(dd.c * 1e4), None, split_after=_n_long, fs=FS, vfs=5.6, lim_k=1.1,
              stacks=[{k: v * 1e4 for k, v in d.items()} for d in dd.cst], order=ST_ORDER)
right_notes(axbc, [f"{r_*100:+6.1f}%" for r_ in dd.r], f"{PERIOD} 수익률", fs=FS, footer="비중 내림차순")

# ── 가로 정렬: 실측(tight bbox) 으로 각 차트의 [라벨 ~ 오른쪽 주석] 을 칸에 맞춤 (막대 폭이 여유를 흡수)
_inv = fig.transFigure.inverted()


def _bb(objs):
    rr = fig.canvas.get_renderer()
    bbs = [_inv.transform(o.get_tightbbox(rr)) for o in objs]
    return min(b[0][0] for b in bbs), min(b[0][1] for b in bbs), max(b[1][0] for b in bbs), max(b[1][1] for b in bbs)


def _fit_labels(ax, pad=2.0):
    """막대 끝 숫자가 축 밖(행 라벨·옆 칸)으로 넘치면 넘친 만큼만 x 범위를 넓힌다 (2026-10-30, 좁은 두 칸 막대용)."""
    for _ in range(3):
        fig.canvas.draw(); rr = fig.canvas.get_renderer(); bb = ax.get_window_extent(rr); lo, hi = ax.get_xlim(); dpp = (hi - lo) / bb.width
        ext = [t_.get_window_extent(rr) for t_ in ax._vlab]
        over_l = max(0.0, bb.x0 - min(e.x0 for e in ext) + pad); over_r = max(0.0, max(e.x1 for e in ext) - bb.x1 + pad)
        if over_l <= pad and over_r <= pad: break
        ax.set_xlim(lo - (over_l * dpp if over_l > pad else 0.0), hi + (over_r * dpp if over_r > pad else 0.0))


def _fit_x(ax, c0, c1):
    for _ in range(3):   # 폭을 바꾸면 라벨 위치가 조금 움직임 -> 반복 수렴
        _fit_labels(ax)
        fig.canvas.draw(); x0, _y0, x1, _y1 = _bb([ax]); p = ax.get_position()
        ax.set_position([p.x0 + (c0 - x0), p.y0, max(p.width + (c1 - c0) - (x1 - x0), 0.05), p.height])


def _fit_pair(a, b, c0, c1, gap=0.010):
    """두 칸 막대: [a 라벨 ~ b 주석] 을 칸에 맞추고 두 칸 폭은 같게, 사이 간격 gap (a 의 라벨·숫자 여백은 실측으로 뺀다)."""
    for _ in range(4):
        _fit_labels(a); _fit_labels(b)
        fig.canvas.draw()
        xa0, _, xa1, _ = _bb([a]); xb0, _, xb1, _ = _bb([b]); pa, pb = a.get_position(), b.get_position()
        la, ra, lb, rb = pa.x0 - xa0, xa1 - pa.x1, pb.x0 - xb0, xb1 - pb.x1
        w = max(((c1 - c0) - la - ra - gap - lb - rb) / 2, 0.04)
        a.set_position([c0 + la, pa.y0, w, pa.height])
        b.set_position([c0 + la + w + ra + gap + lb, pb.y0, w, pb.height])


_fit_x(ax1, LM, CL)
_fit_pair(ax2w, ax2, CR, RM); _fit_pair(ax3w, ax3, LM, CL); _fit_pair(axb, axbc, CR, RM)
fig.canvas.draw(); _x0, _, _x1, _ = _bb([axp]); _p = axp.get_position()
axp.set_position([_p.x0 + (LM - _x0) + max(0.0, ((CL - LM) - (_x1 - _x0)) / 2), _p.y0, _p.width, _p.height])   # 도넛: 왼쪽 칸 가운데 (넘치면 왼쪽 여백 기준)
out_dir = m.OUTPUT_DIR / D["book_date"] / "mtd_pdf"; out_dir.mkdir(parents=True, exist_ok=True)
PFX = "performance_analysis_1D" if "--1d" in sys.argv else "performance_analysis_1M" if "--1m" in sys.argv else (f"performance_analysis_from{START}" if FROM else "mtd_performance_analysis")
out = str(out_dir / f"{PFX}_{FUND.get(BM, BM)}{'_bmuniv' if A else ''}_{ASOF}{f'_gross{GROSS*100:.0f}' if GROSS else ''}{f'_rb{RB[5:7]}{RB[8:10]}' if RB else ''}{'_client' if '--client' in sys.argv else ''}.pdf")
fig.savefig(out, format="pdf", metadata={"Title": f"{TITLE_NAME.get(BM, BM)} MP Performance Analysis {ASOF}"}); fig.savefig(out.replace(".pdf", ".png"), dpi=130)
print("saved", out)
