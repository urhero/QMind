# -*- coding: utf-8 -*-
"""MP 북의 MTD 손익 (전월말 -> 어제, Bloomberg 총수익지수 기준).

사용:
    python research/mtd_pnl.py                 # 어제 기준
    python research/mtd_pnl.py 2026-09-15      # 임의 기준일

입력 : output/{BM}/<최신 기준일>/weights_mp_*.csv (ls_weight = 북 비중)
매핑 : data/{BM}/figi_map.csv (isin -> 본상장 composite FIGI). 없는 ISIN 은 OpenFIGI 로 채움
       (.env OPENFIGI_API_KEY 권장). Bloomberg 에 ISIN 을 직접 주면 미국 OTC 라인(KDDIF 등)으로
       풀리므로 반드시 /bbgid/<FIGI> 로 조회한다.
가격 : Bloomberg Desktop API (터미널 로그인 필요). TOT_RETURN_INDEX_GROSS_DVDS = 배당 재투자 포함
       -> 로컬 통화 1회 + currency=USD 오버라이드 1회. 가격 소스는 get_tri() 하나로 격리.
출력 : output/{BM}/<MP 기준일>/mtd_pnl_<asof>.csv + 콘솔 요약 (local / USD / FX 효과)
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import PARAM  # noqa: E402
from service.paths import DATE_GLOB, OUTPUT_DIR, UNIVERSE_DATA_DIR  # noqa: E402

MAP_PATH = UNIVERSE_DATA_DIR / "figi_map.csv"
BM_SECURITY = f"{PARAM['benchmark']} Index"  # MXWO / MXCN1A 가격지수 (USD). 총수익 비교는 NDDUWI 등 별도
TRI_FIELD = "TOT_RETURN_INDEX_GROSS_DVDS"

# ── OpenFIGI: 도메사일(ISO3) -> 본상장 exchCode 후보 (앞이 우선). 없는 국가(BMU/CYM/LUX 등)는 EXCH_PRIORITY.
COUNTRY_EXCH = {
    "USA": ["US"], "JPN": ["JP", "JT"], "CAN": ["CN", "CT"], "GBR": ["LN"], "FRA": ["FP"], "DEU": ["GR", "GY"],
    "CHE": ["SW", "SE"], "AUS": ["AU", "AT"], "SWE": ["SS"], "NLD": ["NA"], "HKG": ["HK"], "ITA": ["IM"],
    "ESP": ["SM", "SQ"], "SGP": ["SP"], "IRL": ["ID", "US", "LN"], "ISR": ["IT", "US"], "DNK": ["DC"],
    "FIN": ["FH"], "NOR": ["NO"], "BEL": ["BB"], "NZL": ["NZ"], "AUT": ["AV"], "PRT": ["PL"], "MEX": ["MM"],
    "CHN": ["HK", "US"], "MAC": ["HK"],
}
EXCH_PRIORITY = ["US", "JP", "LN", "FP", "GR", "SW", "HK", "AU", "CN", "SS", "NA", "IM", "SM", "SP", "ID", "IT",
                 "DC", "FH", "NO", "BB", "NZ", "AV", "PL", "MM"]
US_REAL = {"UN", "UW", "UA"}  # NYSE/Nasdaq/Amex — 없으면 'US' 는 OTC (EADSF 류) 이므로 제외


def latest_mp_weights() -> tuple[pd.DataFrame, str]:
    for d in reversed(sorted(OUTPUT_DIR.glob(DATE_GLOB))):
        files = sorted(d.glob("weights_mp_*.csv"))
        if files:
            return pd.read_csv(files[-1]), d.name
    raise FileNotFoundError(f"weights_mp_*.csv not found under {OUTPUT_DIR}")


def _openfigi(isins: list[str]) -> dict[str, list[dict]]:
    """ISIN -> OpenFIGI 상장 리스트. 키 없으면 10건/요청·분당 25요청, OPENFIGI_API_KEY 있으면 100건/요청."""
    key = os.getenv("OPENFIGI_API_KEY")
    headers = {"X-OPENFIGI-APIKEY": key} if key else {}
    chunk, pause = (100, 0.3) if key else (10, 2.5)
    out = {}
    for i in range(0, len(isins), chunk):
        batch = isins[i:i + chunk]
        jobs = [{"idType": "ID_ISIN", "idValue": x, "marketSecDes": "Equity"} for x in batch]  # REIT 포함
        r = requests.post("https://api.openfigi.com/v3/mapping", json=jobs, headers=headers, timeout=60)
        r.raise_for_status()
        for isin, res in zip(batch, r.json()):
            out[isin] = res.get("data", [])
        time.sleep(pause)
    return out


def _pick_listing(listings: list[dict], country: str) -> tuple[str | None, str | None, str | None]:
    """(composite FIGI, bbg ticker, exchCode). 도메사일 본상장 > 거래소 우선순위. US 는 실거래소 상장 있을 때만."""
    real_us = {x["ticker"] for x in listings if x["exchCode"] in US_REAL}
    cands = [x for x in listings if x["exchCode"] in EXCH_PRIORITY
             and not (x["exchCode"] == "US" and x["ticker"] not in real_us)]
    pref = COUNTRY_EXCH.get(country, [])
    cands.sort(key=lambda x: (pref.index(x["exchCode"]) if x["exchCode"] in pref else 99,
                              EXCH_PRIORITY.index(x["exchCode"])))
    if cands:
        x = cands[0]
        return x.get("compositeFIGI"), x["ticker"], x["exchCode"]
    return None, None, None


def load_figi_map(book: pd.DataFrame) -> pd.DataFrame:
    """isin -> figi (+bbg_ticker, exch). 캐시에 없는 ISIN 만 OpenFIGI. figi 를 손으로 고치면 존중."""
    cols = ["isin", "figi", "bbg_ticker", "exch"]
    m = pd.read_csv(MAP_PATH) if MAP_PATH.exists() else pd.DataFrame(columns=cols)
    m = m.dropna(subset=["figi"])  # 미매핑은 매번 재시도
    missing = book[~book["isin"].isin(m["isin"])].drop_duplicates("isin")
    if len(missing):
        print(f"[map] OpenFIGI mapping for {len(missing)} isins ...")
        figi = _openfigi(missing["isin"].tolist())
        rows = [dict(zip(cols, (isin, *_pick_listing(figi.get(isin, []), c))))
                for isin, c in zip(missing["isin"], missing["country"])]
        m = pd.concat([m, pd.DataFrame(rows)], ignore_index=True)
        m.to_csv(MAP_PATH, index=False)
    return m


def bdh(securities: list[str], field: str, start, end, currency: str | None = None) -> pd.DataFrame:
    """date x security 일별 필드값 (Bloomberg HistoricalDataRequest). 유일한 가격 소스 접점."""
    import blpapi
    s = blpapi.Session()
    if not (s.start() and s.openService("//blp/refdata")):
        raise RuntimeError("Bloomberg session failed - terminal logged in?")
    svc = s.getService("//blp/refdata")
    rows, errors = [], []
    for i in range(0, len(securities), 200):
        r = svc.createRequest("HistoricalDataRequest")
        for x in securities[i:i + 200]:
            r.getElement("securities").appendValue(x)
        r.getElement("fields").appendValue(field)
        r.set("startDate", pd.Timestamp(start).strftime("%Y%m%d"))
        r.set("endDate", pd.Timestamp(end).strftime("%Y%m%d"))
        r.set("periodicitySelection", "DAILY")
        if currency:
            r.set("currency", currency)
        s.sendRequest(r)
        while True:
            ev = s.nextEvent(60000)
            for msg in ev:
                if not msg.hasElement("securityData"):
                    continue
                sd = msg.getElement("securityData")
                sec = sd.getElementAsString("security")
                if sd.hasElement("securityError"):
                    errors.append((sec, sd.getElement("securityError").getElementAsString("message")))
                    continue
                for fd in sd.getElement("fieldData").values():
                    if fd.hasElement(field):
                        rows.append((sec, fd.getElementAsDatetime("date"), fd.getElementAsFloat(field)))
            if ev.eventType() == blpapi.Event.RESPONSE:
                break
    s.stop()
    for sec, msg in errors:
        print(f"[bbg] {sec}: {msg}")
    px = pd.DataFrame(rows, columns=["sec", "date", "v"]).pivot(index="date", columns="sec", values="v")
    px.index = pd.to_datetime(px.index)
    return px.sort_index()


def bdp(securities: list[str], fields: list[str]) -> pd.DataFrame:
    """security x field 현재값 (Bloomberg ReferenceDataRequest). 종목명(NAME) 등 정적 속성용."""
    import blpapi
    s = blpapi.Session()
    if not (s.start() and s.openService("//blp/refdata")):
        raise RuntimeError("Bloomberg session failed - terminal logged in?")
    svc = s.getService("//blp/refdata")
    out = {}
    for i in range(0, len(securities), 200):
        r = svc.createRequest("ReferenceDataRequest")
        for x in securities[i:i + 200]:
            r.getElement("securities").appendValue(x)
        for f in fields:
            r.getElement("fields").appendValue(f)
        s.sendRequest(r)
        while True:
            ev = s.nextEvent(60000)
            for msg in ev:
                if not msg.hasElement("securityData"):
                    continue
                for sd in msg.getElement("securityData").values():
                    fd = sd.getElement("fieldData")
                    out[sd.getElementAsString("security")] = {
                        f: (fd.getElementAsString(f) if fd.hasElement(f) else None) for f in fields}
            if ev.eventType() == blpapi.Event.RESPONSE:
                break
    s.stop()
    return pd.DataFrame.from_dict(out, orient="index").reindex(securities)


def get_tri(securities: list[str], start, end, currency: str | None = None) -> pd.DataFrame:
    """date x security 총수익지수 (요청 시작일에 리베이스, 배당 재투자 포함)."""
    return bdh(securities, TRI_FIELD, start, end, currency)


def asof_ret(px: pd.DataFrame, base, asof) -> pd.Series:
    """심볼별 (asof 이하 마지막 거래일 / base 이하 마지막 거래일) - 1. 국가별 휴일·시차 대응."""
    p0 = px.loc[:pd.Timestamp(base)].ffill().iloc[-1]
    p1 = px.loc[:pd.Timestamp(asof)].ffill().iloc[-1]
    return p1 / p0 - 1


def attach_securities(w: pd.DataFrame) -> pd.DataFrame:
    """북(isin, ticker, gvkeyiid, ls_weight) 에 country + Bloomberg 조회 식별자 'sec' 를 붙인다.

    MXCN1A 는 ticker 가 이미 Bloomberg 형식("000001 CH Equity") -> 그대로. 아니면 FIGI 매핑 (/bbgid/).
    FIGI 를 못 구한 행은 경고 후 제외.
    """
    w = w.merge(pd.read_parquet(UNIVERSE_DATA_DIR / "country_map.parquet"), on="gvkeyiid", how="left")
    is_bbg = w["ticker"].astype(str).str.endswith(" Equity")
    w[["figi", "bbg_ticker", "exch"]] = None
    if not is_bbg.all():
        m = load_figi_map(w[~is_bbg])
        w = w.drop(columns=["figi", "bbg_ticker", "exch"]).merge(
            m[["isin", "figi", "bbg_ticker", "exch"]], on="isin", how="left")
        no_figi = ~is_bbg & w["figi"].isna()
        if no_figi.any():
            print(f"[warn] {int(no_figi.sum())} isins without FIGI -> fix by hand in {MAP_PATH}")
        w, is_bbg = w[~no_figi], is_bbg[~no_figi]
    w["sec"] = w["ticker"].where(is_bbg, "/bbgid/" + w["figi"].astype(str))
    return w.reset_index(drop=True)


def main(asof: str | None = None) -> None:
    asof = pd.Timestamp(asof) if asof else pd.Timestamp.today().normalize() - pd.Timedelta(days=1)
    base = asof.replace(day=1) - pd.Timedelta(days=1)  # 전월말
    start = base - pd.Timedelta(days=10)

    w, mp_date = latest_mp_weights()
    w = attach_securities(w[["isin", "ticker", "gvkeyiid", "ls_weight"]])
    secs = w["sec"].tolist()
    ret_local = asof_ret(get_tri(secs, start, asof), base, asof)
    ret_usd = asof_ret(get_tri(secs + [BM_SECURITY], start, asof, currency="USD"), base, asof)

    w["mtd_local"] = w["sec"].map(ret_local)
    w["mtd_usd"] = w["sec"].map(ret_usd)
    w["contrib_local"] = w["ls_weight"] * w["mtd_local"]
    w["contrib_usd"] = w["ls_weight"] * w["mtd_usd"]
    w["missing_px"] = w["mtd_usd"].isna()

    out = OUTPUT_DIR / mp_date / f"mtd_pnl_{asof.date()}.csv"
    w.drop(columns="sec").sort_values("contrib_usd").to_csv(out, index=False)

    long_, short_ = w[w["ls_weight"] > 0], w[w["ls_weight"] < 0]
    print(f"\nMTD {base.date()} -> {asof.date()}  (book: weights_mp {mp_date}, n={len(w)}, no price={int(w['missing_px'].sum())})")
    print(f"  {'':12}{'local':>10}{'USD':>10}{'FX':>10}")
    for name, g in [("MP total", w), ("long side", long_), ("short side", short_)]:
        loc, usd = g["contrib_local"].sum(), g["contrib_usd"].sum()
        print(f"  {name:12}{loc:>+10.4%}{usd:>+10.4%}{usd - loc:>+10.4%}   (gross {g['ls_weight'].sum():+.2%})")
    print(f"  BM {BM_SECURITY:9}{'':>10}{ret_usd[BM_SECURITY]:>+10.4%}")
    print(f"  -> {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
