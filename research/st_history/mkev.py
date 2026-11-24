from classify import *
df = load_raw(); df = df[df.code.map(a_share)]
df["acts"] = df.title.map(parse_title)
ev = df[df.acts.map(lambda a: any(x[0] in ("add","remove") for x in a))].copy()
ev["acts"] = ev.acts.map(json.dumps)
ev.to_parquet("events.parquet"); print("events", len(ev))
