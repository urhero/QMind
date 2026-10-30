# -*- coding: utf-8 -*-
"""718708_bf — MSCI China A (718708) 공식 공표 전 구성 백필 (bf = backfill, 2026-10-30).

공식 718708 은 2019-12-02 부터 공표된다. 백테스트는 그 전(2019-11 월말까지)을 718708_bf 로 쓰고, bf 는 2019-11-26 이후
718708 과 같은 방식(대형+중형)으로 맞춘다 — 실제로는 2018-06~2019-11-25 의 A주 편입이 대형주뿐(=718709)이었지만 bf 는 중형주까지 담는다
(사용자 2026-10-30). 구간:
  2009-12 ~ 2019-10 월말: 아래 규칙 시뮬레이션
  2019-11 월말        : 실제 플래그로 고른 대형+중형 구성 = 718708 방식 그대로, 합계 공표가 없어 검증 불가
규칙 (718708 방법 근사):
  후보   = 그 달 133333 CHINA A ONSHORE 구성 (msci_idx_wgt, 월말 N — 2026-10 기준 718708 은 133333 의 완전한 부분집합)
  크기   = EM 표준 지수(A주 제외) 발행사 전체 시총의 하위 5% 값을 기준(cut)으로, 회사 전체 시총(같은 발행사 상장 라인 합) >= A*cut,
           이미 편입된 종목은 A*B*cut (MSCI 버퍼 흉내), FIF 반영 시총 >= G*cut
  시점   = 2·5·8·11월 말 정기변경에서만 편입·편출 (그 사이는 133333 에서 빠진 종목만 제외)
  비중   = 가격 x 다음날 주식수 x FIF / 환율 (std_IIF 미적용 — 718708 과 같음)
  한계   = Stock Connect 거래 가능 여부(2014-11 상해, 2016-12 심천 개시)·외국인 지분 여유는 이력이 없어 반영 못 함
  식별자 = 2019-10 까지 행은 본토(CNY) 라인 (코드·가격통화), 2019-11 과 공식 718708 은 홍콩(CNH) 라인 -> 이어 붙일 때는 ISIN 으로
보정 (calib): 2009-12 부터 연속으로 돌리고 실제 718708(대·중형) 이 있는 2019-12~2026-09 81개월과 비교.
  채택 A=2.0, B=0.67, G=0.25, cut=하위 5%: 비중 겹침 평균 93.0%(최저 88.7%), 월 수익 차 연 TE 0.95%, 종목 467 vs 470
  (참고: 133333 그대로 쓰면 겹침 87.4%, TE 1.37%). 이웃 설정도 0.92~0.93 — 고원.
저장 (write): MSCIDB.dbo.msci_idx_wgt 에 msci_index_code '718708_bf', freq 'M' (2009-12 ~ 2019-11 월말, 당일 T·다음날 N,
  공식 718708 과 같은 컬럼). 검증 로그(msci_idx_wgt_check)는 없다 — 공표값이 없으므로.
  이어 붙인 이력 = View_msci_idx_wgt_718708_hist (src '718708_bf' -> 2019-12 부터 '718708').
사용: python research/china_a_backfill.py calib|write      (DB 계정은 .env, MSCIDB 서버는 아래 상수)
"""
import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyodbc
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
ENV = dotenv_values(ROOT / ".env")
SERVER, DB = "10.206.1.119,9433", "MSCIDB"
START, CAL0, CAL1, SIM_END, FLAGS_DATE = "20091201", "20191231", "20260930", "20191031", "20191129"
BF = "718708_bf"
A, B, G = 2.0, 0.67, 0.25
REVIEW_MONTHS = ("02", "05", "08", "11")


def connect():
    drv = ENV.get("ODBC_NAME") or "ODBC Driver 17 for SQL Server"
    return pyodbc.connect(f"DRIVER={{{drv}}};SERVER={SERVER};DATABASE={DB};UID={ENV['USER_NAME']};PWD={ENV['USER_PWD']}",
                          autocommit=True, timeout=30)


def q(c, sql, *p, cols):
    cur = c.cursor()
    cur.execute(sql, *p)
    return pd.DataFrame([tuple(r) for r in cur.fetchall()], columns=cols)


