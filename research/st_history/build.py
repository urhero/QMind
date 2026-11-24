"""이벤트 + PDF 메타 -> ST 구간 테이블.

출력 st_intervals.csv: code, grade(ST|*ST), start, end(NaT=현재까지), start_src, end_src
- 시행일: PDF 의 "自...起" 날짜, 없으면 공시일 다음 평일 (eff_src=approx)
- 이벤트 후 상태: PDF 새 약칭이 있으면 약칭 기준, 없으면 제목 해석(add/remove)
- 첫 이벤트 이전 상태: 그 공시 당시 약칭 (2002년 이전부터 ST 인 종목 -> start=NaT, start_src=pre_2002)
"""
import json

import numpy as np
import pandas as pd

from classify import a_share, load_raw, parse_title


def name_state(n):
    """약칭 -> (star, other). 비ST 면 (0,0). None = 판단 불가."""
    if not isinstance(n, str) or not n:
        return None
    n = n.upper().replace("＊", "*")
    if n.startswith(("*ST", "S*ST")):
        return (1, 0)
    if n.startswith(("ST", "SST")):
        return (0, 1)
    return (0, 0)


def grade(state):
    return "*ST" if state[0] else ("ST" if state[1] else None)


def build(raw="raw.jsonl", meta="pdf_meta.jsonl"):
    df = load_raw(raw)
    df = df[df.code.map(a_share)].copy()
    df["acts"] = df.title.map(parse_title)
    m = pd.DataFrame([json.loads(l) for l in open(meta, encoding="utf-8")]).drop_duplicates("announcementId", keep="last")
    ev = df[df.acts.map(lambda a: any(x[0] in ("add", "remove") for x in a))].merge(m, on="announcementId", how="left")
    ev["eff_pdf"] = pd.to_datetime(ev["eff"])
    ev["eff_date"] = ev.eff_pdf.fillna(ev.ann_date + pd.offsets.BDay(1))
    ev["eff_src"] = np.where(ev.eff_pdf.notna(), "pdf_" + ev.get("eff_rule", pd.Series("", index=ev.index)).fillna(""), "approx")

    def new_name(r):
        names = r.new_names if isinstance(r.new_names, list) else []
        cand = [n for n in names if n != r.sec_name]
        return cand[-1] if cand else None

    ev["new_name"] = ev.apply(new_name, axis=1)
    ev = ev.sort_values(["code", "eff_date", "announcementId"])

    rows, audit = [], []
    for code, g in ev.groupby("code", sort=False):
        # 첫 이벤트 이전 상태: 첫 이벤트가 해제면 그 등급이 이미 걸려 있던 것 (공시 secName 은 현재 이름이 섞여 있어 쓰지 않음)
        first = g.iloc[0]
        rm = {gr for act, gr in first.acts if act == "remove"}
        if rm == {"star", "other"}:  # 옛 "撤销特别处理": 등급은 당시 약칭으로, 없으면 ST
            ns0 = name_state(first.sec_name)
            state = ns0 if ns0 and grade(ns0) else (0, 1)
        else:
            state = (int("star" in rm), int("other" in rm))
        cur_start, cur_src = (pd.NaT, "before_first_event") if grade(state) else (None, None)
        cur_grade = grade(state)
        for r in g.itertuples():
            s = list(state)
            for act, gr in r.acts:
                i = 0 if gr == "star" else 1
                if act == "add":
                    s[i] = 1
                elif act == "remove":
                    s[i] = 0
                elif act == "keep":
                    s[i] = 1
            # *ST 해제 시 ST 유지 문구(并实施/继续实施其他...)가 없으면 정상 종목으로 돌아간다
            if ("remove", "star") in r.acts and not any(gr == "other" and act in ("add", "keep") for act, gr in r.acts):
                s[1] = 0
            # ST 해제 공시가 나왔으면 그 시점에 *ST 가 아니었다 (*ST 유지 문구가 없으면 *ST 해제 누락으로 보고 정상으로)
            if ("remove", "other") in r.acts and not any(gr == "star" and act in ("add", "keep") for act, gr in r.acts):
                s[0] = 0
            title_state = tuple(s)
            # 약칭은 제목이 모호할 때만 쓴다 (본문 연혁 문장의 옛 약칭을 집는 경우가 있어 기본은 제목)
            ns = name_state(r.new_name)
            adds = {gr for act, gr in r.acts if act in ("add", "keep")}
            rems = {gr for act, gr in r.acts if act == "remove"}
            ambiguous = "部分" in r.title or bool(adds & rems)
            new_state = ns if (ambiguous and ns is not None) else title_state
            audit.append((code, r.eff_date, r.title, r.sec_name, r.new_name, grade(title_state), grade(new_state)))
            ng = grade(new_state)
            if ng != cur_grade:
                if cur_grade is not None:
                    rows.append((code, cur_grade, cur_start, r.eff_date - pd.Timedelta(days=1), cur_src, r.eff_src))
                cur_grade, cur_start, cur_src = ng, r.eff_date, r.eff_src
            state = new_state
        if cur_grade is not None:
            rows.append((code, cur_grade, cur_start, pd.NaT, cur_src, None))
    iv = pd.DataFrame(rows, columns=["code", "grade", "start", "end", "start_src", "end_src"])
    au = pd.DataFrame(audit, columns=["code", "eff_date", "title", "sec_name_before", "new_name", "grade_by_title", "grade_final"])
    return iv, au, df


def name_check(iv, df):
    """모든 수집 공시의 당시 약칭 vs 구간 -> 불일치 비율 (이벤트 당일 ±3일 제외)."""
    obs = df[["code", "ann_date", "sec_name"]].copy()
    obs["obs"] = obs.sec_name.map(lambda n: grade(name_state(n) or (0, 0)))
    obs = obs.merge(iv, on="code", how="left")
    inside = (obs.start.isna() | (obs.ann_date >= obs.start)) & (obs.end.isna() | (obs.ann_date <= obs.end)) & obs.grade.notna()
    obs["iv_grade"] = np.where(inside, obs.grade, None)
    o = obs.sort_values("iv_grade", na_position="last").drop_duplicates(["code", "ann_date", "sec_name"])
    near = o.merge(iv[["code", "start"]].rename(columns={"start": "s2"}), on="code", how="left")
    near = near[(near.ann_date - near.s2).abs() <= pd.Timedelta(days=3)][["code", "ann_date"]].drop_duplicates()
    o = o.merge(near.assign(near=1), on=["code", "ann_date"], how="left")
    o = o[o.near.isna()]
    o["iv_grade"] = o.iv_grade.fillna("none")
    o["obs"] = o.obs.fillna("none")
    return o


if __name__ == "__main__":
    iv, au, df = build()
    iv.to_csv("st_intervals.csv", index=False)
    au.to_csv("st_events_audit.csv", index=False)
    print(iv.grade.value_counts().to_dict(), "codes", iv.code.nunique())
    print("start_src", iv.start_src.value_counts(dropna=False).to_dict())
    print("title vs name disagreement:", (au.grade_by_title.fillna("none") != au.grade_final.fillna("none")).sum(), "of", len(au))
    o = name_check(iv, df)
    print("name-observation agreement:", round((o.obs == o.iv_grade).mean(), 4), "n", len(o))
