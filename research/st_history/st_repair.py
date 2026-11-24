"""가격제한으로 ST 구간 보정 (주판, 2026-07-06 이전). 입력: 공시 기반 구간. 출력: 보정 구간 + 보정 표시.
대폭 변동일 = |ret|>5.6%, 재개일(전일 정지/7일 초과 공백) 제외, 구간 경계 ±3거래일 제외.
트리거 = 20거래일 안에 대폭 변동일 2회 이상.
- 시작 불명(before_first_event): 해제일 전 마지막 트리거 대폭 변동일 다음 거래일을 시작일로
- 그 외: 첫 트리거 대폭 변동일 전날에 구간 종료 (해제 공시 누락)"""
import sys, glob
import pandas as pd, numpy as np
src, dst = sys.argv[1], sys.argv[2]
iv = pd.read_csv(src, dtype={"code": str}, parse_dates=["start", "end"]); iv["code"] = iv.code.str.zfill(6)
cm = pd.read_parquet("code_map.parquet"); cm["k"] = cm.gvkey + cm.iid
cm = cm.sort_values("ed", na_position="last").drop_duplicates("k", keep="last")[["k", "code"]]
cm = cm[cm.code.fillna("").str.match(r"^(60[0-5]|000|001|002|003)") & cm.code.isin(iv.code)]
parts = []
for f in sorted(glob.glob("px/*.parquet")):
    px = pd.read_parquet(f, columns=["k", "d", "c", "aj", "st", "v"]); px["k"] = px.k.astype(str)
    parts.append(px.merge(cm, on="k", how="inner"))
px = pd.concat(parts, ignore_index=True).sort_values(["code", "d"]); del parts
px["adj"] = px.c / px.aj; g = px.groupby("code", sort=False)
px["ret"] = px.adj / g.adj.shift(1) - 1
px["resume"] = (g.st.shift(1) == 5) | ((px.d - g.d.shift(1)).dt.days > 7)
px["tradeday"] = (px.st != 5) & (px.v > 0)
px = px[px.tradeday & (px.d < "2026-07-06")].copy()
px["big"] = (px.ret.abs() > 0.056) & ~px.resume
px["i"] = px.groupby("code").cumcount()
bydays = {c: d for c, d in px.groupby("code")}
out = []
for r in iv.itertuples():
    rec = r._asdict(); rec.pop("Index"); rec["repair"] = ""
    d = bydays.get(r.code)
    if d is None or r.grade is None:
        out.append(rec); continue
    s = r.start if pd.notna(r.start) else d.d.min()
    e = r.end if pd.notna(r.end) else pd.Timestamp("2026-07-05")
    w = d[(d.d >= s) & (d.d <= e)]
    if len(w) < 8:
        out.append(rec); continue
    w = w.iloc[3:-3] if pd.notna(r.start) else w.iloc[:-3]
    # 상장폐지 정리매매(마지막 거래일 전 40거래일)는 가격제한이 달라 보정 근거에서 뺀다
    w = w[w.i < d.i.max() - 40]
    b = w[w.big]
    # 트리거: 20거래일 안에 2회 이상
    trig = b[(b.i.shift(-1) - b.i <= 20) | (b.i - b.i.shift(1) <= 20)]
    if trig.empty:
        out.append(rec); continue
    if pd.isna(r.start):
        last = trig.d.max(); nxt = d[d.d > last].d
        rec["start"] = nxt.iloc[0] if len(nxt) else last; rec["repair"] = "start_by_price"
    else:
        first = trig.d.min(); prev = d[d.d < first].d
        rec["end"] = prev.iloc[-1] if len(prev) else first; rec["repair"] = "end_by_price"
    out.append(rec)
o = pd.DataFrame(out)
o.to_csv(dst, index=False)
print("intervals", len(o), "| repaired:", o.repair.value_counts().to_dict())
print(o[o.repair != ""].sample(12, random_state=1).to_string(index=False))