def load(c):
    """월별 후보(133333) + 특성 + EM 크기 기준 + 정답(718708)."""
    cand = q(c, """SELECT calc_date, msci_security_code, isin, bb_ticker FROM msci_idx_wgt
                   WHERE msci_index_code='133333' AND freq='M' AND basis='N' AND calc_date BETWEEN ? AND ?""", START, CAL1,
             cols=["d", "code", "isin", "tk"])
    truth = q(c, "SELECT calc_date, isin, wgt FROM msci_idx_wgt WHERE msci_index_code='718708' AND freq='M' AND basis='N'",
              cols=["d", "isin", "w_true"])
    feat, cut = [], {}
    for d in sorted(cand.d.unique()):
        sr = q(c, """
            WITH fx AS (SELECT ISO_currency_symbol, MIN(spot_fx_eod00d) fx FROM M_DAILY_TRACKER_XRATES WHERE calc_date=? GROUP BY ISO_currency_symbol),
            s AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY msci_security_code ORDER BY umarket) rn FROM M_SECURITY_REPORT WHERE calc_date=?)
            SELECT s.msci_security_code, s.msci_issuer_code, s.price_ISO_currency_symbol, s.EM_universe_flag, s.family_std_flag_next_day,
                   s.foreign_inc_factor_next_day,
                   s.price * s.eod_number_of_shares_next_day / CASE WHEN s.price_ISO_currency_symbol='USD' THEN 1.0 ELSE fx.fx END
            FROM s LEFT JOIN fx ON fx.ISO_currency_symbol = s.price_ISO_currency_symbol
            WHERE s.rn = 1 AND s.price_ISO_currency_symbol <> 'CNH'""", d, d,   # CNH 라인은 A주 중복이라 회사 시총에서 뺀다
            cols=["code", "issuer", "ccy", "em", "f", "fif", "cap"])
        co = sr.groupby("issuer").cap.sum()
        em = co.reindex(sr[(sr.em == "1") & (sr.f == "1") & (sr.ccy != "CNY")].issuer.unique()).dropna()
        cut[d] = em.quantile(0.05)
        x = sr[sr.ccy == "CNY"][["code", "fif", "cap"]].assign(d=d, co_cap=lambda t: t.code.map(sr.set_index("code").issuer).map(co))
        feat.append(x)
    df = cand.merge(pd.concat(feat), on=["d", "code"], how="left").merge(truth, on=["d", "isin"], how="left")
    df["ff"] = df.cap * df.fif
    return df, pd.Series(cut)


def simulate(df, cut, a=A, b=B, g=G):
    """월별 시뮬레이션 구성 {calc_date: set(code)} — 2009-12 부터 연속 (정기변경 때만 편입·편출, 버퍼)."""
    S, out = set(), {}
    for d, x in df.groupby("d", sort=True):
        if d[4:6] in REVIEW_MONTHS or not S:
            thr = np.where(x.code.isin(S), a * b, a) * cut[d]
            S = set(x.code[(x.co_cap.to_numpy() >= thr) & (x.ff.to_numpy() >= g * cut[d])])
        else:
            S &= set(x.code)
        out[d] = S
    return out


def score(df, out, ret=None):
    rows = []
    for d, x in df[(df.d >= CAL0) & (df.d <= CAL1)].groupby("d"):
        s, t = x.code.isin(out[d]).to_numpy(), x.w_true.notna().to_numpy()
        ws = np.where(s, x.ff, 0.0); ws /= ws.sum()
        wt = np.nan_to_num(x.w_true.to_numpy()); wt /= wt.sum()
        row = dict(d=d, n_sim=s.sum(), n_true=t.sum(), recall=(s & t).sum() / t.sum(), prec=(s & t).sum() / s.sum(), overlap=np.minimum(ws, wt).sum())
        if ret is not None:
            r = np.nan_to_num(ret.reindex(pd.MultiIndex.from_arrays([pd.Series(pd.to_datetime([d] * len(x)) + pd.offsets.MonthEnd(0)), x["isin"].astype(str)])).to_numpy())
            row["rdiff"] = (ws * r).sum() - (wt * r).sum()
        rows.append(row)
    z = pd.DataFrame(rows)
    res = dict(months=len(z), n_sim=z.n_sim.mean(), n_true=z.n_true.mean(), recall=z.recall.mean(), prec=z.prec.mean(),
               overlap=z.overlap.mean(), overlap_min=z.overlap.min())
    if ret is not None:
        res["te_pct"] = z.rdiff.std() * np.sqrt(12) * 100
    return res


