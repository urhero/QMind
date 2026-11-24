import json, time, os, calendar
from q import query
out = open("raw.jsonl", "a", encoding="utf-8")
done = set(open("done.txt").read().split()) if os.path.exists("done.txt") else set()
log = open("done.txt", "a")
for y in range(2002, 2027):
    for m in range(1, 13):
        if (y, m) > (2026, 11): break
        se = f"{y}-{m:02d}-01~{y}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}"
        for key in ["风险警示", "特别处理"]:
            tag = f"{key}|{se}"
            if tag in done: continue
            page, n = 1, 0
            while True:
                d = query(key, se, page)
                for a in d.get("announcements") or []:
                    a["_key"] = key; out.write(json.dumps(a, ensure_ascii=False) + "\n"); n += 1
                if not d.get("hasMore"): break
                page += 1; time.sleep(0.25)
            out.flush(); log.write(tag + "\n"); log.flush()
            print(tag, d.get("totalAnnouncement"), n, flush=True); time.sleep(0.25)
print("DONE", flush=True)
