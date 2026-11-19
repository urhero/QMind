from __future__ import annotations

"""일별 직접 적재: 세 지수 구성 라인과 그 라인의 S&P AFL 일별 팩터값을 kb_global 에 쌓는다 (2026-11-06).

원천 일별 표(ciqAfValueDailyNA / ciqAFValueDailyIntl)는 약 22영업일만 남기고 지운다 (2026-11-01 -> 11-06 에
NA 시작일이 09-30 -> 10-05 로 밀림). 지나간 날은 다시 받을 수 없어 매일 원천에 있는 날짜를 받아 쌓는다.
가격·거래량·총수익은 원천이 전 이력을 보관하므로 복사하지 않고 view(dbo.v_px_d) 로 읽는다.

흐름 (날짜 오름차순):
1. 원천 날짜 찾기: 최근 평일마다 (표, 유니버스, 날짜) seek. 날짜 전체 집계는 표가 커서 수 분 걸린다.
2. 받을 날짜: 아직 없는 날짜 + 이미 받은 최근 REVISIT_DAYS 개 (원천 사후 수정 반영).
3. 지수별 라인 (univ_line_d): 그날 바스켓 -> select_lines (월간 direct-load 와 같은 규칙, 연속성 기준 = 직전 저장일)
   -> price_lines (같은 회사 같은 ISIN 에서 선택 라인 거래대금이 최다 라인의 5% 미만이면 가격은 최다 라인).
4. 팩터값 (afl_value_d): 세 지수 라인 합집합 x 기본 팩터를 서버 안에서 임시표로 모은 뒤 바뀐 값만 고치고 없는 행만 넣는다.
   지우지 않는다. 임시표 행이 저장 행의 90% 미만이면 그 (날짜, 원천) 은 건너뛴다 (원천이 지우는 중이거나 덜 들어옴).
기본은 쓰기 없이 건수만 보고하고, write=True 일 때만 쓴다. 실행 기록은 direct_load_d_log.
"""

import logging

import pandas as pd
import sqlalchemy as sql

import config
from db.factor_direct import (
    _active_on,
    _basket_engine,
    _dates,
    _read_table_history,
    _spg_engine,
    latest_basket,
    select_lines,
)

logger = logging.getLogger(__name__)

DAILY_UNIVERSES = {  # 이름: (MSCIDB 바스켓 코드, 라인 연속성 초깃값을 읽을 월간 표)
    "MXWO": ("990100", "clarifi_mxwo_afl"),
    "MXCN1A": ("133333", "clarifi_mxcn1a_afl"),
    "718708": ("718708", "clarifi_718708_afl"),
}
DAILY_SOURCES = (("ciqAFValueDailyIntl", (2111, 2112, 2114)), ("ciqAfValueDailyNA", (2109, 2110)))
BASE_FACTOR = 1001  # ciqAfFactor.factorRelativeId: 기본 팩터 (DM/EM 상대값 변형 제외, 466개)
PROBE_DAYS = 45  # 원천 날짜 탐색 창 (평일)
REVISIT_DAYS = 5
MIN_ROW_RATIO = 0.9
SWITCH_SHARE = 0.05
TRADED_DAYS = 30  # 거래대금 중앙값 창 (달력일)
BASKET_LOOKBACK_DAYS = 10


# ─────────────────────────────────────────────────────────────────────────────
# 순수 함수
# ─────────────────────────────────────────────────────────────────────────────
def weekdays_back(end: pd.Timestamp, n: int) -> list[pd.Timestamp]:
    """end 이하 평일 n 개 (오름차순)."""
    return list(pd.bdate_range(end=pd.Timestamp(end).normalize(), periods=n))


def dates_to_load(source_dates, stored: pd.Series, revisit: int) -> list[pd.Timestamp]:
    """원천에 있는 날짜 중 아직 없는 날짜 + 최근 revisit 개. 원천에서 사라진 날짜는 건드리지 않는다."""
    src = sorted(set(pd.to_datetime(pd.Index(source_dates))))
    recent = set(src[-revisit:]) if revisit else set()
    have = set(pd.to_datetime(stored.index))
    return [d for d in src if d not in have or d in recent]


def guard_ok(n_new: int, n_stored: int, ratio: float = MIN_ROW_RATIO) -> bool:
    """새로 만든 행이 저장된 행보다 크게 적으면 원천이 덜 들어왔거나 지우는 중이다."""
    return n_stored == 0 or n_new >= ratio * n_stored


