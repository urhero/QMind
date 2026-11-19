/* ============================================================================
   일별 적재 표와 가격 view (kb_global) - 2026-11-06
   - 적재: python main.py direct-load-daily --write (db/daily_direct.py), 매일 14:30 작업 스케줄러
   - 읽기: db/daily_read.py (QMind·리스크 모델 공용). 규칙 근거는 research.md §2.5
   - univ_line_d : 날짜별 지수 구성 라인. 팩터는 iid 라인, 가격은 px_iid 라인 (같은 회사 같은 ISIN 의 거래 많은 라인)
   - afl_value_d : S&P AFL 일별 팩터값 (기본 팩터 factorRelativeId=1001, 세 지수 라인만).
                   원천(ciqAfValueDailyNA/Intl)은 약 22영업일만 남기고 지우므로 여기 쌓인 것이 유일한 이력이다.
                   행을 지우지 않는다: 원천에서 사라진 날짜·라인은 마지막 값으로 남고, 원천이 고친 값만 덮는다.
   - v_px_d      : 가격·거래량·총수익 view. 원천이 전 이력을 보관하므로 복사하지 않는다.
                   시가·고가·저가·거래량·VWAP = CIQ ciqPriceEquity 무수정 환산 (없으면 sec_dprc), 종가 = sec_dprc prccd.
                   수정가 = px_close / split_adj, 총수익지수 tr_idx = px_close / split_adj x tr_factor (배당 세전 현금, 스핀오프 포함).
                   split_adj 는 현재 = 1 기준이라 분할 때 과거 행이 바뀐다. tr_factor 는 앞으로만 쌓인다.
                   sec_dprc 는 휴장일에도 직전 가격 행(prcstd=5)을 둔다: 수익률을 낼 때는 traded = 1 만 쓴다.
                   gvkey, iid, ddt 로 걸러 읽을 것 (gvkeyiid 는 계산 칸이라 인덱스를 못 탄다)
   ============================================================================ */
USE [kb_global]
GO

IF OBJECT_ID('dbo.univ_line_d') IS NULL
CREATE TABLE dbo.univ_line_d (
    universe   varchar(10) NOT NULL,   -- MXWO / MXCN1A / 718708
    ddt        date NOT NULL,          -- 바스켓 날짜 = 팩터 asOfDate
    gvkey      varchar(6) NOT NULL,
    iid        varchar(3) NOT NULL,    -- 팩터 라인 (월간 clarifi_*_afl 과 같은 선택 규칙)
    px_iid     varchar(3) NOT NULL,    -- 가격 라인 (보통 iid 와 같다)
    isin       varchar(12) NULL,       -- 바스켓 ISIN
    wgt        float NULL,             -- 바스켓 비중
    updated_at datetime NOT NULL CONSTRAINT DF_univ_line_d_updated_at DEFAULT GETDATE(),
    CONSTRAINT PK_univ_line_d PRIMARY KEY (universe, ddt, gvkey, iid)
);
GO

IF OBJECT_ID('dbo.afl_value_d') IS NULL
CREATE TABLE dbo.afl_value_d (
    asOfDate    date NOT NULL,
    factorId    smallint NOT NULL,     -- xf_target.dbo.ciqAfFactor
    gvkey       varchar(6) NOT NULL,
    iid         varchar(3) NOT NULL,
    universeId  smallint NOT NULL,     -- 원천 AFL 유니버스 (2109~2114)
    factorValue float NULL,
    updated_at  datetime NOT NULL CONSTRAINT DF_afl_value_d_updated_at DEFAULT GETDATE(),
    CONSTRAINT PK_afl_value_d PRIMARY KEY (asOfDate, factorId, gvkey, iid, universeId)
);
GO

IF OBJECT_ID('dbo.direct_load_d_log') IS NULL
CREATE TABLE dbo.direct_load_d_log (
    run_at   datetime NOT NULL,
    ddt      date NOT NULL,
    part     varchar(30) NOT NULL,     -- lines:<universe> / afl:<원천 표>
    n_src    int NOT NULL,             -- 이번에 원천에서 만든 행
    n_stored int NOT NULL,             -- 적재 전 저장 행 (같은 날짜·범위)
    n_ins    int NOT NULL,
    n_upd    int NOT NULL,
    status   varchar(10) NOT NULL,     -- ok / skip (가드) / dry
    note     nvarchar(400) NULL,
    CONSTRAINT PK_direct_load_d_log PRIMARY KEY (run_at, ddt, part)
);
GO

IF OBJECT_ID('dbo.v_px_d') IS NOT NULL DROP VIEW dbo.v_px_d;
GO
CREATE VIEW dbo.v_px_d AS
SELECT
    d.gvkey,
    d.iid,
    d.gvkey + d.iid AS gvkeyiid,
    CAST(d.datadate AS date) AS ddt,
    d.curcdd AS ccy,
    COALESCE(c.priceOpen * c.adjustmentFactor, d.prcod) AS px_open,
    COALESCE(c.priceHigh * c.adjustmentFactor, d.prchd) AS px_high,
    COALESCE(c.priceLow * c.adjustmentFactor, d.prcld) AS px_low,
    d.prccd AS px_close,
    COALESCE(c.volume / NULLIF(c.adjustmentFactor, 0), d.cshtrd) AS volume,
    c.VWAP * c.adjustmentFactor AS vwap,
    d.ajexdi AS split_adj,
    ISNULL(t.trfd, 1) AS tr_factor,
    d.prccd / NULLIF(d.ajexdi, 0) * ISNULL(t.trfd, 1) AS tr_idx,
    fx.usd_per_local,
    d.prcstd AS px_status,
    CASE WHEN d.prcstd = 5 THEN 0 ELSE 1 END AS traded,   -- 5 = 휴장일 직전 가격 이월 (실측: CIQ 행·거래량 없음과 일치)
    CASE WHEN c.adjustmentFactor IS NULL THEN 0 ELSE 1 END AS has_ciq
FROM xf_target.dbo.sec_dprc d
OUTER APPLY (
    SELECT TOP 1 p.priceOpen, p.priceHigh, p.priceLow, p.volume, p.VWAP, p.adjustmentFactor
    FROM xf_target.dbo.ciqGvkeyIID g
    JOIN xf_target.dbo.ciqPriceEquity p ON p.tradingItemId = g.objectId AND p.pricingDate = d.datadate
    WHERE g.gvkey = d.gvkey AND g.iid = d.iid
      AND d.datadate >= ISNULL(g.symbolStartDate, '1900-01-01') AND d.datadate <= ISNULL(g.symbolEndDate, '2100-01-01')
    ORDER BY g.activeFlag DESC
) c
OUTER APPLY (
    SELECT TOP 1 s.trfd FROM xf_target.dbo.sec_dtrt s
    WHERE s.gvkey = d.gvkey AND s.iid = d.iid AND s.datadate <= d.datadate
    ORDER BY s.datadate DESC
) t
OUTER APPLY (
    SELECT TOP 1 u.exratd / NULLIF(l.exratd, 0) AS usd_per_local   -- exrt_dly 는 GBP 1 단위당 환율
    FROM xf_target.dbo.exrt_dly l
    JOIN xf_target.dbo.exrt_dly u ON u.tocurd = 'USD' AND u.datadate = l.datadate
    WHERE l.tocurd = d.curcdd AND l.datadate <= d.datadate AND l.datadate >= DATEADD(day, -7, d.datadate)
    ORDER BY l.datadate DESC
) fx
WHERE d.prccd IS NOT NULL;
GO
