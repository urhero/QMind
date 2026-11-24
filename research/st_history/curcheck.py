import json, pandas as pd
from build import build, name_state, grade
iv, au, df = build()
iv.to_csv("st_intervals.csv", index=False); au.to_csv("st_events_audit.csv", index=False)
cur = pd.DataFrame(json.load(open("szse_stock.json"))["stockList"])
cur = cur[cur.code.str.match(r"^(60|68|00|30)\d{4}$")].copy()
cur["truth"] = cur.zwjc.str.replace(" ","").map(lambda n: grade(name_state(n) or (0,0))).fillna("none")
open_ = iv[iv.end.isna()].drop_duplicates("code", keep="last").set_index("code").grade
cur["mine"] = cur.code.map(open_).fillna("none")
print(pd.crosstab(cur.truth, cur.mine))
bad = cur[cur.truth != cur.mine]; bad.to_csv("current_mismatch.csv", index=False)