def price_lines(lines: pd.DataFrame, isin_hist: pd.DataFrame, on_date: pd.Timestamp, traded: pd.Series) -> pd.Series:
    """선택 라인 -> 가격 라인 (gvkeyiid). lines = [gvkeyiid, isin(바스켓)], traded = 라인별 달러 거래대금 중앙값.

    후보는 같은 회사(gvkey)에 그날 같은 ISIN 이 붙은 라인뿐이다 (우선주 등 다른 증권은 제외).
    선택 라인 거래대금이 후보 최다의 SWITCH_SHARE 미만일 때만 바꾼다: 2026-11-01 MXWO 6라인(0.26%) 이
    독일·미국 장외 등 2차 상장이었다 (ACN 02W 등). 비슷한 이중 상장은 그대로 둔다 (날마다 바뀌지 않게).
    """
    cand = isin_hist.loc[_active_on(isin_hist, on_date), ["gvkey", "iid", "isin"]]
    cand = cand.assign(px=cand["gvkey"] + cand["iid"])[["gvkey", "isin", "px"]]
    m = lines[["gvkeyiid", "isin"]].assign(gvkey=lines["gvkeyiid"].str[:6]).merge(cand, on=["gvkey", "isin"], how="left")
    m["px"] = m["px"].fillna(m["gvkeyiid"])
    m["traded"] = m["px"].map(traded).fillna(0.0)
    best = m.sort_values("traded", ascending=False, kind="stable").drop_duplicates("gvkeyiid").set_index("gvkeyiid")
    own = best.index.to_series().map(traded).fillna(0.0)
    switch = (best["traded"] > 0) & (own < SWITCH_SHARE * best["traded"])
    out = best["px"].where(switch, best.index.to_series())
    return out.reindex(lines["gvkeyiid"]).rename("px_gvkeyiid")


# ─────────────────────────────────────────────────────────────────────────────
# DB
# ─────────────────────────────────────────────────────────────────────────────
def _source_dates(conn, candidates: list[pd.Timestamp]) -> dict[str, list[pd.Timestamp]]:
    """원천 표별 행이 있는 날짜. PK 가 (universeId, asOfDate, ...) 라 seek 한 번씩."""
    out = {}
    for source, universes in DAILY_SOURCES:
        q = sql.text(f"SELECT TOP 1 1 FROM xf_target.dbo.{source} WHERE universeId = :u AND asOfDate = :d")
        out[source] = sorted({d for d in candidates for u in universes if conn.execute(q, {"u": u, "d": d.date()}).first()})
    return out


def _stored_counts(conn, since: pd.Timestamp) -> dict[str, pd.Series]:
    """원천 표별 저장된 날짜와 행 수."""
    out = {}
    for source, universes in DAILY_SOURCES:
        df = pd.read_sql_query(
            sql.text(f"SELECT asOfDate, COUNT(*) n FROM dbo.afl_value_d WHERE asOfDate >= :a AND universeId IN ({','.join(map(str, universes))}) GROUP BY asOfDate"),
            conn,
            params={"a": since.date()},
        )
        out[source] = pd.Series(df["n"].values, index=pd.to_datetime(df["asOfDate"]), dtype=int)
    return out


