"""ST 구간 검증 (연도별 처리, 메모리 절약). 주판(60[0-5], 000-003), 2005-01-01 ~ 2026-07-05.
A) ST 구간 안 |ret|>5.3% 비율  B) 구간 밖 5% 고정(h==l, |ret| 4.8~5.2%) 일수 -> 누락 후보."""
import sys, glob
import pandas as pd, numpy as np
iv = pd.read_csv(sys.argv[1], dtype={"code": str}, parse_dates=["start", "end"])
iv["code"] = iv.code.str.zfill(6); iv["start"] = iv.start.fillna(pd.Timestamp("1990-01-01")); iv["end"] = iv.end.fillna(pd.Timestamp("2100-01-01"))
cm = pd.read_parquet("code_map.parquet"); cm["k"] = cm.gvkey + cm.iid
cm = cm.sort_values("ed", na_position="last").drop_duplicates("k", keep="last")[["k", "code"]]
cm = cm[cm.code.fillna("").str.match(r"^(60[0-5]|000|001|002|003)")]
stats, cands, fps = [], [], []
for f in sorted(glob.glob("px/*.parquet")):
    y = int(f[-12:-8])
    if y < 2005: continue
    px = pd.read_parquet(f); px["k"] = px.k.astype(str)
    px = px.merge(cm, on="k", how="inner").sort_values(["k", "d"])
    px["adj"] = px.c / px.aj; g = px.groupby("k", sort=False)
    px["ret"] = px.adj / g.adj.shift(1) - 1; px["prev_st"] = g.st.shift(1); px["gap"] = (px.d - g.d.shift(1)).dt.days
    px = px[px.ret.notna() & (px.st != 5) & (px.prev_st != 5) & (px.gap <= 7) & (px.d < "2026-07-06") & (px.v > 0)].copy()
    sub = px[["code", "d"]].reset_index().merge(iv[["code", "start", "end", "grade"]], on="code")
    sub = sub[(sub.d >= sub.start) & (sub.d <= sub.end)].drop_duplicates("index").set_index("index")
    px["in_st"] = px.index.isin(sub.index)
    px["big"] = px.ret.abs() > 0.053
    px["lock5"] = (px.h == px.l) & px.ret.abs().between(0.048, 0.052)
    stats.append(dict(yr=y, days=len(px), st_days=int(px.in_st.sum()), in_big=round(px[px.in_st].big.mean(), 4),
                      out_big=round(px[~px.in_st].big.mean(), 4), in_lock5=int(px[px.in_st].lock5.sum()), out_lock5=int(px[~px.in_st].lock5.sum())))
    c = px[~px.in_st & px.lock5].groupby("code").size(); cands.append(pd.DataFrame({"yr": y, "n": c}))
    b = px[px.in_st & px.big].groupby("code").size(); fps.append(pd.DataFrame({"yr": y, "n": b}))
    del px, sub
print(pd.DataFrame(stats).to_string(index=False))
cand = pd.concat(cands).reset_index().rename(columns={"index": "code"}); fp = pd.concat(fps).reset_index().rename(columns={"index": "code"})
cand.to_csv("st_miss_cand.csv", index=False); fp.to_csv("st_fp_cand.csv", index=False)
print("miss candidates (code-year, >=3 locked-5% days outside ST):", (cand.n >= 3).sum(), "| codes", cand[cand.n >= 3].code.nunique())
print(cand[cand.n >= 3].sort_values("n", ascending=False).head(20).to_string(index=False))
print("false-pos candidates (code-year, >=3 days |ret|>5.3% inside ST):", (fp.n >= 3).sum(), "| codes", fp[fp.n >= 3].code.nunique())
print(fp[fp.n >= 3].sort_values("n", ascending=False).head(20).to_string(index=False))
