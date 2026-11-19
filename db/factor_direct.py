from __future__ import annotations

"""ClariFI 없이 S&P 원천(xf_target) 에서 clarifi_*_afl 과 같은 형식의 월말 행을 만든다.

ClariFI 가 하던 일 = 바스켓 ISIN -> 종목 라인 -> 필드별 값 조회 -> 대상 테이블 INSERT.
값 자체는 전부 xf_target 에 있으므로 집합 쿼리로 대체한다 (한 달 수 초, ClariFI 는 전 기간 88분).
2026-10-30 실측: clarifi_718708_afl 202개월 비NULL 19,212,223건 완전 일치, M_RETURN 79,296건 오차 1e-8 미만.

흐름: 월말 이하 마지막 바스켓일 -> ISIN 을 sec_idhist PIT 로 매핑 -> 라인 선택 -> 월간 팩터값 + M_RETURN
-> 속성(sec, country, isin, ticker) -> 종목 x 필드 전체 행 (값 없으면 NULL 행, ClariFI 와 같은 형태).
기본은 대상 테이블과 대조만 하고, write=True 일 때만 append (fetch_snp 가 updated_at 최신을 택한다).
규칙 근거와 ClariFI 와 달라지는 점은 research.md 참조.
"""

import logging
import re

import numpy as np
import pandas as pd
import sqlalchemy as sql

import config

logger = logging.getLogger(__name__)

COLUMNS = ["gvkeyiid", "ticker", "isin", "ddt", "val", "fld", "sec", "country"]
RETURN_FIELD = "M_RETURN"
WEIGHT_SUFFIX = "_WGT"

# 지역 유니버스. DM/EM(2115, 2116) 은 universe-relative 팩터 전용이라 읽지 않는다 (파이프라인 미사용).
SOURCE_TABLES = (("ciqAFValueMonthlyIntl", "2111,2112,2114"), ("ciqAfValueMonthlyNA", "2109,2110"))
# ciqGvkeyIID 는 시점별 매핑 (거래 라인이 시기마다 다른 종목 라인에 붙는다) -> 기준일에 유효한 매핑만 조인
MAPPED_ON = "(g.symbolStartDate IS NULL OR g.symbolStartDate <= :d) AND (g.symbolEndDate IS NULL OR g.symbolEndDate >= :d)"
OTC_EXCHANGE = 19  # sec_dprc 거래소 코드: 미국 장외. 같은 ISIN 의 해외 라인이 여기 잡히는 경우가 많다
PRIMARY_RANK = {"PRIHISTUSA": 1, "PRIHISTCAN": 2, "PRIHISTROW": 3}  # clarifi_sm_primary_global option 1
NO_PRIMARY_RANK = 4
ISIN_LINE_SUFFIX = {"US": "", "CA": "C"}  # 나머지 나라는 W 라인
FX_TOLERANCE = pd.Timedelta(days=7)
MAX_BASKET_LAG_DAYS = 7
PRICE_FRESH_DAYS = 7
MIN_VALUE_COVERAGE = 0.9  # 값이 하나라도 있는 종목 비율 (미달 = 원천에 그 달이 아직 없음)
MIN_PRICE_COVERAGE = 0.9  # 최근 가격이 있는 종목 비율 (미달 = sec_dprc 미갱신, M_RETURN 이 조용히 틀어짐)
EQUAL_TOLERANCE = 1e-8


# ─────────────────────────────────────────────────────────────────────────────
# 순수 함수
# ─────────────────────────────────────────────────────────────────────────────
def validate_month_end(date: str | pd.Timestamp) -> pd.Timestamp:
    """달력 월말만 허용한다 (원천 월간 테이블의 asOfDate 도 달력 월말)."""
    ts = pd.Timestamp(date)
    if ts != ts + pd.offsets.MonthEnd(0):
        raise ValueError(f"{ts.date()} is not a calendar month end")
    return ts


def latest_basket(basket: pd.DataFrame, ddt: pd.Timestamp) -> pd.DataFrame:
    """월말 이하 마지막 날의 바스켓. 너무 오래됐으면 옛 구성으로 쌓이지 않게 오류."""
    b = basket.assign(ddt=pd.to_datetime(basket["ddt"]))
    b = b[b["ddt"] <= ddt]
    if b.empty:
        raise ValueError(f"no basket rows on or before {ddt.date()}")
    last = b["ddt"].max()
    if (ddt - last).days > MAX_BASKET_LAG_DAYS:
        raise ValueError(f"basket is stale: last date {last.date()} for {ddt.date()}")
    return b[b["ddt"] == last].reset_index(drop=True)


def _active_on(df: pd.DataFrame, on_date: pd.Timestamp) -> pd.Series:
    """efffrom/effthru 기간 판정. NaT = 열린 기간 (원천의 2900-01-01 은 로딩 때 NaT 로 바뀐다)."""
    return (df["efffrom"].isna() | (df["efffrom"] <= on_date)) & (df["effthru"].isna() | (df["effthru"] >= on_date))


