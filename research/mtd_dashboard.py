# -*- coding: utf-8 -*-
"""MTD 일별 손익 대시보드 (정적 HTML).

사용:
    python research/mtd_dashboard.py               # 어제까지
    python research/mtd_dashboard.py 2026-09-15    # 기준일 지정 (캐시 안에서 어느 날이든)

구성:
  1. 일별 수익률 캐시  data/{BM}/daily_ret/{YYYY-MM}.parquet  (date, sec, ret_local, ret_usd; %/100)
     - Bloomberg DAY_TO_DAY_TOT_RETURN_GROSS_DVDS (배당 재투자 포함). 총수익지수 대신 일수익률을 캐시하는 이유:
       총수익지수는 요청 시작일에 리베이스돼 증분 조회가 깨진다. 일수익률은 증분 append 가 안전.
     - 캐시에 없는 (종목, 날짜)만 조회 -> 매일 실행이 수 초.
  2. 리밸런싱 로그  data/{BM}/rebalance_log.csv  (month, rule_date, actual_date, source, note)
     - 규칙 = 월초 3번째 거래일 (BM 지수가 종가를 찍는 날 기준). 행이 없으면 규칙으로 채워 append.
     - actual_date 를 손으로 고치면 source=manual 로 표시. 실제 일자는 매달 남긴다 (연간 분석용).
  3. 두 북 이어붙임: 전월말 ~ 리밸런싱일 종가 = 전월 북, 그 이후 = 당월 북 (교대는 리밸런싱일 종가).
     - realized  : 이어붙인 실현 기준
     - model     : 당월 북을 전월말부터 적용 (백테스트 가정)
     - timing    : realized - model
  4. 출력: output/{BM}/<MP 기준일>/mtd_dashboard.html (+ mtd_daily_<asof>.csv)
     - 두 번째 탭 "현재 포트" 는 당월 북(캡 후 배포 비중)으로 스타일 배분·섹터/종목 스타일 분해·팩터 비중 변화를
       같은 디자인으로 그린다 (구 dashboard_*.html 의 '현재 포트/배팅' 섹션 재조판, 2026-09-18)
     - HTML 에는 종목 x 날짜 일수익률을 넣고, 리밸런싱일(월말/N영업일/로그 실제값) 과 전월 북 규모 맞춤 토글을
       브라우저에서 바꿔 가며 이어붙임을 재계산한다. 콘솔/CSV 는 로그의 실제 리밸런싱일 기준.

북 가치는 buy-and-hold: 종목별 누적성장 G 로 P&L = sum w_i (G_i - 1). 일별 기여 = w_i * G_{i,t-1} * r_{i,t}.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mtd_pnl import BM_SECURITY, attach_securities, bdh, bdp  # noqa: E402
from config import PARAM, PIPELINE_PARAMS  # noqa: E402
from service.paths import DATA_DIR, DATE_GLOB, OUTPUT_DIR, UNIVERSE_DATA_DIR  # noqa: E402
from service.pipeline.weight_construction import resolve_target_gross  # noqa: E402

RET_FIELD = "DAY_TO_DAY_TOT_RETURN_GROSS_DVDS"
CACHE_DIR = UNIVERSE_DATA_DIR / "daily_ret"
REBAL_LOG = UNIVERSE_DATA_DIR / "rebalance_log.csv"
RULE_NTH_TRADING_DAY = 3
BM = PARAM["benchmark"]
FACTOR_STYLE: dict[str, str] = {}  # load_book 이 채움 (팩터 -> 스타일)
FACTOR_WEIGHT: dict[str, dict[str, float]] = {}  # load_book 이 채움 (북 날짜 -> {팩터: factor_weight, 캡 후 배분 합~1})
NAMES_PATH = UNIVERSE_DATA_DIR / "security_names.csv"


def security_names(secs: list[str]) -> dict[str, str]:
    """sec -> 종목명. 캐시(security_names.csv)에 없는 것만 Bloomberg NAME 조회."""
    cache = pd.read_csv(NAMES_PATH) if NAMES_PATH.exists() else pd.DataFrame(columns=["sec", "name"])
    have = dict(zip(cache["sec"], cache["name"]))
    missing = [x for x in secs if x not in have]
    if missing:
        print(f"[names] Bloomberg NAME for {len(missing)} securities")
        got = bdp(missing, ["NAME"])["NAME"].dropna()
        have.update(got.to_dict())
        pd.DataFrame({"sec": list(have), "name": list(have.values())}).to_csv(NAMES_PATH, index=False)
    return have


def factor_meta() -> dict[str, str]:
    """팩터 -> 영문 정식명 (factor_info.csv, 전 팩터 존재). 한글 설명(factor_desc_kr.csv)은 일부만 있어 일관성 때문에 안 씀."""
    info = pd.read_csv(DATA_DIR / "factor_info.csv").drop_duplicates("factorAbbreviation").set_index("factorAbbreviation")
    return info["factorName"].astype(str).to_dict()


def deploy_multiplier_series(months: pd.DatetimeIndex) -> pd.Series:
    """월별 배포 배수 = 목표 gross(mp_target_gross.csv 이력) / 북 gross(deploy_multiplier_*.csv 스냅샷).

    사용자 규칙 (2026-09-21): 두 이력 모두 계단식 — 변경 전까지 직전 값을 계속 쓰고(ffill), 최초 기록 이전 달은
    최초 값을 소급(bfill). 결과는 MXWO 의 기록된 multiplier 와 일치하고 MXCN1A 는 전 기간 14% 소급과 일치한다.
    """
    tg = pd.read_csv(UNIVERSE_DATA_DIR / "mp_target_gross.csv", parse_dates=["effective_date"]).sort_values("effective_date")
    target = tg.set_index("effective_date")["target_gross"].astype(float)
    snaps = sorted((OUTPUT_DIR / "mp_weight_history").glob("deploy_multiplier_*.csv"))
    bg = pd.concat([pd.read_csv(f, parse_dates=["as_of"]) for f in snaps]).set_index("as_of")["book_gross_before"].astype(float).sort_index()
    idx = months.union(target.index).union(bg.index)
    t = target.reindex(idx).ffill().bfill()
    g = bg.reindex(idx).ffill().bfill()
    return (t / g).reindex(months)


def factor_horizons(book_dir: Path, factors: list[str]) -> tuple[dict, str | None]:
    """팩터별 1M/3M/YTD/1Y 롱숏 복리수익률 (factor_returns_matrix_*.csv, 한쪽 다리 100% 기준, 마지막 월말까지).

    대시보드는 여기에 **현재 배포 한쪽 다리 크기** ((롱+|숏|)/2, 캡 후) 를 곱해 "현재 배포 비중으로 그 기간 보유했다면"
    의 기여로 보여준다 (사용자 지정 2026-09-21). 배수 이력 방식(factor_contrib x 배포 배수)은 deploy_multiplier_series 참조.
    """
    files = sorted(book_dir.glob("factor_returns_matrix_*.csv"))
    if not files:
        return {}, None
    m = pd.read_csv(files[-1], index_col=0, parse_dates=True).sort_index()
    last = m.index[-1]
    win = {"1M": m.iloc[-1:], "3M": m.iloc[-3:], "YTD": m[m.index.year == last.year], "1Y": m.iloc[-12:]}
    out = {f: {k: (float((1 + v[f].fillna(0.0)).prod() - 1) if v[f].notna().any() else None) for k, v in win.items()}
           for f in factors if f in m.columns}
    return out, str(last.date())


BM_UNIV_PATH = UNIVERSE_DATA_DIR / "bm_universe.csv"  # 실제 BM 구성종목 (Bloomberg PORT 내보내기에서 추출, 소형주 제외)


def bm_restrict(book: pd.DataFrame) -> tuple[pd.Series, pd.Series, dict]:
    """MP 를 실제 BM 유니버스로 제한하고 롱·숏 각각을 원래 목표(각 gross/2)로 비례 조정 (2026-09-21 사용자 지정).

    반환: (조정 비중, 종목별 배율(스타일 열에 같이 곱함), 조정 내역 dict). 파일이 없으면 (원본, 1, {}).
    """
    if not BM_UNIV_PATH.exists():
        return book["ls_weight"], pd.Series(1.0, index=book.index), {}
    univ = set(pd.read_csv(BM_UNIV_PATH)["ticker"])
    w = book["ls_weight"]
    keep = book["ticker"].isin(univ)
    w0 = w.where(keep, 0.0)
    target = float(w.abs().sum()) / 2
    long0, short0 = float(w0[w0 > 0].sum()), float(-w0[w0 < 0].sum())
    sl, ss = (target / long0 if long0 > 0 else 1.0), (target / short0 if short0 > 0 else 1.0)
    factor = pd.Series(0.0, index=book.index)
    factor[keep & (w > 0)] = sl
    factor[keep & (w < 0)] = ss
    adj = {"n_univ": len(univ), "n_book": int((w != 0).sum()), "n_removed": int((~keep & (w != 0)).sum()),
           "removed_long": float(w[~keep & (w > 0)].sum()), "removed_short": float(w[~keep & (w < 0)].sum()),
           "long_before": long0, "short_before": short0, "scale_long": sl, "scale_short": ss, "target": target}
    return w * factor, factor, adj


# ── 북 로딩 ─────────────────────────────────────────────────────────────────
def month_dirs() -> list[Path]:
    return sorted(OUTPUT_DIR.glob(DATE_GLOB))


def load_book(d: Path) -> pd.DataFrame | None:
    """기준일 폴더의 배포 MP 북 (isin, ticker, gvkeyiid, sec, ls_weight, style_share...).

    신형 weights_mp_*.csv 또는 구형 total_aggregated_weights_*_mp*.csv (style=='MP' 행). 없으면 None.
    스타일 분해용으로 weights_factor / total_aggregated (팩터 행) 에서 종목별 스타일 비중 share 도 만든다.
    """
    mp_files = sorted(d.glob("weights_mp_*.csv")) or sorted(
        f for f in d.glob("total_aggregated_weights_*_mp*.csv") if "style" not in f.name)
    if not mp_files:
        return None
    raw = pd.read_csv(mp_files[-1], index_col=0 if "total_aggregated" in mp_files[-1].name else None)
    fac_files = sorted(d.glob("weights_factor_*.csv")) or mp_files
    fac = pd.read_csv(fac_files[-1], index_col=0)
    fac = fac[fac["style"] != "MP"]
    book = raw[raw["style"] == "MP"] if "style" in raw and (raw["style"] == "MP").any() else raw
    book = book[["isin", "ticker", "gvkeyiid", "ls_weight"]].dropna(subset=["isin"]).copy()
    # 배포 규모의 정본은 mp_target_gross.csv 이력. 파일 gross 가 다르면(구 스냅샷, 소급 변경) 그 값으로 재스케일
    target = resolve_target_gross(d.name, UNIVERSE_DATA_DIR / "mp_target_gross.csv", PIPELINE_PARAMS.get("mp_target_gross"))
    gross = float(book["ls_weight"].abs().sum())
    scale = 1.0
    if target and gross > 0 and abs(gross - target) > 1e-6:
        print(f"[book] {d.name}: file gross {gross:.2%} -> target {target:.2%} (mp_target_gross.csv)")
        scale = target / gross
        book["ls_weight"] = book["ls_weight"] * scale
    sec_map = fac.drop_duplicates("isin").set_index("isin")["sec"]
    book["sector"] = book["isin"].map(sec_map)
    # 스타일별 부호 있는 배포 비중: 팩터별 종목 비중(mp_ls_weight, 배포 배수 적용값)을 스타일로 합산 후 같은 scale.
    # 스타일 합은 종목 MP 비중과 거의 같지만 섹터 숏캡/EMA 스무딩 이전 값이라 소폭 차이 (근사 주석은 HTML 에)
    # 섹터 숏캡·스무딩은 종목 MP 행에만 적용되고 팩터 행(mp_ls_weight)은 캡 전 값이다.
    # 종목별 비율 (캡 후 MP 비중 / 캡 전 팩터 합) 을 그 종목의 팩터·스타일 비중에 곱해 캡 적용 후 분해를 만든다
    # -> 스타일/팩터 기여 합 == MP 기여 (배포 scale 도 이 비율에 포함).
    nz = fac[fac["mp_ls_weight"] != 0].copy()
    pre = nz.groupby("isin")["mp_ls_weight"].sum()
    ratio = (book.set_index("isin")["ls_weight"] / pre).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    nz["mp_ls_weight"] = nz["mp_ls_weight"] * nz["isin"].map(ratio).fillna(0.0)
    st = nz.groupby(["isin", "style"])["mp_ls_weight"].sum().unstack(fill_value=0.0)
    book = book.merge(st.add_prefix("style:"), left_on="isin", right_index=True, how="left")
    # 팩터별 종목 비중 (팩터 기여 상하위 표용) + 팩터->스타일
    fw = nz.groupby(["isin", "factor"])["mp_ls_weight"].sum()
    # apply 가 dict 를 MultiIndex 로 펼치므로 루프로 만든다. attrs 는 merge 에서 사라지므로 모듈 전역에 누적.
    fdict = {isin: dict(zip(g.index.get_level_values(1), map(float, g.values))) for isin, g in fw.groupby(level=0)}
    book["factors"] = book["isin"].map(fdict)
    FACTOR_STYLE.update(nz.drop_duplicates("factor").set_index("factor")["style"].to_dict())
    fwt = fac[fac["factor_weight"] > 0].drop_duplicates("factor").set_index("factor")["factor_weight"]
    FACTOR_WEIGHT[d.name] = {k: float(v) for k, v in fwt.items()}
    return book


# ── 일별 수익률 캐시 ────────────────────────────────────────────────────────
def load_cache(month: str) -> pd.DataFrame:
    f = CACHE_DIR / f"{month}.parquet"
    if f.exists():
        return pd.read_parquet(f)
    return pd.DataFrame(columns=["date", "sec", "ret_local", "ret_usd"])


def fetch_ret(secs: list[str], start, end) -> pd.DataFrame:
    loc = bdh(secs, RET_FIELD, start, end).stack().rename("ret_local") / 100
    usd = bdh(secs, RET_FIELD, start, end, currency="USD").stack().rename("ret_usd") / 100
    df = pd.concat([loc, usd], axis=1).reset_index()
    return df.rename(columns={"level_1": "sec"}) if "level_1" in df else df


def update_cache(month: str, secs: list[str], start, end) -> pd.DataFrame:
    """(secs, start..end) 가 캐시에 있도록 증분 조회 후 저장. 반환: 캐시 전체."""
    cache = load_cache(month)
    have_max = cache.groupby("sec")["date"].max() if len(cache) else pd.Series(dtype="datetime64[ns]")
    new_secs = [s for s in secs if s not in have_max.index]
    parts = []
    if new_secs:
        print(f"[cache] {month}: {len(new_secs)} new securities, full range")
        parts.append(fetch_ret(new_secs, start, end))
    old_secs = [s for s in secs if s in have_max.index]
    if old_secs:
        since = have_max[old_secs].max() + pd.Timedelta(days=1)  # 최신 날짜 기준 (결측 종목이 있어도 매일 전체 재조회 안 함)
        if since <= pd.Timestamp(end):
            print(f"[cache] {month}: incremental {since.date()} -> {pd.Timestamp(end).date()} for {len(old_secs)} securities")
            parts.append(fetch_ret(old_secs, since, end))
    # BM 지수는 종목보다 늦게 확정되는 날이 있어(예: MXCN1A 9/21) 최근 5거래일을 매번 다시 받아 덮어쓴다
    if len(cache) and BM_SECURITY in secs:
        parts.append(fetch_ret([BM_SECURITY], pd.Timestamp(end) - pd.Timedelta(days=7), end))
    if parts:
        cache = pd.concat([cache, *parts], ignore_index=True)
        cache["date"] = pd.to_datetime(cache["date"])
        cache = cache.drop_duplicates(["date", "sec"], keep="last").sort_values(["date", "sec"])
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.to_parquet(CACHE_DIR / f"{month}.parquet", index=False)
    return cache


# ── 리밸런싱 로그 ───────────────────────────────────────────────────────────
def rebalance_date(month: str, trading_days: pd.DatetimeIndex) -> dict:
    """{'rule_date','actual_date','source','note'}. 로그에 없으면 규칙으로 채워 저장."""
    rule = trading_days[RULE_NTH_TRADING_DAY - 1] if len(trading_days) >= RULE_NTH_TRADING_DAY else trading_days[-1]
    log = pd.read_csv(REBAL_LOG, dtype=str) if REBAL_LOG.exists() else pd.DataFrame(
        columns=["month", "rule_date", "actual_date", "source", "note"])
    row = log[log["month"] == month]
    if row.empty:
        entry = {"month": month, "rule_date": str(rule.date()), "actual_date": str(rule.date()),
                 "source": "rule", "note": f"{RULE_NTH_TRADING_DAY}번째 거래일 (자동)"}
        log = pd.concat([log, pd.DataFrame([entry])], ignore_index=True)
        log.to_csv(REBAL_LOG, index=False)
        return entry
    entry = row.iloc[0].to_dict()
    entry["rule_date"] = str(rule.date())
    entry["source"] = "manual" if entry["actual_date"] != entry["rule_date"] else "rule"
    return entry


# ── 손익 계산 ───────────────────────────────────────────────────────────────
def book_pnl(book: pd.DataFrame, ret: pd.DataFrame, dates: pd.DatetimeIndex, col: str) -> tuple[pd.DataFrame, int]:
    """종목 x 날짜 누적 기여 (buy-and-hold). 반환: (cum_contrib[isin x date], 수익률 결측 종목 수)."""
    r = ret.pivot(index="date", columns="sec", values=col).reindex(dates)
    r = r.reindex(columns=book["sec"])
    missing = int(r.isna().all().sum())
    g = (1 + r.fillna(0.0)).cumprod()
    cum = (g - 1).mul(book["ls_weight"].values, axis=1)
    cum.columns = book["isin"].values
    return cum.T, missing


def build(asof: pd.Timestamp) -> dict:
    dirs = month_dirs()
    cur_dir = [d for d in dirs if pd.Timestamp(d.name) < asof][-1]
    prev_dir = [d for d in dirs if pd.Timestamp(d.name) < pd.Timestamp(cur_dir.name)]
    base = pd.Timestamp(cur_dir.name)
    month = asof.strftime("%Y-%m")
    if base.strftime("%Y-%m") == month:  # 기준일이 당월 말과 같은 달이면 그 북은 아직 미배포
        raise ValueError(f"asof {asof.date()} 가 최신 북 {base.date()} 과 같은 달 - 다음 달 데이터부터")

    cur = attach_securities(load_book(cur_dir))
    prev = attach_securities(load_book(prev_dir[-1])) if prev_dir and load_book(prev_dir[-1]) is not None else None
    secs = sorted(set(cur["sec"]) | (set(prev["sec"]) if prev is not None else set()) | {BM_SECURITY})
    ret = update_cache(month, secs, base + pd.Timedelta(days=1), asof)
    ret = ret[(ret["date"] > base) & (ret["date"] <= asof)]

    # 거래일 = 종목 과반이 수익률을 찍은 날 (BM 지수만 늦게 확정되는 날도 포함). BM 결측일은 0 으로 채움
    cnt = ret[ret["sec"] != BM_SECURITY].groupby("date")["sec"].nunique()
    dates = cnt[cnt >= 0.5 * cnt.max()].index.sort_values()
    bm = ret[ret["sec"] == BM_SECURITY].set_index("date")["ret_usd"].reindex(dates).fillna(0.0)
    rb = rebalance_date(month, dates)
    rb_date = pd.Timestamp(rb["actual_date"])
    seg_a, seg_b = dates[dates <= rb_date], dates[dates > rb_date]

    out = {"universe": BM, "asof": str(asof.date()), "base": str(base.date()), "book_date": cur_dir.name,
           "prev_book_date": prev_dir[-1].name if prev is not None else None, "rebalance": rb,
           "dates": [str(d.date()) for d in dates], "bm_cum": ((1 + bm).cumprod() - 1).round(6).tolist(),
           "n_cur": len(cur), "n_prev": len(prev) if prev is not None else 0,
           "gross_cur": float(cur["ls_weight"].abs().sum()), "gross_prev": float(prev["ls_weight"].abs().sum()) if prev is not None else None}

    model_usd, miss = book_pnl(cur, ret, dates, "ret_usd")
    model_loc, _ = book_pnl(cur, ret, dates, "ret_local")
    out["missing_cur"] = miss
    realized_usd, realized_loc = model_usd * 0.0, model_loc * 0.0
    if prev is not None and len(seg_a):
        a_usd, miss_p = book_pnl(prev, ret, seg_a, "ret_usd")
        a_loc, _ = book_pnl(prev, ret, seg_a, "ret_local")
        out["missing_prev"] = miss_p
    else:
        a_usd = a_loc = None
        out["missing_prev"] = 0
    b_usd, _ = book_pnl(cur, ret, seg_b, "ret_usd") if len(seg_b) else (None, 0)
    b_loc, _ = book_pnl(cur, ret, seg_b, "ret_local") if len(seg_b) else (None, 0)

    def stitch(a, b, cols_prev, cols_cur):
        """실현 기준 종목 x 날짜: 구간 A 는 전월 북 누적, 구간 B 는 A 말값(전월 북 종목) + 당월 북 누적."""
        idx = sorted(set(cols_prev) | set(cols_cur))
        m = pd.DataFrame(0.0, index=idx, columns=dates)
        if a is not None:
            m.loc[a.index, a.columns] = a.values
            if len(seg_b):
                m.loc[a.index, seg_b] = np.repeat(a.iloc[:, -1].values[:, None], len(seg_b), axis=1)
        if b is not None:
            m.loc[b.index, b.columns] += b.values
        return m
    prev_isins = list(prev["isin"]) if prev is not None else []
    realized_usd = stitch(a_usd, b_usd, prev_isins, list(cur["isin"]))
    realized_loc = stitch(a_loc, b_loc, prev_isins, list(cur["isin"]))

    out["series"] = {
        "model_usd": model_usd.sum().round(6).tolist(), "model_local": model_loc.sum().round(6).tolist(),
        "realized_usd": realized_usd.sum().round(6).tolist(), "realized_local": realized_loc.sum().round(6).tolist(),
        "prev_seg_usd": (a_usd.sum().reindex(dates).ffill().fillna(0).round(6).tolist() if a_usd is not None else [0.0] * len(dates)),
    }
    # 종목 메타 + 종목 x 날짜 누적기여 (USD, realized / model) -> JS 에서 날짜별 분해
    meta = pd.concat([prev.assign(book="prev") if prev is not None else None, cur.assign(book="cur")]) if prev is not None else cur.assign(book="cur")
    style_cols = sorted({c for c in cur.columns if c.startswith("style:")} | ({c for c in prev.columns if c.startswith("style:")} if prev is not None else set()))
    w_prev = prev.set_index("isin")["ls_weight"] if prev is not None else pd.Series(dtype=float)
    w_cur = cur.set_index("isin")["ls_weight"]
    s_prev = prev.set_index("isin").reindex(columns=style_cols).fillna(0.0) if prev is not None else None
    s_cur = cur.set_index("isin").reindex(columns=style_cols).fillna(0.0)
    m1 = meta.drop_duplicates("isin", keep="last").set_index("isin")

    def style_w(tbl, isin):
        if tbl is None or isin not in tbl.index:
            return {}
        r = tbl.loc[isin]
        return {c[6:]: round(float(r[c]), 8) for c in style_cols if r[c] != 0}
    factor_style = dict(FACTOR_STYLE)
    factors = sorted(factor_style)
    fidx = {f: i for i, f in enumerate(factors)}
    f_prev = prev.set_index("isin")["factors"] if prev is not None else pd.Series(dtype=object)
    f_cur = cur.set_index("isin")["factors"]

    def fac_w(tbl, isin):
        d = tbl.get(isin) if isin in tbl.index else None
        return [[fidx[k], round(v, 8)] for k, v in d.items()] if isinstance(d, dict) else []
    names = security_names(sorted(set(m1["sec"])))
    # BM 유니버스 제한본 (임시 탭): 비중·스타일 열에 종목별 배율 적용
    wb_cur, fb_cur, adj_cur = bm_restrict(cur)
    wb_cur.index = fb_cur.index = cur["isin"].values
    if prev is not None:
        wb_prev, fb_prev, adj_prev = bm_restrict(prev)
        wb_prev.index = fb_prev.index = prev["isin"].values
    else:
        wb_prev, fb_prev, adj_prev = pd.Series(dtype=float), pd.Series(dtype=float), {}
    out["bm_adj"] = {"cur": adj_cur, "prev": adj_prev, "enabled": bool(adj_cur)}

    def scaled(d, f):
        return {k: v * f for k, v in d.items()} if f else {}
    stocks = []
    for isin in realized_usd.index:
        row = m1.loc[isin]
        sp_, sc_ = style_w(s_prev, isin), style_w(s_cur, isin)
        stocks.append({"isin": isin, "ticker": str(row["ticker"]) if pd.notna(row["ticker"]) else "", "name": names.get(row["sec"], ""),
                       "sector": row["sector"] if pd.notna(row["sector"]) else "n/a",
                       "country": row["country"] if pd.notna(row["country"]) else "n/a",
                       "w_prev": float(w_prev.get(isin, 0.0)), "w_cur": float(w_cur.get(isin, 0.0)),
                       "style_prev": sp_, "style_cur": sc_,
                       "f_prev": fac_w(f_prev, isin), "f_cur": fac_w(f_cur, isin),
                       "wb_prev": float(wb_prev.get(isin, 0.0)), "wb_cur": float(wb_cur.get(isin, 0.0)),
                       "sb_prev": scaled(sp_, float(fb_prev.get(isin, 0.0))), "sb_cur": scaled(sc_, float(fb_cur.get(isin, 0.0)))})
    out["stocks"] = stocks
    out["factors"] = factors
    out["factor_style"] = factor_style
    fm = factor_meta()
    out["factor_name"] = {f: fm.get(f, "") for f in factors}
    out["factor_weight"] = FACTOR_WEIGHT.get(cur_dir.name, {})
    out["factor_weight_prev"] = FACTOR_WEIGHT.get(prev_dir[-1].name, {}) if prev_dir else {}
    out["style_cap"] = float(PIPELINE_PARAMS.get("style_cap", 0.25))
    out["factor_horizon"], out["horizon_asof"] = factor_horizons(cur_dir, factors)
    # 종목 x 날짜 일수익률(USD, 결측 0) -> 브라우저가 임의 리밸런싱일로 이어붙임을 다시 계산
    isin2sec = m1["sec"]
    r = ret.pivot(index="date", columns="sec", values="ret_usd").reindex(dates)
    r = r.reindex(columns=[isin2sec[i] for i in realized_usd.index]).fillna(0.0)
    out["ret"] = r.T.round(6).values.tolist()
    out["styles"] = [c[6:] for c in style_cols]
    return out


# ── HTML ────────────────────────────────────────────────────────────────────
def render_html(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    tpl = (Path(__file__).parent / "mtd_dashboard_template.html").read_text(encoding="utf-8")
    return tpl.replace("/*__DATA__*/null", payload)


def main(asof: str | None = None) -> None:
    asof = pd.Timestamp(asof) if asof else pd.Timestamp.today().normalize() - pd.Timedelta(days=1)
    data = build(asof)
    out_dir = OUTPUT_DIR / data["book_date"]
    html = out_dir / "mtd_dashboard.html"
    html.write_text(render_html(data), encoding="utf-8")
    daily = pd.DataFrame({"date": data["dates"], "bm": data["bm_cum"], **data["series"]})
    daily.to_csv(out_dir / f"mtd_daily_{asof.date()}.csv", index=False)
    s = data["series"]
    rb = data["rebalance"]
    print(f"\n{BM} MTD {data['base']} -> {data['asof']}  (books: prev {data['prev_book_date']} gross {data['gross_prev']:.2%} | "
          f"cur {data['book_date']} gross {data['gross_cur']:.2%})")
    print(f"  rebalance {rb['actual_date']} ({rb['source']}, rule {rb['rule_date']})  missing ret: cur {data['missing_cur']} prev {data['missing_prev']}")
    print(f"  realized  {s['realized_usd'][-1]:+.4%} (local {s['realized_local'][-1]:+.4%})   prev-book segment {s['prev_seg_usd'][-1]:+.4%}")
    print(f"  model     {s['model_usd'][-1]:+.4%} (local {s['model_local'][-1]:+.4%})   timing {s['realized_usd'][-1] - s['model_usd'][-1]:+.4%}")
    print(f"  BM        {data['bm_cum'][-1]:+.4%}\n  -> {html}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