def _read_basket_days(code: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """[start - 여유, end] 바스켓 전체 (날짜별 latest_basket 은 호출 쪽에서)."""
    engine = _basket_engine()
    with engine.connect() as conn:
        b = pd.read_sql_query(
            sql.text("EXEC dbo.spg_msci_wgt_Daily :start, :end, :code"),
            conn,
            params={"start": (start - pd.Timedelta(days=BASKET_LOOKBACK_DAYS)).strftime("%Y-%m-%d"), "end": end.strftime("%Y-%m-%d"), "code": code},
        )
    engine.dispose()
    return b


def _reference(conn) -> dict[str, pd.DataFrame]:
    """select_lines 재료 (실행마다 한 번)."""
    return {
        "isin_hist": _dates(pd.read_sql_query(sql.text("SELECT gvkey, iid, itemvalue isin, efffrom, effthru FROM xf_target.dbo.sec_idhist WHERE item = 'ISIN'"), conn), "efffrom", "effthru"),
        "primary": _dates(
            pd.read_sql_query(
                sql.text("SELECT gvkey, itemvalue iid, item, efffrom, effthru FROM xf_target.dbo.sec_idhist WHERE item IN ('PRIHISTROW', 'PRIHISTUSA', 'PRIHISTCAN') AND itemvalue IS NOT NULL"),
                conn,
            ),
            "efffrom",
            "effthru",
        ),
        "company": pd.read_sql_query(sql.text("SELECT gvkey, loc FROM xf_target.dbo.company"), conn),
        "security": pd.read_sql_query(sql.text("SELECT gvkey, iid, exchg, secstat FROM xf_target.dbo.security"), conn),
    }


def _traded_value(conn, gvkeys: list[str], end: pd.Timestamp) -> pd.Series:
    """라인별 최근 TRADED_DAYS 달러 거래대금 중앙값 (prccd x cshtrd x 환율), 여러 라인이 있는 회사만."""
    if not gvkeys:
        return pd.Series(dtype=float)
    conn.exec_driver_sql("DROP TABLE IF EXISTS #gv")
    conn.exec_driver_sql("CREATE TABLE #gv (gvkey varchar(6) COLLATE DATABASE_DEFAULT PRIMARY KEY)")
    conn.exec_driver_sql("INSERT INTO #gv VALUES (?)", [(g,) for g in gvkeys])
    df = pd.read_sql_query(
        sql.text(
            "SELECT v.gvkeyiid, v.px_close * v.volume * v.usd_per_local traded FROM #gv g "
            "JOIN dbo.v_px_d v ON v.gvkey = g.gvkey WHERE v.ddt > :a AND v.ddt <= :d"
        ),
        conn,
        params={"a": (end - pd.Timedelta(days=TRADED_DAYS)).date(), "d": end.date()},
    )
    return df.groupby("gvkeyiid")["traded"].median()


def _seen_lines(conn, universe: str, before: pd.Timestamp) -> pd.Series:
    """라인별 마지막 저장일 (before 이전). 없으면 빈 Series."""
    df = pd.read_sql_query(
        sql.text("SELECT gvkey + iid gvkeyiid, MAX(ddt) last_ddt FROM dbo.univ_line_d WHERE universe = :u AND ddt < :d GROUP BY gvkey, iid"),
        conn,
        params={"u": universe, "d": before.date()},
    )
    return pd.to_datetime(df.set_index("gvkeyiid")["last_ddt"])


def _log(conn, run_at, ddt, part, n_src, n_stored, n_ins, n_upd, status, note=None) -> None:
    """실행 기록. 쓰기 없는 실행(dry)은 남기지 않는다."""
    if status == "dry":
        return
    conn.execute(
        sql.text("INSERT INTO dbo.direct_load_d_log VALUES (:r, :d, :p, :s, :st, :i, :u, :status, :note)"),
        {"r": run_at, "d": ddt.date(), "p": part, "s": n_src, "st": n_stored, "i": n_ins, "u": n_upd, "status": status, "note": note},
    )


def _write_lines(conn, universe: str, ddt: pd.Timestamp, lines: pd.DataFrame, write: bool, run_at) -> dict:
    """(지수, 날짜) 라인을 통째로 바꾼다 (파생 행이라 교체). 새 라인이 저장분의 90% 미만이면 건너뛴다."""
    n_stored = conn.execute(sql.text("SELECT COUNT(*) FROM dbo.univ_line_d WHERE universe = :u AND ddt = :d"), {"u": universe, "d": ddt.date()}).scalar()
    ok = guard_ok(len(lines), n_stored)
    status = ("ok" if ok else "skip") if write else "dry"
    if write and ok:
        conn.execute(sql.text("DELETE FROM dbo.univ_line_d WHERE universe = :u AND ddt = :d"), {"u": universe, "d": ddt.date()})
        rows = [
            (universe, ddt.date(), g[:6], g[6:], p[6:], isin, float(w))
            for g, p, isin, w in lines[["gvkeyiid", "px_gvkeyiid", "isin", "wgt"]].itertuples(index=False, name=None)
        ]
        cursor = conn.connection.cursor()
        cursor.fast_executemany = True
        cursor.executemany("INSERT INTO dbo.univ_line_d (universe, ddt, gvkey, iid, px_iid, isin, wgt) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        cursor.close()
    switched = int((lines["px_gvkeyiid"] != lines["gvkeyiid"]).sum())
    _log(conn, run_at, ddt, f"lines:{universe}", len(lines), n_stored, len(lines) if write and ok else 0, 0, status, f"px switched {switched}")
    return {"n": len(lines), "stored": n_stored, "status": status}


# 임시표는 파라미터 없이 만든다: 파라미터 배치는 sp_prepexec 안에서 돌아 그 안에서 만든 #표는 끝나면 사라진다
STAGE_DDL = """
DROP TABLE IF EXISTS #stage;
CREATE TABLE #stage (asOfDate date, factorId smallint, gvkey varchar(6) COLLATE DATABASE_DEFAULT, iid varchar(3) COLLATE DATABASE_DEFAULT,
                     universeId smallint, factorValue float, PRIMARY KEY (asOfDate, factorId, gvkey, iid, universeId));
"""
STAGE_SQL = """
INSERT INTO #stage
SELECT CAST(m.asOfDate AS date), m.factorId, m.gvkey, m.iid, m.universeId, m.factorValue
FROM xf_target.dbo.{source} m
JOIN #lines s ON s.gvkey = m.gvkey AND s.iid = m.iid
JOIN xf_target.dbo.ciqAfFactor f ON f.factorId = m.factorId AND f.factorRelativeId = {base}
WHERE m.universeId IN ({universes}) AND m.asOfDate = :d;
"""
KEYS = "t.asOfDate = s.asOfDate AND t.factorId = s.factorId AND t.gvkey = s.gvkey AND t.iid = s.iid AND t.universeId = s.universeId"
CHANGED = "(t.factorValue <> s.factorValue OR (t.factorValue IS NULL AND s.factorValue IS NOT NULL) OR (t.factorValue IS NOT NULL AND s.factorValue IS NULL))"


def _merge_values(conn, source: str, universes: tuple, ddt: pd.Timestamp, write: bool, run_at) -> dict:
    """원천 -> #stage -> 바뀐 값 UPDATE, 없는 행 INSERT. 지우지 않는다. #lines 는 호출 쪽에서 만든다."""
    conn.exec_driver_sql("SET NOCOUNT ON")
    conn.exec_driver_sql(STAGE_DDL)
    conn.execute(sql.text(STAGE_SQL.format(source=source, base=BASE_FACTOR, universes=",".join(map(str, universes)))), {"d": ddt.date()})
    uni = ",".join(map(str, universes))
    n_src = conn.execute(sql.text("SELECT COUNT(*) FROM #stage")).scalar()
    n_stored = conn.execute(sql.text(f"SELECT COUNT(*) FROM dbo.afl_value_d WHERE asOfDate = :d AND universeId IN ({uni})"), {"d": ddt.date()}).scalar()
    n_upd = conn.execute(sql.text(f"SELECT COUNT(*) FROM dbo.afl_value_d t JOIN #stage s ON {KEYS} WHERE {CHANGED}")).scalar()
    n_ins = conn.execute(sql.text(f"SELECT COUNT(*) FROM #stage s WHERE NOT EXISTS (SELECT 1 FROM dbo.afl_value_d t WHERE {KEYS})")).scalar()
    ok = guard_ok(n_src, n_stored)
    status = ("ok" if ok else "skip") if write else "dry"
    if write and ok:
        conn.execute(sql.text(f"UPDATE t SET factorValue = s.factorValue, updated_at = GETDATE() FROM dbo.afl_value_d t JOIN #stage s ON {KEYS} WHERE {CHANGED}"))
        conn.execute(
            sql.text(
                "INSERT INTO dbo.afl_value_d (asOfDate, factorId, gvkey, iid, universeId, factorValue) "
                f"SELECT s.asOfDate, s.factorId, s.gvkey, s.iid, s.universeId, s.factorValue FROM #stage s WHERE NOT EXISTS (SELECT 1 FROM dbo.afl_value_d t WHERE {KEYS})"
            )
        )
    _log(conn, run_at, ddt, f"afl:{source}", n_src, n_stored, n_ins if write and ok else 0, n_upd if write and ok else 0, status)
    return {"src": n_src, "stored": n_stored, "ins": n_ins, "upd": n_upd, "status": status}


def _build_lines(universe: str, ddt: pd.Timestamp, basket: pd.DataFrame, ref: dict, seen: pd.Series, traded: pd.Series) -> tuple[pd.DataFrame, list]:
    """그날 바스켓 -> 라인 [gvkeyiid, px_gvkeyiid, isin, wgt] + 못 붙인 ISIN."""
    b = latest_basket(basket, ddt)
    selected = select_lines(b["isin"], ddt, ref["isin_hist"], ref["primary"], ref["company"], ref["security"], seen)
    lines = b.merge(selected, on="isin").groupby("gvkeyiid", as_index=False).agg(isin=("isin", "first"), wgt=("wgt", "sum"))
    lines["px_gvkeyiid"] = price_lines(lines, ref["isin_hist"], ddt, traded).values
    unmapped = b.loc[~b["isin"].isin(selected["isin"]), "isin"].tolist()
    if b["ddt"].iloc[0] != ddt:
        logger.info("%s %s: basket from %s", universe, ddt.date(), b["ddt"].iloc[0].date())
    return lines, unmapped


def run_direct_load_daily(write: bool = False, end: str | None = None) -> dict:
    """원천에 남아 있는 날짜를 받아 쌓는다. 기본은 쓰기 없이 건수만."""
    end_ts = pd.Timestamp(end).normalize() if end else pd.Timestamp.today().normalize()
    run_at = pd.Timestamp.now().floor("s").to_pydatetime()
    engine = _spg_engine(config.DIRECT_LOAD["target_db"])
    summary = []
    with engine.connect() as conn:
        found = _source_dates(conn, weekdays_back(end_ts, PROBE_DAYS))
        all_src = sorted({d for ds in found.values() for d in ds})
        if not all_src:
            raise RuntimeError(f"no source dates in the last {PROBE_DAYS} weekdays up to {end_ts.date()}")
        stored = _stored_counts(conn, all_src[0])
        todo = sorted({d for source, ds in found.items() for d in dates_to_load(ds, stored[source], REVISIT_DAYS)})
        logger.info("source dates %s ~ %s (%d), to load %d: %s", all_src[0].date(), all_src[-1].date(), len(all_src), len(todo), [str(d.date()) for d in todo])
        if not todo:
            return {"todo": [], "summary": []}
        ref = _reference(conn)
        baskets = {u: _read_basket_days(code, todo[0], todo[-1]) for u, (code, _) in DAILY_UNIVERSES.items()}
        # 거래대금은 바스켓 ISIN 에 라인이 둘 이상인 회사만 (전 세계 다중 상장 회사를 다 읽지 않게)
        active = ref["isin_hist"].loc[_active_on(ref["isin_hist"], todo[-1])]
        active = active[active["isin"].isin(set().union(*(set(b["isin"]) for b in baskets.values())))]
        multi = active.groupby(["gvkey", "isin"])["iid"].transform("size") > 1
        traded = _traded_value(conn, sorted(active.loc[multi, "gvkey"].unique()), todo[-1])
        seen = {}
        for u, (_, table) in DAILY_UNIVERSES.items():
            seen[u] = _seen_lines(conn, u, todo[0])
            if seen[u].empty:
                seen[u], _ = _read_table_history(table, todo[0])  # 첫 실행: 월간 표의 라인 이력으로 연속성 시작
        conn.commit()

        for d in todo:
            union = set()
            for u in DAILY_UNIVERSES:
                lines, unmapped = _build_lines(u, d, baskets[u], ref, seen[u], traded)
                r = _write_lines(conn, u, d, lines, write, run_at)
                conn.commit()
                seen[u] = pd.concat([seen[u].drop(lines["gvkeyiid"], errors="ignore"), pd.Series(d, index=lines["gvkeyiid"])])
                union |= set(lines["gvkeyiid"])
                summary.append({"ddt": d, "part": f"lines:{u}", "n": r["n"], "stored": r["stored"], "status": r["status"], "unmapped": len(unmapped)})
            conn.exec_driver_sql("DROP TABLE IF EXISTS #lines")
            conn.exec_driver_sql("CREATE TABLE #lines (gvkey varchar(6) COLLATE DATABASE_DEFAULT, iid varchar(3) COLLATE DATABASE_DEFAULT, PRIMARY KEY (gvkey, iid))")
            conn.exec_driver_sql("INSERT INTO #lines VALUES (?, ?)", [(g[:6], g[6:]) for g in sorted(union)])
            for source, universes in DAILY_SOURCES:
                if d not in found[source]:
                    continue
                r = _merge_values(conn, source, universes, d, write, run_at)
                conn.commit()
                summary.append({"ddt": d, "part": f"afl:{source}", **r})
                logger.info("%s %s: src %d stored %d ins %d upd %d %s", d.date(), source, r["src"], r["stored"], r["ins"], r["upd"], r["status"])
    engine.dispose()
    if not write:
        logger.info("dry run: nothing written (use --write)")
    return {"todo": todo, "summary": summary}