def isin_check_digit(base: str) -> str:
    """ISIN 11자리(국가 2 + 본체 9) 의 검증 숫자 (Luhn, 문자 A=10 .. Z=35)."""
    digits = "".join(str(int(ch, 36)) for ch in base)
    total = sum(n // 10 + n % 10 for n in (int(d) * (2 - i % 2) for i, d in enumerate(reversed(digits))))
    return str(-total % 10)


def backfill_isin_history(isin_hist: pd.DataFrame, cusip_hist: pd.DataFrame, basket_isins=()) -> pd.DataFrame:
    """sec_idhist ISIN 이력이 시작되기 전 구간을 채운다 -> 추가 행 [gvkey, iid, isin, efffrom, effthru].

    미국·캐나다 상장 라인의 ISIN 이력은 2013-01-01 부터라 보강 없이는 2013년 이전 MXWO 비중 약 40% 가
    연결되지 않는다 (2005~2012 약 55~59% -> 보강 후 99% 대, research.md §2.4). 모두 라인의 첫 ISIN 시작 전날까지:
    1. 첫 ISIN 이 US/CA: 그 전 CUSIP 구간마다 국가코드 + CUSIP + 검증 숫자 (CUSIP 이 바뀐 과거도 따라간다)
    2. 그 밖 (미국 상장 외국 국적 IE·CH·PA 등, ISIN 이 CUSIP 과 무관): 첫 ISIN 시점의 CUSIP 이 이어진 구간은 첫 ISIN
    3. 바스켓 ISIN 본체(3~11자리) 가 그 전 CUSIP 과 같고 검증 숫자가 맞으면 그 ISIN
       (국적 이전 전 BMG·KYG, 아일랜드·영국으로 옮기기 전 Eaton·Aon 의 US ISIN 등)
    4. 위로 아무것도 안 붙은 라인은 첫 ISIN 을 열린 시작으로, 단 그 ISIN 을 다른 라인이 쓴 적이 없을 때만
       (해외 라인 이력이 늦게 시작하는 경우; 분할로 ISIN 이 옮겨 간 라인은 제외된다)
    그래도 남는 바스켓 ISIN 은 build_month 에서 MSCI 체험분 매핑으로 (`msci_fallback_lines`).
    """
    first = isin_hist.sort_values("efffrom", na_position="first").drop_duplicates(["gvkey", "iid"])
    first = first[["gvkey", "iid", "isin", "efffrom"]].rename(columns={"isin": "first_isin", "efffrom": "first_from"})
    c = cusip_hist.merge(first, on=["gvkey", "iid"], how="left")
    c = c[c["first_from"].isna() | c["efffrom"].isna() | (c["efffrom"] < c["first_from"])].assign(body=lambda x: x["cusip"].str[:9].str.upper())
    known = c["first_isin"].notna()
    us_ca = known & c["first_isin"].str[:2].isin(["US", "CA"])
    base = c["first_isin"].str[:2] + c["body"]
    derived = c[us_ca & base.str.fullmatch(r"[A-Z0-9]{11}", na=False)]
    derived = derived.assign(isin=base[derived.index] + base[derived.index].map(isin_check_digit))
    spans = c[known & ~us_ca & (c["effthru"].isna() | (c["effthru"] >= c["first_from"]))].assign(isin=lambda x: x["first_isin"])

    b = pd.Series(pd.unique(pd.Series(basket_isins, dtype=object).dropna()), dtype=object)
    b = b[b.str.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", na=False)]
    b = b[b.str[:11].map(isin_check_digit) == b.str[11]]
    cins = c[~c.index.isin(spans.index)].merge(pd.DataFrame({"isin": b, "body": b.str[2:11]}), on="body")  # US 라인도: 캐나다 이전 전 Tim Hortons

    filled = pd.concat([derived, spans, cins])
    lines_on = isin_hist[["isin", "gvkey", "iid"]].drop_duplicates()["isin"].value_counts()
    opened = first[first["first_from"].notna() & first["first_isin"].map(lines_on).eq(1)]
    opened = opened.merge(filled[["gvkey", "iid"]].drop_duplicates(), on=["gvkey", "iid"], how="left", indicator=True)
    opened = opened[opened["_merge"] == "left_only"].assign(isin=lambda x: x["first_isin"], efffrom=pd.NaT, effthru=pd.NaT)

    c = pd.concat([filled, opened])
    day_before = c["first_from"] - pd.Timedelta(days=1)
    return pd.DataFrame(
        {
            "gvkey": c["gvkey"],
            "iid": c["iid"],
            "isin": c["isin"],
            "efffrom": c["efffrom"],
            "effthru": c["effthru"].where(day_before.isna() | (c["effthru"] < day_before), day_before),
        }
    ).drop_duplicates().reset_index(drop=True)


def msci_fallback_lines(isins: pd.Series, on_date: pd.Timestamp, msci: pd.DataFrame, isin_hist: pd.DataFrame | None = None) -> pd.DataFrame:
    """sec_idhist 로 못 찾은 바스켓 ISIN 을 MSCI 체험분 매핑으로 -> [isin, gvkeyiid].

    msci = [isin, gvkey, iid, efffrom, effthru]: MSCIConstituentIdentifier(ISIN, 2005~2025-07) -> tradingItemId -> ciqGvkeyIID.
    원천 ISIN 이력이 늦게 시작하거나 (Vivendi 2008, Nordea 2007) 옛 ISIN 이 아예 없는 경우 (국적 이전 전) 를 메운다.
    마지막 수단: MSCI 는 중국 A 주를 Stock Connect 라인으로 잡는 등 라인 규칙이 달라 다른 규칙보다 앞세우지 않는다.
    isin_hist 가 그 ISIN 을 다른 회사 것으로 알면 (어느 시점이든) MSCI 를 쓰지 않는다 (회사 간 충돌 방지).
    """
    m = msci.loc[msci["isin"].isin(pd.unique(isins)) & _active_on(msci, on_date)]
    if isin_hist is not None:
        owners = isin_hist[["isin", "gvkey"]].drop_duplicates()
        clash = m[["isin", "gvkey"]].merge(owners, on="isin", suffixes=("", "_other")).query("gvkey != gvkey_other")["isin"]
        m = m[~m["isin"].isin(clash)]
    m = m.assign(gvkeyiid=m["gvkey"] + m["iid"]).sort_values(["isin", "gvkeyiid"])
    return m.drop_duplicates("isin")[["isin", "gvkeyiid"]].reset_index(drop=True)


def select_lines(
    isins: pd.Series,
    on_date: pd.Timestamp,
    isin_hist: pd.DataFrame,
    primary: pd.DataFrame,
    company: pd.DataFrame,
    security: pd.DataFrame,
    seen: pd.Series,
) -> pd.DataFrame:
    """바스켓 ISIN 마다 종목 라인 하나를 고른다 -> [isin, gvkeyiid]. 후보가 없는 ISIN 은 빠진다.

    같은 ISIN 에 라인이 여럿이면 (선진국 ISIN 의 약 절반):
    1. 대상 테이블에 쌓여 있던 활성 라인, 그중 최근 것 - 팩터 이력이 라인에 묶여 있어 연속성이 먼저다
    2. clarifi_sm_primary_global 규칙 - 회사 소재지 지역 primary > USA > CAN > ROW
    3. 미국 장외(OTC)·비활성 라인 후순위, ISIN 나라와 맞는 접미사 우선
    """
    columns = ["isin", "gvkeyiid"]
    m = pd.DataFrame({"isin": pd.unique(isins)}).merge(isin_hist.loc[_active_on(isin_hist, on_date), ["isin", "gvkey", "iid"]], on="isin")
    if m.empty:
        return pd.DataFrame(columns=columns)
    m["gvkeyiid"] = m["gvkey"] + m["iid"]
    m = m.merge(company[["gvkey", "loc"]], on="gvkey", how="left").merge(security[["gvkey", "iid", "exchg", "secstat"]], on=["gvkey", "iid"], how="left")
    m = m.merge(primary.loc[_active_on(primary, on_date), ["gvkey", "iid", "item"]], on=["gvkey", "iid"], how="left")

    domicile_item = m["loc"].map({"USA": "PRIHISTUSA", "CAN": "PRIHISTCAN"}).fillna("PRIHISTROW")
    m["rank"] = m["item"].map(PRIMARY_RANK).fillna(NO_PRIMARY_RANK)
    m.loc[m["item"] == domicile_item, "rank"] = 0
    m["otc"] = m["exchg"] == OTC_EXCHANGE
    m["inactive"] = m["secstat"] != "A"
    want_suffix = m["isin"].str[:2].map(ISIN_LINE_SUFFIX).fillna("W")
    m["suffix_off"] = m["iid"].str.replace(r"\d", "", regex=True) != want_suffix
    m["last_seen"] = pd.to_datetime(m["gvkeyiid"].map(seen)).where(~m["inactive"])

    m = m.sort_values(
        ["isin", "last_seen", "rank", "otc", "inactive", "suffix_off", "gvkeyiid"],
        ascending=[True, False, True, True, True, True, True],
        na_position="last",
        kind="stable",
    )
    return m.drop_duplicates("isin")[columns].reset_index(drop=True)


def _usd_prices(px: pd.DataFrame, fx: pd.DataFrame) -> pd.Series:
    """prccd/ajexdi 를 그 가격일 환율로 달러 환산. exrt_dly 는 기준통화(GBP) 1 단위당 환율."""
    usd = fx[fx["tocurd"] == "USD"].set_index("datadate")["exratd"]
    rates = fx.assign(usd_per_local=fx["datadate"].map(usd) / fx["exratd"]).dropna(subset=["usd_per_local"]).sort_values("datadate")
    m = pd.merge_asof(
        px.sort_values("datadate"),
        rates[["tocurd", "datadate", "usd_per_local"]],
        on="datadate",
        left_by="curcdd",
        right_by="tocurd",
        tolerance=FX_TOLERANCE,
    )
    return (m["prccd"] / m["ajexdi"] * m["usd_per_local"]).set_axis(m["gvkeyiid"])


def month_returns(px_end: pd.DataFrame, px_prev: pd.DataFrame, fx: pd.DataFrame) -> pd.Series:
    """후행 1개월 달러 가격수익률 (배당 미포함) -> gvkeyiid 별.

    거래정지로 가격이 오래됐어도 범위 제한 없이 마지막 가격을 쓰고 환율도 그 가격일 기준이어야
    ClariFI 값과 일치한다 (718708 전 기간 실측: 월말 환율을 쓰면 최대 4% 틀림).
    """
    return _usd_prices(px_end, fx) / _usd_prices(px_prev, fx) - 1


def pick_ticker(symbols: pd.DataFrame, current_tic: pd.Series) -> pd.Series:
    """ciqSymbol 활성 심볼(최신 시작일) > sec_idcurrent TIC > 마지막 심볼 -> gvkeyiid 별."""
    ordered = symbols.sort_values("symbolStartDate", na_position="first", kind="stable")
    last = ordered.drop_duplicates("gvkeyiid", keep="last").set_index("gvkeyiid")["symbolValue"]
    active = ordered[ordered["activeFlag"] == 1].drop_duplicates("gvkeyiid", keep="last").set_index("gvkeyiid")["symbolValue"]
    return active.combine_first(current_tic).combine_first(last)


def _is_special_field(fld: str) -> bool:
    return fld == RETURN_FIELD or fld.endswith(WEIGHT_SUFFIX)


def map_fields(flds: list[str], factors: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """대상 테이블 fld (오래된 이름 -> 새 이름 순) -> ciqAfFactor.factorId. 팩터당 필드 하나.

    'Name(ABBR)' 약어 우선, 'Name (1)' 류는 이름으로 찾는다. 같은 팩터의 이름이 여럿이면 (개명) 진짜 약어를 가진
    최신 이름 하나만 쓴다 - 둘 다 쓰면 download 뒤 같은 종목-약어 행이 중복된다.
    M_RETURN 과 <지수>_WGT 는 원천 팩터가 아니라 따로 계산한다. 못 찾은 필드는 두 번째 값으로 알린다.
    """
    by_abbreviation = dict(zip(factors["factorAbbreviation"], factors["factorId"]))
    by_name = dict(zip(factors["factorName"], factors["factorId"]))
    chosen, unmapped = {}, []
    for fld in flds:
        if _is_special_field(fld):
            continue
        tail = re.search(r"\(([^()]*)\)\s*$", fld)
        factor_id = by_abbreviation.get(tail.group(1)) if tail else None
        has_abbreviation = factor_id is not None
        if not has_abbreviation:
            factor_id = by_name.get(fld[: tail.start()].strip() if tail else fld.strip())
        if factor_id is None:
            unmapped.append(fld)
        elif factor_id not in chosen or has_abbreviation or not chosen[factor_id][1]:
            chosen[factor_id] = (fld, has_abbreviation)
    # 대소문자만 다른 철자는 ClariFI 가 같은 값으로 둘 다 쓴다 (IO_ChgofIO_HF). download 는 대소문자 무시로 묶어 중복 안 남
    pairs = [(fld, fid) for fid, (kept, _) in chosen.items() for fld in dict.fromkeys(flds) if fld.casefold() == kept.casefold()]
    return pd.DataFrame(pairs, columns=["fld", "factorId"]), unmapped


def assemble_rows(
    lines: pd.DataFrame,
    flds: list[str],
    field_map: pd.DataFrame,
    values: pd.DataFrame,
    returns: pd.Series,
    attrs: pd.DataFrame,
    ddt: pd.Timestamp,
) -> pd.DataFrame:
    """종목 x 필드 전체 행 (값 없으면 NULL 행). lines = [gvkeyiid, isin(바스켓), wgt]."""
    grid = pd.MultiIndex.from_product([lines["gvkeyiid"], flds], names=["gvkeyiid", "fld"]).to_frame(index=False)
    found = values.merge(field_map, on="factorId")[["gvkeyiid", "fld", "factorValue"]].drop_duplicates(["gvkeyiid", "fld"])
    out = grid.merge(found, on=["gvkeyiid", "fld"], how="left").rename(columns={"factorValue": "val"})
    out["val"] = out["val"].astype(float)

    is_return = out["fld"] == RETURN_FIELD
    out.loc[is_return, "val"] = out.loc[is_return, "gvkeyiid"].map(returns).astype(float)
    is_weight = out["fld"].str.endswith(WEIGHT_SUFFIX)
    out.loc[is_weight, "val"] = out.loc[is_weight, "gvkeyiid"].map(lines.set_index("gvkeyiid")["wgt"]).astype(float)

    meta = lines[["gvkeyiid", "isin"]].rename(columns={"isin": "basket_isin"}).merge(attrs, on="gvkeyiid", how="left")
    meta["isin"] = meta["isin"].fillna(meta["basket_isin"])  # sec_idcurrent 에 ISIN 없는 라인이 있다
    meta["sec"] = meta["sec"].fillna("Undefined")  # ClariFI 표기. download 가 이 값으로 거른다
    out = out.merge(meta.drop(columns="basket_isin"), on="gvkeyiid", how="left")
    out["ddt"] = ddt
    return out[COLUMNS]


def check_coverage(lines: pd.DataFrame, values: pd.DataFrame, last_prices: pd.DataFrame, ddt: pd.Timestamp) -> None:
    """원천이 그 달까지 들어왔는지 확인. 미적재 상태로 돌면 전 종목 NULL 이나 옛 가격이 쌓인다."""
    n = len(lines)
    with_value = values.loc[values["factorValue"].notna(), "gvkeyiid"].nunique()
    if with_value < MIN_VALUE_COVERAGE * n:
        raise RuntimeError(f"source factor table not ready for {ddt.date()}: {with_value}/{n} securities have values")
    fresh = int((last_prices["datadate"] >= ddt - pd.Timedelta(days=PRICE_FRESH_DAYS)).sum())
    if fresh < MIN_PRICE_COVERAGE * n:
        raise RuntimeError(f"sec_dprc not updated for {ddt.date()}: {fresh}/{n} securities have recent prices")


def _same_text(a: pd.Series, b: pd.Series) -> pd.Series:
    """속성 비교: NULL 과 공백만 다른 값은 같게 본다 (ClariFI 는 빈 문자열, char 컬럼은 뒤 공백)."""
    return a.fillna("").astype(str).str.strip() == b.fillna("").astype(str).str.strip()


def compare_with_table(built: pd.DataFrame, table_rows: pd.DataFrame, lines: pd.DataFrame) -> dict:
    """직접 빌드 결과와 대상 테이블 대조. 테이블은 fetch_snp 처럼 (종목, 필드)별 updated_at 최신만 본다."""
    latest = table_rows.sort_values("updated_at", kind="stable").drop_duplicates(["gvkeyiid", "fld"], keep="last")
    weights = lines.set_index("gvkeyiid")["wgt"]
    built_names, table_names = set(built["gvkeyiid"]), set(latest["gvkeyiid"])
    direct_only = sorted(((g, float(weights.get(g, np.nan))) for g in built_names - table_names), key=lambda x: (-x[1], x[0]))
    total_weight = float(weights.sum())

    j = built.merge(latest, on=["gvkeyiid", "fld"], suffixes=("", "_t"))
    val, val_t = j["val"].astype(float), j["val_t"].astype(float)
    has, has_t = val.notna(), val_t.notna()
    both = has & has_t
    gap = (val - val_t).abs()
    differ = j.assign(gap=gap)[both & (gap >= EQUAL_TOLERANCE)]

    per_security = j.drop_duplicates("gvkeyiid")
    attr_mismatch, attr_examples = {}, {}
    for col in ("ticker", "isin", "sec", "country"):
        off = per_security[~_same_text(per_security[col], per_security[f"{col}_t"])]
        attr_mismatch[col] = len(off)
        attr_examples[col] = off[["gvkeyiid", col, f"{col}_t"]].values.tolist()[:8]

    return {
        "built_rows": len(built),
        "table_rows": len(latest),
        "both": int(both.sum()),
        "equal": int((both & (gap < EQUAL_TOLERANCE)).sum()),
        "differ": len(differ),
        "differ_examples": differ.sort_values("gap", ascending=False)[["gvkeyiid", "fld", "val", "val_t"]].values.tolist()[:10],
        "table_value_direct_null": int((has_t & ~has).sum()),
        "direct_value_table_null": int((has & ~has_t).sum()),
        "direct_only": direct_only,
        "direct_only_weight": sum(w for _, w in direct_only if not np.isnan(w)) / total_weight if total_weight else 0.0,
        "table_only": sorted(table_names - built_names),
        "fields_only_direct": sorted(set(built["fld"]) - set(latest["fld"])),
        "fields_only_table": sorted(set(latest["fld"]) - set(built["fld"])),
        "attr_mismatch": attr_mismatch,
        "attr_examples": attr_examples,
    }


def safe_table(name: str) -> str:
    """테이블명은 파라미터화할 수 없어 식별자 형식만 허용한다."""
    if not re.fullmatch(r"#{0,2}[A-Za-z0-9_]+", name):
        raise ValueError(f"Invalid table name '{name}'")
    return name


def insert_tuples(rows: pd.DataFrame) -> list[tuple]:
    """INSERT 용 튜플. NaN/None 은 SQL NULL, ddt 는 date (대상 컬럼형)."""
    out = rows[COLUMNS].astype(object).where(rows[COLUMNS].notna(), None)
    out["ddt"] = pd.to_datetime(rows["ddt"]).dt.date
    return list(out.itertuples(index=False, name=None))


# ─────────────────────────────────────────────────────────────────────────────
# DB
# ─────────────────────────────────────────────────────────────────────────────
def _engine(server: str, database: str, user: str, password: str):
    url = sql.engine.URL.create(
        "mssql+pyodbc",
        username=user,
        password=password,
        host=server,
        database=database,
        query={"driver": config.PARAM["odbc_name"]},
    )
    return sql.create_engine(url)


def _spg_engine(database: str):
    d = config.DIRECT_LOAD
    return _engine(d["server_name"], database, d["user_name"], d["user_pwd"])


def _basket_engine():
    d = config.DIRECT_LOAD
    return _engine(d["basket_server"], d["basket_db"], config.PARAM["user_name"], config.PARAM["user_pwd"])


def _dates(df: pd.DataFrame, *columns: str) -> pd.DataFrame:
    """원천의 2900-01-01 등 datetime64 범위 밖 값은 NaT(열린 기간) 로."""
    for col in columns:
        df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def _read_basket(code: str, ddt: pd.Timestamp) -> pd.DataFrame:
    start = (ddt - pd.Timedelta(days=MAX_BASKET_LAG_DAYS + 3)).strftime("%Y-%m-%d")
    engine = _basket_engine()
    with engine.connect() as conn:
        basket = pd.read_sql_query(
            sql.text("EXEC dbo.spg_msci_wgt_Daily :start, :end, :code"),
            conn,
            params={"start": start, "end": ddt.strftime("%Y-%m-%d"), "code": code},
        )
    engine.dispose()
    return latest_basket(basket, ddt)


def _read_table_history(table: str, ddt: pd.Timestamp) -> tuple[pd.Series, list[str]]:
    """대상 테이블의 라인별 마지막 등장 월 (그 달 제외) + 필드 목록 (직전 월, 그 달 행이 이미 있으면 그 달까지).

    대상이 heap 이라 스캔한다 (월 1회라 수용). 새 팩터는 ClariFI 나 수동으로 필드가 생겨야 따라온다.
    """
    engine = _spg_engine(config.DIRECT_LOAD["target_db"])
    params = {"d": ddt.strftime("%Y-%m-%d")}
    with engine.connect() as conn:
        seen = pd.read_sql_query(sql.text(f"SELECT gvkeyiid, MAX(ddt) last_ddt FROM dbo.{table} WITH (NOLOCK) WHERE ddt < :d GROUP BY gvkeyiid"), conn, params=params)
        if seen.empty:
            engine.dispose()
            raise ValueError(f"{table} has no rows before {ddt.date()}: field list unknown")
        prev = pd.to_datetime(seen["last_ddt"]).max().strftime("%Y-%m-%d")
        flds = pd.read_sql_query(
            # 이진 정렬: 기본 정렬은 대소문자만 다른 두 철자를 한 필드로 묶어 어느 쪽인지 실행마다 달라진다
            sql.text(
                f"SELECT fld COLLATE Latin1_General_BIN2 fld, MAX(updated_at) last_at FROM dbo.{table} WITH (NOLOCK) "
                "WHERE ddt >= :prev AND ddt <= :d GROUP BY fld COLLATE Latin1_General_BIN2"
            ),
            conn,
            params={"prev": prev, **params},
        )
    engine.dispose()
    return pd.to_datetime(seen.set_index("gvkeyiid")["last_ddt"]), flds.sort_values(["last_at", "fld"])["fld"].tolist()


def _read_table_rows(table: str, ddt: pd.Timestamp) -> pd.DataFrame:
    engine = _spg_engine(config.DIRECT_LOAD["target_db"])
    with engine.connect() as conn:
        rows = pd.read_sql_query(
            sql.text(f"SELECT gvkeyiid, ticker, isin, ddt, val, fld, sec, country, updated_at FROM dbo.{table} WITH (NOLOCK) WHERE ddt = :d"),
            conn,
            params={"d": ddt.strftime("%Y-%m-%d")},
        )
    engine.dispose()
    return rows


def _last_prices(conn, on_date: pd.Timestamp) -> pd.DataFrame:
    """종목별 on_date 이하 마지막 가격 (범위 제한 없음: month_returns 참조)."""
    return _dates(
        pd.read_sql_query(
            sql.text(
                "SELECT s.gvkey + s.iid gvkeyiid, p.datadate, p.prccd, p.ajexdi, p.curcdd FROM #lines s "
                "CROSS APPLY (SELECT TOP 1 d.datadate, d.prccd, d.ajexdi, d.curcdd FROM sec_dprc d "
                "WHERE d.gvkey = s.gvkey AND d.iid = s.iid AND d.datadate <= :d AND d.prccd IS NOT NULL "
                "ORDER BY d.datadate DESC) p"
            ),
            conn,
            params={"d": on_date.strftime("%Y-%m-%d")},
        ),
        "datadate",
    )


def build_month(date: str, table: str, basket_code: str) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """한 달치 행을 만든다 (DB 쓰기 없음, 세션 임시 테이블만) -> (rows, lines, info)."""
    ddt = validate_month_end(date)
    table = safe_table(table)
    seen, flds = _read_table_history(table, ddt)
    basket = _read_basket(basket_code, ddt)
    d = ddt.strftime("%Y-%m-%d")

    engine = _spg_engine(config.DIRECT_LOAD["source_db"])
    with engine.begin() as conn:
        isin_hist = _dates(pd.read_sql_query(sql.text("SELECT gvkey, iid, itemvalue isin, efffrom, effthru FROM sec_idhist WHERE item = 'ISIN'"), conn), "efffrom", "effthru")
        cusip_hist = _dates(pd.read_sql_query(sql.text("SELECT gvkey, iid, itemvalue cusip, efffrom, effthru FROM sec_idhist WHERE item = 'CUSIP'"), conn), "efffrom", "effthru")
        isin_hist = pd.concat([isin_hist, backfill_isin_history(isin_hist, cusip_hist, basket["isin"])], ignore_index=True)
        primary = _dates(
            pd.read_sql_query(sql.text("SELECT gvkey, itemvalue iid, item, efffrom, effthru FROM sec_idhist WHERE item IN ('PRIHISTROW', 'PRIHISTUSA', 'PRIHISTCAN') AND itemvalue IS NOT NULL"), conn),
            "efffrom",
            "effthru",
        )
        company = pd.read_sql_query(sql.text("SELECT gvkey, loc FROM company"), conn)
        security = pd.read_sql_query(sql.text("SELECT gvkey, iid, exchg, secstat FROM security"), conn)

        selected = select_lines(basket["isin"], ddt, isin_hist, primary, company, security, seen)
        missing = basket.loc[~basket["isin"].isin(selected["isin"]), "isin"]
        if not missing.empty:
            msci = _dates(
                pd.read_sql_query(
                    sql.text(
                        "SELECT i.identifierValue isin, g.gvkey, g.iid, i.fromDate efffrom, i.toDate effthru FROM MSCIConstituentIdentifier i "
                        "JOIN MSCIConstituent m ON m.constituentID = i.constituentID JOIN ciqGvkeyIID g ON g.objectId = m.tradingItemId "
                        # ponytail: 매핑 기간 조건 없음 - MSCI 는 새 ISIN 을 매핑 시작 며칠 전에 올려 조건을 걸면
                        # 지주사 전환 달 맞는 라인 6건을 잃는다 (전 기간 점검, 기간 밖 오매핑 0건, 2026-11-19)
                        "WHERE i.identifierTypeID = 3"
                    ),
                    conn,
                ),
                "efffrom",
                "effthru",
            )
            selected = pd.concat([selected, msci_fallback_lines(missing, ddt, msci, isin_hist)], ignore_index=True)
        lines = (
            basket.merge(selected, on="isin")
            .groupby("gvkeyiid", as_index=False)
            .agg(isin=("isin", "first"), wgt=("wgt", "sum"))
        )
        unmapped_isins = basket.loc[~basket["isin"].isin(selected["isin"]), ["isin", "wgt"]].values.tolist()

        conn.exec_driver_sql("DROP TABLE IF EXISTS #lines")
        conn.exec_driver_sql("CREATE TABLE #lines (gvkey varchar(6) COLLATE DATABASE_DEFAULT, iid varchar(3) COLLATE DATABASE_DEFAULT, PRIMARY KEY (gvkey, iid))")
        conn.exec_driver_sql("INSERT INTO #lines VALUES (?, ?)", [(g[:6], g[6:]) for g in lines["gvkeyiid"]])

        factors = pd.read_sql_query(sql.text("SELECT factorId, factorName, factorAbbreviation FROM ciqAfFactor"), conn)
        field_map, unmapped_flds = map_fields(flds, factors)
        ids = ",".join(str(int(i)) for i in field_map["factorId"].unique())
        values = pd.concat(
            [
                pd.read_sql_query(
                    sql.text(
                        f"SELECT m.gvkey + m.iid gvkeyiid, m.factorId, m.factorValue FROM {source} m "
                        f"JOIN #lines s ON s.gvkey = m.gvkey AND s.iid = m.iid "
                        f"WHERE m.universeId IN ({universes}) AND m.asOfDate = :d AND m.factorId IN ({ids})"
                    ),
                    conn,
                    params={"d": d},
                ).astype({"factorValue": float})
                for source, universes in SOURCE_TABLES
            ],
            ignore_index=True,
        )

        px_end = _last_prices(conn, ddt)
        check_coverage(lines, values, px_end, ddt)
        px_prev = _last_prices(conn, ddt - pd.offsets.MonthEnd(1))
        fx_from = (min(px_end["datadate"].min(), px_prev["datadate"].min()) - FX_TOLERANCE).strftime("%Y-%m-%d")
        fx = _dates(pd.read_sql_query(sql.text("SELECT tocurd, datadate, exratd FROM exrt_dly WHERE datadate >= :a AND datadate <= :d"), conn, params={"a": fx_from, "d": d}), "datadate")
        returns = month_returns(px_end, px_prev, fx)

        sectors = pd.read_sql_query(
            sql.text(
                "SELECT h.gvkey, r.gicdesc sec FROM co_hgic h JOIN r_giccd r ON r.giccd = h.gsector "
                "WHERE h.indtype = 'GICS' AND h.indfrom <= :d AND (h.indthru IS NULL OR h.indthru >= :d) "
                "AND h.gvkey IN (SELECT gvkey FROM #lines)"
            ),
            conn,
            params={"d": d},
        ).drop_duplicates("gvkey")
        current = pd.read_sql_query(
            sql.text("SELECT c.gvkey + c.iid gvkeyiid, c.item, c.itemvalue FROM sec_idcurrent c JOIN #lines s ON s.gvkey = c.gvkey AND s.iid = c.iid WHERE c.item IN ('ISIN', 'TIC')"),
            conn,
        )
        symbols = _dates(
            pd.read_sql_query(
                sql.text(
                    "SELECT g.gvkey + g.iid gvkeyiid, y.symbolValue, y.symbolStartDate, y.activeFlag FROM ciqSymbol y "
                    f"JOIN ciqGvkeyIID g ON g.objectId = y.objectId JOIN #lines s ON s.gvkey = g.gvkey AND s.iid = g.iid WHERE {MAPPED_ON}"
                ),
                conn,
                params={"d": d},
            ),
            "symbolStartDate",
        )
    engine.dispose()

    item = lambda name: current[current["item"] == name].drop_duplicates("gvkeyiid").set_index("gvkeyiid")["itemvalue"]  # noqa: E731
    attrs = lines[["gvkeyiid"]].assign(gvkey=lambda x: x["gvkeyiid"].str[:6])
    attrs["ticker"] = attrs["gvkeyiid"].map(pick_ticker(symbols, item("TIC")))
    attrs["isin"] = attrs["gvkeyiid"].map(item("ISIN"))
    attrs = attrs.merge(sectors, on="gvkey", how="left").merge(company.rename(columns={"loc": "country"}), on="gvkey", how="left")

    all_fields = field_map["fld"].tolist() + [f for f in flds if _is_special_field(f)]
    rows = assemble_rows(lines, all_fields, field_map, values, returns, attrs.drop(columns="gvkey"), ddt)
    info = {
        "basket_date": str(basket["ddt"].iloc[0].date()),
        "basket_names": basket["isin"].nunique(),
        "lines": len(lines),
        "unmapped_isins": unmapped_isins,
        "fields": len(all_fields),
        "unmapped_fields": unmapped_flds,
        "with_value": int(values.loc[values["factorValue"].notna(), "gvkeyiid"].nunique()),
    }
    return rows, lines, info


def insert_rows(conn, table: str, rows: pd.DataFrame) -> None:
    """같은 트랜잭션에서 INSERT. updated_at 은 컬럼 기본값(getdate)."""
    table = safe_table(table)
    cursor = conn.connection.cursor()
    cursor.fast_executemany = True
    cursor.executemany(f"INSERT INTO {table} ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})", insert_tuples(rows))
    cursor.close()


def format_report(info: dict, result: dict) -> list[str]:
    """대조 리포트 (cp949 콘솔 호환 ASCII)."""
    lines = [
        f"basket {info['basket_date']}: {info['basket_names']} names -> {info['lines']} lines, {info['with_value']} with values, {info['fields']} fields, {result['built_rows']} rows",
        f"unmapped ISINs: {len(info['unmapped_isins'])} {info['unmapped_isins'][:10]}",
        f"unmapped fields: {info['unmapped_fields']}",
        f"table rows (latest export): {result['table_rows']}",
    ]
    if result["table_rows"] == 0:
        return lines + ["nothing to compare: target table has no rows for this month"]
    lines += [
        f"values: both={result['both']} equal={result['equal']} differ={result['differ']} table_only_value={result['table_value_direct_null']} direct_only_value={result['direct_value_table_null']}",
        f"differ examples [gvkeyiid, fld, direct, table]: {result['differ_examples']}",
        f"names only in direct: {len(result['direct_only'])} (basket weight {result['direct_only_weight'] * 100:.3f}%)",
    ]
    lines += [f"  {g} {w * 100:.3f}%" for g, w in result["direct_only"]]
    lines += [
        f"names only in table: {len(result['table_only'])} {result['table_only']}",
        f"fields only in direct: {result['fields_only_direct']}",
        f"fields only in table: {result['fields_only_table']}",
        f"attribute mismatches (securities): {result['attr_mismatch']}",
    ]
    lines += [f"  {col} [gvkeyiid, direct, table]: {ex}" for col, ex in result["attr_examples"].items() if ex]
    return lines


def run_direct_load(date: str, table: str, basket_code: str, write: bool = False) -> dict:
    """빌드 -> 대상 테이블과 대조 -> (write=True 면) append. 기본은 쓰기 없음."""
    rows, lines, info = build_month(date, table, basket_code)
    if rows.duplicated(["gvkeyiid", "fld"]).any():
        raise RuntimeError("duplicate (gvkeyiid, fld) in built rows")  # 라인 선택이 깨졌을 때 운영 테이블에 쌓이지 않게
    result = compare_with_table(rows, _read_table_rows(table, pd.Timestamp(date)), lines)
    for line in format_report(info, result):
        logger.info(line)
    if write:
        engine = _spg_engine(config.DIRECT_LOAD["target_db"])
        with engine.begin() as conn:
            insert_rows(conn, table, rows)
        engine.dispose()
        logger.info("wrote %d rows to %s", len(rows), table)
    else:
        logger.info("dry run: nothing written (use --write to append)")
    return {"info": info, "result": result, "rows": rows, "lines": lines}
