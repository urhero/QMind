from __future__ import annotations

"""일별 데이터 읽기 (QMind·리스크 모델 공용, 2026-11-06). 적재는 db/daily_direct.py, 표 정의는 db/sql/daily_tables.sql.

    from db.daily_read import universe_lines, daily_factors, daily_prices
    lines = universe_lines("MXWO", "2026-10-01", "2026-10-31")      # 날짜별 구성 라인
    fac = daily_factors("MXWO", "2026-10-01", "2026-10-31", ["PM12M1M"])  # 날짜 x 라인 x 팩터 (long)
    px = daily_prices(lines["px_gvkeyiid"].unique(), "2026-01-01", "2026-10-31")  # 가격·총수익률

키는 월간 파이프라인과 같은 gvkeyiid 다. 팩터는 gvkeyiid(팩터 라인), 가격은 px_gvkeyiid(가격 라인) 로 붙인다.
수익률은 tr_idx(배당 세전 현금 재투자, 스핀오프 포함) 비율이고 달러 수익률은 같은 날 환율을 곱한 비율이다.
"""

from typing import Iterable

import pandas as pd
import sqlalchemy as sql

import config
from db.factor_direct import _spg_engine

PRICE_COLUMNS = ["gvkeyiid", "ddt", "ccy", "px_open", "px_high", "px_low", "px_close", "volume", "vwap", "split_adj", "tr_factor", "tr_idx", "usd_per_local", "traded", "has_ciq"]
WARMUP_DAYS = 10  # 첫날 수익률을 위해 앞쪽 며칠 더 읽는다


def add_returns(px: pd.DataFrame) -> pd.DataFrame:
    """라인별 날짜순 총수익률 ret_loc (현지통화), ret_usd (달러). 라인의 첫 행은 NaN."""
    out = px.sort_values(["gvkeyiid", "ddt"], kind="stable").reset_index(drop=True)
    g = out.groupby("gvkeyiid", sort=False)
    out["ret_loc"] = g["tr_idx"].pct_change(fill_method=None)
    out["ret_usd"] = (out["tr_idx"] * out["usd_per_local"]).groupby(out["gvkeyiid"], sort=False).pct_change(fill_method=None)
    return out


def _engine():
    return _spg_engine(config.DIRECT_LOAD["target_db"])


def universe_lines(universe: str, start: str, end: str) -> pd.DataFrame:
    """[ddt, gvkeyiid, px_gvkeyiid, isin, wgt]. universe = MXWO / MXCN1A / 718708."""
    engine = _engine()
    with engine.connect() as conn:
        df = pd.read_sql_query(
            sql.text(
                "SELECT ddt, gvkey + iid gvkeyiid, gvkey + px_iid px_gvkeyiid, isin, wgt FROM dbo.univ_line_d "
                "WHERE universe = :u AND ddt BETWEEN :a AND :b ORDER BY ddt, gvkey, iid"
            ),
            conn,
            params={"u": universe, "a": start, "b": end},
        )
    engine.dispose()
    df["ddt"] = pd.to_datetime(df["ddt"])
    return df


def daily_factors(universe: str, start: str, end: str, factors: Iterable[str] | None = None) -> pd.DataFrame:
    """[ddt, gvkeyiid, factor, val] (factor = ciqAfFactor 약어). 그날 지수 구성 라인만."""
    engine = _engine()
    with engine.connect() as conn:
        wanted = ""
        if factors is not None:
            conn.exec_driver_sql("DROP TABLE IF EXISTS #f")
            conn.exec_driver_sql("CREATE TABLE #f (abbr varchar(100) COLLATE DATABASE_DEFAULT PRIMARY KEY)")
            conn.exec_driver_sql("INSERT INTO #f VALUES (?)", [(f,) for f in sorted(set(factors))])
            wanted = "JOIN #f ON #f.abbr = f.factorAbbreviation"
        df = pd.read_sql_query(
            sql.text(
                "SELECT l.ddt, l.gvkey + l.iid gvkeyiid, f.factorAbbreviation factor, v.factorValue val "
                "FROM dbo.univ_line_d l JOIN dbo.afl_value_d v ON v.asOfDate = l.ddt AND v.gvkey = l.gvkey AND v.iid = l.iid "
                f"JOIN xf_target.dbo.ciqAfFactor f ON f.factorId = v.factorId {wanted} "
                "WHERE l.universe = :u AND l.ddt BETWEEN :a AND :b"
            ),
            conn,
            params={"u": universe, "a": start, "b": end},
        )
    engine.dispose()
    df["ddt"] = pd.to_datetime(df["ddt"])
    return df


def daily_prices(lines: Iterable[str], start: str, end: str, traded_only: bool = True) -> pd.DataFrame:
    """가격 라인(gvkeyiid) 들의 일별 가격·거래량·총수익 + ret_loc, ret_usd. 무수정 현지통화 (영국 파운드).

    traded_only=True (기본): 휴장일 이월 행을 빼서 수익률이 거래일 사이로 계산된다 (휴장일 0 수익률이 생기지 않음).
    달력 정렬이 필요하면 False 로 읽거나 결과를 reindex 한다.
    """
    ids = sorted(set(lines))
    a = (pd.Timestamp(start) - pd.Timedelta(days=WARMUP_DAYS)).date()
    engine = _engine()
    with engine.connect() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS #l")
        conn.exec_driver_sql("CREATE TABLE #l (gvkey varchar(6) COLLATE DATABASE_DEFAULT, iid varchar(3) COLLATE DATABASE_DEFAULT, PRIMARY KEY (gvkey, iid))")
        conn.exec_driver_sql("INSERT INTO #l VALUES (?, ?)", [(g[:6], g[6:]) for g in ids])
        df = pd.read_sql_query(
            sql.text(
                f"SELECT {', '.join('v.' + c for c in PRICE_COLUMNS)} FROM #l JOIN dbo.v_px_d v ON v.gvkey = #l.gvkey AND v.iid = #l.iid "
                f"WHERE v.ddt BETWEEN :a AND :b{' AND v.traded = 1' if traded_only else ''}"
            ),
            conn,
            params={"a": a, "b": end},
        )
    engine.dispose()
    df["ddt"] = pd.to_datetime(df["ddt"])
    out = add_returns(df)
    return out[out["ddt"] >= pd.Timestamp(start)].reset_index(drop=True)