def write(c, df, out):
    """msci_idx_wgt 에 '718708_bf' 월말 행 (공식 718708 과 같은 컬럼, 당일 T·다음날 N).
    구성: N = 그 달 시뮬레이션 구성, T = 그 달 동안 유지된 구성 (= 전월 N; 첫 달 2009-12 은 T 없음).
    2019-11 월말만 실제 플래그 (T = 당일 std flag, N = 다음날 std flag, CNH 라인). 값은 M_SECURITY_REPORT 그 날 행 그대로,
    시총 = 718708 규칙 (T: 가격 x 종가 주식수 x FIF / 환율, N: 다음날 시총 x 다음날 FIF, std_IIF 미적용)."""
    months = sorted(d for d in out if d <= SIM_END)
    mem = [(d, code, "N") for d in months for code in out[d]]
    mem += [(d, code, "T") for p, d in zip(months, months[1:]) for code in out[p]]
    cur = c.cursor()
    cur.execute("CREATE TABLE #mem (calc_date varchar(8), msci_security_code varchar(7), basis char(1))")
    cur.fast_executemany = True
    cur.executemany("INSERT INTO #mem VALUES (?, ?, ?)", mem)
    cur.execute("""
        INSERT INTO #mem
        SELECT calc_date, msci_security_code, 'N' FROM M_SECURITY_REPORT
        WHERE calc_date = ? AND ISO_country_symbol_next_day = 'CN' AND family_std_flag_next_day = '1' AND price_ISO_ccy_symbol_next_day = 'CNH'
        UNION
        SELECT calc_date, msci_security_code, 'T' FROM M_SECURITY_REPORT
        WHERE calc_date = ? AND ISO_country_symbol = 'CN' AND family_std_flag = '1' AND price_ISO_currency_symbol = 'CNH'""", FLAGS_DATE, FLAGS_DATE)
    cur.execute("DELETE FROM dbo.msci_idx_wgt WHERE msci_index_code = ?", BF)
    cur.execute("""
        WITH fx AS (SELECT calc_date, ISO_currency_symbol, MIN(spot_fx_eod00d) fx FROM M_DAILY_TRACKER_XRATES
                    WHERE calc_date IN (SELECT calc_date FROM #mem) GROUP BY calc_date, ISO_currency_symbol),
        s AS (SELECT s.*, ROW_NUMBER() OVER (PARTITION BY s.calc_date, s.msci_security_code ORDER BY s.umarket) rn
              FROM M_SECURITY_REPORT s WHERE s.calc_date IN (SELECT calc_date FROM #mem)),
        x AS (SELECT m.basis, s.*, v.fxv,
                     CASE m.basis WHEN 'T' THEN s.price * s.closing_number_of_shares * s.foreign_inclusion_factor / v.fxv
                          ELSE COALESCE(s.initial_mkt_cap_usd_next_day, s.price * s.eod_number_of_shares_next_day / v.fxv) * s.foreign_inc_factor_next_day END cap
              FROM #mem m
              JOIN s ON s.calc_date = m.calc_date AND s.msci_security_code = m.msci_security_code AND s.rn = 1
              LEFT JOIN fx ON fx.calc_date = s.calc_date AND fx.ISO_currency_symbol = s.price_ISO_currency_symbol
              CROSS APPLY (SELECT CASE WHEN s.price_ISO_currency_symbol = 'USD' THEN 1.0 ELSE fx.fx END fxv) v)
        INSERT INTO dbo.msci_idx_wgt (msci_index_code, freq, basis, calc_date, msci_security_code, msci_issuer_code, security_name, isin, bb_ticker,
               RIC, share_class, share_type, sub_industry, sub_industry_next_day, ISO_country_symbol, ISO_country_symbol_next_day,
               price_ISO_currency_symbol, price_ISO_ccy_symbol_next_day, std_IIF, std_IIF_next_day,
               foreign_inclusion_factor, foreign_inc_factor_next_day, domestic_inclusion_factor, domestic_inc_factor_next_day,
               family_std_flag, family_std_flag_next_day, family_std_dom_flag, family_std_dom_flag_next_day,
               initial_mkt_cap_usd, initial_mkt_cap_usd_next_day, price, closing_number_of_shares,
               eod_number_of_shares_today, eod_number_of_shares_next_day, price_adjustment_factor, prelim_price_adj_fact_nxt_day,
               fx, mkt_cap_usd, wgt)
        SELECT ?, 'M', basis, calc_date, msci_security_code, msci_issuer_code, security_name,
               -- 신규 상장 직후 월말 스냅샷엔 ISIN 이 빈 경우가 있다 (19종목 208행) -> 같은 MSCI 코드의 가장 가까운 날짜 ISIN
               COALESCE(isin, (SELECT TOP 1 o.isin FROM M_SECURITY_REPORT o WHERE o.msci_security_code = x.msci_security_code AND o.isin IS NOT NULL
                               ORDER BY ABS(CAST(o.calc_date AS int) - CAST(x.calc_date AS int)))),
               REPLACE(REPLACE(REPLACE(REPLACE(bb_ticker, ' CG ', ' CH '), ' CS ', ' CH '), ' C1 ', ' CH '), ' C2 ', ' CH '),
               RIC, share_class, share_type, sub_industry, sub_industry_next_day, ISO_country_symbol, ISO_country_symbol_next_day,
               price_ISO_currency_symbol, price_ISO_ccy_symbol_next_day, std_IIF, std_IIF_next_day,
               foreign_inclusion_factor, foreign_inc_factor_next_day, domestic_inclusion_factor, domestic_inc_factor_next_day,
               family_std_flag, family_std_flag_next_day, family_std_dom_flag, family_std_dom_flag_next_day,
               initial_mkt_cap_usd, initial_mkt_cap_usd_next_day, price, closing_number_of_shares,
               eod_number_of_shares_today, eod_number_of_shares_next_day, price_adjustment_factor, prelim_price_adj_fact_nxt_day,
               fxv, cap, cap / SUM(cap) OVER (PARTITION BY calc_date, basis)
        FROM x WHERE cap > 0""", BF)   # cap 0 = 다음날 FIF 0 (외국인 한도 소진, 예 2014-12 GREE) -> 비중 0 이라 뺀다
    n = cur.rowcount
    cur.execute("DROP TABLE #mem")
    print("membership", len(mem), "+ flags month | written", n, "rows")


