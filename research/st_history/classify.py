"""CNINFO 공시 제목 -> ST/*ST 지정·해제 이벤트.

제목을 앞에서부터 읽으며 동사(撤销/实施/继续实施 ...) 와 대상(退市风险警示=*ST, 其他风险警示/其他特别处理=ST,
옛 용어 特别处理=ST) 을 짝짓는다. 대상은 가장 최근 동사를 따른다 ("撤销 特别处理 及 退市风险警示" -> 둘 다 해제,
"撤销 退市风险警示 并 实施 其他特别处理" -> *ST 해제 + ST 지정).
"""
import json
import re

import pandas as pd

# 이벤트가 아닌 공시: 가능성 예고, 신청, 진행 보고, 제3자 의견, 채권
EXCLUDE = re.compile(
    r"可能|存在.{0,6}(被|风险)|申请|进展|措施|核查|意见|受托管理|律师|证券(股份)?有限(责任)?公司关于|债券|公司债|"
    r"\d{2}[^\s]{0,6}债|消除|提示性公告.*第.次|第.次提示|问询|回复|自查|更正|补充|取消|延期|摘要|英文"
)
VERB = r"(?P<verb>撤销|撤消|撤除|解除|取消|继续实施|继续实行|叠加实施|被实施|被实行|将被实施|将被实行|实施|实行|进行)"
OBJ = (
    r"(?P<obj>退市风险的?警示(?:的)?(?:特别处理)?|警示存在终止上市风险的特别处理|终止上市风险警示|"
    r"其[他它]风险警示|其[他它]特别处理|特别处理|风险警示)"
)
TOKEN = re.compile(f"{VERB}|{OBJ}")
REMOVE = {"撤销", "撤消", "撤除", "解除", "取消"}


def obj_grade(obj: str) -> str:
    if "退市" in obj or "终止上市" in obj:
        return "star"
    return "other"  # 其他风险警示 / 其他特别处理 / 옛 特别处理 / 风险警示


def parse_title(title: str):
    """-> list of (action, grade). action: add | remove | keep. 빈 리스트 = 이벤트 아님."""
    t = re.sub(r"</?em>|\s|“|”|\"", "", title)
    # 해제가 확정된 "신청" 표현은 이벤트다: "申请及撤销", "申请获(得)批准", "获准撤销"
    approved = re.search(r"申请(?:及|并)撤|申请(?:已)?获|获准|同意撤", t)
    if EXCLUDE.search(re.sub(r"申请", "", t) if approved else t):
        return []
    # "撤销因重整而被实施的退市风险警示": 수식절 안의 被实施 는 동사가 아니다
    t = re.sub(r"因.{0,15}?而?被(?:实施|实行)的", "", t)
    # "撤销对公司股票交易实施的退市风险警示" / "撤销股票交易实行其他风险警示":
    # 접속사(并/及/暨/、/，/和/同时) 없이 撤销 뒤에 오는 实施·实行 은 대상 수식어다
    t = re.sub(r"(撤销|撤消)([^并及暨，、和同]{0,10}?)(?:被)?(?:实施|实行)的?", r"\1\2", t)
    # 대상이 동사 앞: "其他特别处理撤销" -> "撤销其他特别处理"
    t = re.sub(r"(退市风险警示|其[他它]特别处理|其[他它]风险警示|特别处理)的?(撤销|撤消)", r"\2\1", t)
    out, verb = [], None
    for m in TOKEN.finditer(t):
        if m.group("verb"):
            verb = m.group("verb")
            continue
        if verb is None:
            continue
        obj = m.group("obj")
        act = "remove" if verb in REMOVE else ("keep" if verb.startswith("继续") else "add")
        # 옛 표현 "撤销特别处理" (退市/其他 없이) = 모든 경고 해제
        if act == "remove" and obj == "特别处理":
            out += [("remove", "star"), ("remove", "other")]
        else:
            out.append((act, obj_grade(obj)))
    return list(dict.fromkeys(out))


def load_raw(path="raw.jsonl") -> pd.DataFrame:
    rows = [json.loads(line) for line in open(path, encoding="utf-8")]
    df = pd.DataFrame(rows).drop_duplicates("announcementId")
    df["title"] = df["announcementTitle"].str.replace(r"</?em>", "", regex=True)
    df["sec_name"] = df["secName"].fillna("").str.replace(r"\s", "", regex=True)
    df["ann_date"] = pd.to_datetime(df["announcementTime"], unit="ms", utc=True).dt.tz_convert("Asia/Shanghai").dt.tz_localize(None).dt.normalize()
    df["code"] = df["secCode"].fillna("").astype(str).str.zfill(6)
    return df[["announcementId", "code", "sec_name", "ann_date", "title", "adjunctUrl", "orgId"]]


def a_share(code: str) -> bool:
    return isinstance(code, str) and bool(re.match(r"^(60|68|00|30)\d{4}$", code))
