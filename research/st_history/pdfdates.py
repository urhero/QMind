"""이벤트 공시 PDF -> 시행일, 새 약칭.

1) PDF 앞 3쪽 텍스트를 texts/<announcementId>.txt 로 저장 (재실행 시 이어서)
2) 저장된 텍스트를 파싱해 pdf_meta.jsonl 을 새로 쓴다
시행일 우선순위 (공시일-3일 ~ +120일 안의 날짜만):
  a. "...起始日：2022年5月26日"          (상하이/선전 표준 양식, 2012년 이후)
  b. "并于2022年5月26日复牌" / "...日起复牌"  (정지 후 재개일 = 새 약칭 적용일)
  c. "自/于/从 2006年4月28日 (开市)起"      (가장 이른 날)
새 약칭: "简称...变更为“X”" 의 X
"""
import concurrent.futures as cf
import json
import os
import re
import subprocess
import sys
import tempfile
import time

import pandas as pd
import requests

BASE = "https://static.cninfo.com.cn/"
D = r"(\d{4})年(\d{1,2})月(\d{1,2})日"
START = re.compile(r"起始日[:：]?" + D)
RESUME = re.compile(r"于" + D + r"(?:开市起)?复牌")
DATE = re.compile(r"(?:自|于|从)" + D + r"(?:开市|起|开始)")
NEWNAME = re.compile(r"简称(?:由|将由)?[^。；;]{0,30}?变更为[“\"「]([^”\"」]{2,12})[”\"」]")
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0"})
TXT = "texts"


def fetch(row):
    aid, url = row
    path = os.path.join(TXT, f"{aid}.txt")
    if os.path.exists(path):
        return aid, "cached"
    for i in range(5):
        try:
            r = S.get(BASE + url, timeout=60)
            r.raise_for_status()
            break
        except Exception as e:  # noqa: BLE001
            err = e
            time.sleep(3 * (i + 1))
    else:
        return aid, f"err {str(err)[:80]}"
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(r.content)
        f.flush()
        txt = subprocess.run(["pdftotext", "-l", "3", "-enc", "UTF-8", f.name, "-"], capture_output=True, timeout=60).stdout.decode("utf-8", "ignore")
    open(path, "w", encoding="utf-8").write(re.sub(r"\s+", "", txt))
    return aid, "ok"


def _dates(rx, flat, ann):
    out = []
    for y, m, d in rx.findall(flat):
        try:
            dt = pd.Timestamp(int(y), int(m), int(d))
        except ValueError:
            continue
        if ann - pd.Timedelta(days=3) <= dt <= ann + pd.Timedelta(days=120):
            out.append(dt)
    return out


def parse_text(flat: str, ann: pd.Timestamp) -> dict:
    for src, rx in (("start", START), ("resume", RESUME), ("from", DATE)):
        ds = _dates(rx, flat, ann)
        if ds:
            eff, eff_rule = min(ds), src
            break
    else:
        eff, eff_rule = None, None
    names = NEWNAME.findall(flat)
    return {"eff": str(eff.date()) if eff is not None else None, "eff_rule": eff_rule, "new_names": names[:3], "nchar": len(flat)}


if __name__ == "__main__":
    os.makedirs(TXT, exist_ok=True)
    ev = pd.read_parquet(sys.argv[1])
    todo = [(r.announcementId, r.adjunctUrl) for r in ev.itertuples() if isinstance(r.adjunctUrl, str)]
    if "--parse-only" not in sys.argv:
        with cf.ThreadPoolExecutor(2) as ex:
            for i, (aid, st) in enumerate(ex.map(fetch, todo), 1):
                if st.startswith("err"):
                    print(aid, st, flush=True)
                if i % 500 == 0:
                    print(i, flush=True)
    with open("pdf_meta.jsonl", "w", encoding="utf-8") as out:
        for r in ev.itertuples():
            path = os.path.join(TXT, f"{r.announcementId}.txt")
            if not os.path.exists(path):
                out.write(json.dumps({"announcementId": r.announcementId, "err": "no_text"}) + "\n")
                continue
            res = parse_text(open(path, encoding="utf-8").read(), pd.Timestamp(r.ann_date))
            out.write(json.dumps({"announcementId": r.announcementId, **res}, ensure_ascii=False) + "\n")
    print("DONE", flush=True)