def monthly_returns():
    """MXCN1A mreturn (다음 달 수익률, gvkeyiid) 를 (월말, isin) 으로."""
    d = ROOT / "data" / "MXCN1A"
    fr = pd.concat(pd.read_parquet(f, columns=["ddt", "gvkeyiid", "isin"]).drop_duplicates(["ddt", "gvkeyiid"]) for f in sorted(d.glob("factor_[0-9]*.parquet")))
    mr = pd.read_parquet(d / "mreturn.parquet")
    for t in (fr, mr):
        t["ddt"] = pd.to_datetime(t.ddt) + pd.offsets.MonthEnd(0)
    return fr.merge(mr, on=["ddt", "gvkeyiid"]).drop_duplicates(["ddt", "isin"]).set_index(["ddt", "isin"]).M_RETURN


def main(cmd):
    c = connect()
    df, cut = load(c)
    if cmd == "calib":
        ret = monthly_returns()
        res = [dict(a=a, b=b, g=g, **score(df, simulate(df, cut, a, b, g), ret))
               for a, b, g in itertools.product([1.5, 1.8, 2.0, 2.5, 3.0], [0.5, 0.67, 1.0], [0.0, 0.25, 0.5])]
        print(pd.DataFrame(res).sort_values("overlap", ascending=False).head(10).round(3).to_string(index=False))
        print("133333 as-is:", {k: round(v, 3) for k, v in score(df, {d: set(x.code) for d, x in df.groupby("d")}, ret).items()})
    elif cmd == "write":
        write(c, df, simulate(df, cut))
    else:
        raise SystemExit("calib | write")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "calib")
