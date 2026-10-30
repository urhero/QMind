/* ============================================================================
   MSCI 지수 구성·비중 저장 (검증 통과분만) — 2026-10-28, v3 2026-10-29 (당일/다음날 기준 분리)
   - 대상: msci_index_code 하나 (예: 990100 = MSCI World = MXWO). 일별('D') 과 월말('M') 을 같은 프로시저로.
   - 비중 기준 basis 두 가지 (같은 테이블, 키에 포함):
       'T' = 당일 종가 구성·비중 (calc_date 장 마감 시점, 정기변경 반영 전)
       'N' = 다음날 구성·비중 (calc_date 장 마감 후 반영 = 익영업일 시가 기준, *_next_day 필드)
     정기변경일(2·5·8·11월 말 영업일) 에는 둘의 구성이 다르다. 평소에는 같은 종목, 비중은 다음날 주식수·유동비율 차이만.
     QMind bmwgt / ClariFI MXWO_WGT / 기존 msci_bbu_csv 는 'N' 관례 (2026 정기변경 월말 4개로 확인: 공통 종목 비중 차이 0.000%p).
   - 컬럼: 기존 msci_bbu_csv 수준 (당일·다음날 쌍) + msci_issuer_code, fx, 가격조정계수. 두 basis 행 모두 원천 값을 그대로 담고
     mkt_cap_usd·wgt 만 basis 에 따라 다르다. sedol·cusip 은 원천 100% NULL 이라 뺐고, ddt·index_name·수익률은 원천 조인으로 대신한다.
   - 지수별 규칙 (그 지수의 최신 index_name 으로 판별 — 133333 은 2018-02 까지 이름이 'CHINA A' 였다):
       CHINA A ONSHORE% (133333): domestic flag / DIF (기존 Store_MSCI_wgt_new 와 같음)
       CHINA A (718708): 홍콩 Stock Connect 라인(가격통화 CNH)만, family_std_flag / FIF, std_IIF 는 쓰지 않음
                         (EM·China 지수의 A주 부분편입 0.2 는 China A 지수엔 적용 안 됨 — 같은 종목 ADR 6개도 제외).
                         2026-10-28 bbg 구성 파일 415종목 일치, 비중 차 최대 5e-7%p.
       그 외: family_std_flag / FIF / std_IIF.
     bb_ticker 의 ' CG '/' CS '/' C1 '/' C2 ' -> ' CH ' 치환은 기존과 같다.
   - 소속 국가 (기준별):
       T: 당일 국가(ISO_country_symbol) 가 당일 국가목록에 있음
       N: 다음날 국가(ISO_country_symbol_next_day) 가 다음 영업일 국가목록(일별 COUNTRIES_WEIGHT, 7일 이내) 에 있음.
          다음 영업일 목록이 아직 없으면(최신일) 당일 목록 -> 국가 편입·제외가 있는 정기변경일은 그날 실패하고 다음 날 재처리 때 통과.
          (실패 사례: 2017-05 PK, 2018-06-26 OLD MUTUAL GB->ZA, 2019-05 AR·SA, 2020-11 KW 편입, 2021-11 AR·PK 제외)
       당일 국가목록 = 같은 주기 (M_)COUNTRIES_WEIGHT -> 없으면 일별 COUNTRIES_WEIGHT 같은 날 (월간 2017-04~2020-03, 2021 공백 보완)
       목록이 없으면: 지수 자체 국가(단일국가 지수), 그것도 없으면 지수 DM/EM 플래그
       (MSCI Short Implementation Guide 2018 대응표; ACWI 는 플래그가 DM=0/EM=1 이라 region_code='ACWI' 면 DM 또는 EM).
       플래그(DM/EM_universe_flag) 에는 다음날 값이 없어 N 이 국가 편입을 놓친다 (2014-05 QA·AE 가 다음날 EM 인데 당일 플래그는 FM).
       그래서 N 이 공표 다음날 시총과 안 맞으면 다음 기간(같은 주기 다음 날짜, 40일 이내) 플래그로 본 국가로 한 번 더 골라 보고,
       그게 맞을 때만 저장한다 (check.cty_alt = 1; 적용 4건 = EM·ACWI 2009-05 AR·PK 제외, 2014-05 QA·AE 편입, 모두 공표값과 오차 0).
       처음부터 다음 기간 플래그를 쓰면 다음 달 중간에 바뀐 국가
       (2010-05 IL, 2013-11 GR·MA) 가 앞당겨져 틀리므로 당일 플래그가 우선. 공표 다음날 시총이 없으면(월간 2008-09 이전) 보완하지 않는다.
   - 검증 (모두 통과해야 저장, 결과는 기준·날짜마다 msci_idx_wgt_check 에 남김):
       종목 수 허용 차이 @n_tol: 일별 0, 월간 10 (2026-10-29 사용자 결정 — 월간은 시총이 1bp 안에 맞으면 10종목 이내 차이는 저장)
       T: 종목 수 = INDEX_REPORT.count_of_securities, 비조정 시총 합 = unadj_mkt_cap_eod00d_usd x 1e6 (|비율-1| <= @tol),
          환율 결측 0.  비조정 시총 = price x closing_number_of_shares x inclusion_factor x std_IIF / fx (가이드의 검증식)
       N: 다음날 시총 합 = initial_mkt_cap_eod00d_usd_nd x 1e6 (공표 있을 때; 월간은 2008-09 이후 대부분),
          종목 수 = 다음 영업일 count_of_securities (일별 INDEX_REPORT 에 있을 때, 2016-07~), 환율 결측 0.
          다음날 시총 공표가 없으면 같은 날 T 통과를 요구 (구성 규칙이 그날 맞았다는 근거).
       다음날 시총 = initial_mkt_cap_usd_next_day (없으면 price x eod_number_of_shares_next_day / fx — 2022~ 실값과 1bp 이내 100%)
                     x inc_factor_next_day x std_IIF_next_day.  initial_mkt_cap_usd_next_day 는 유동비율 곱하기 전 값.
   - 반영 방식: 통과한 (기준, 날짜) 만, 저장분과 행 단위로 비교해 새 종목 INSERT / 빠진 종목 DELETE / 값이 바뀐 종목만 UPDATE.
     값이 같은 행은 손대지 않으므로 udt = 그 행이 마지막으로 실제 바뀐 시각. wgt 는 합계 순서 오차(~1e-17) 때문에 1e-12 초과만 변경으로 본다.
   - 검증 실패한 날짜는 기존 저장분을 건드리지 않는다 (벤더 정정 중 일시 불일치로 좋은 데이터를 잃지 않게). 로그만 실패로 남는다.
   - @dry_run = 1: 저장하지 않고 검증 결과(#chk) 만 돌려준다.
   ============================================================================ */
USE [MSCIDB]
GO

IF OBJECT_ID('dbo.msci_idx_wgt') IS NULL
CREATE TABLE dbo.msci_idx_wgt (
    msci_index_code     varchar(6)  NOT NULL,
    freq                char(1)     NOT NULL,          -- 'D' = SECURITY_REPORT, 'M' = M_SECURITY_REPORT
    basis               char(1)     NOT NULL,          -- 'T' = 당일 종가 구성, 'N' = 다음날(장 마감 후 반영) 구성
    calc_date           varchar(8)  NOT NULL,
    msci_security_code  varchar(7)  NOT NULL,
    msci_issuer_code    varchar(5)  NULL,              -- 같은 회사의 여러 주식 종류를 묶는 키
    security_name       varchar(25) NULL,
    isin                varchar(12) NULL,
    bb_ticker           varchar(25) NULL,              -- 중국 거래소 코드 CH 로 통일
    RIC                 varchar(20) NULL,
    share_class         varchar(20) NULL,
    share_type          varchar(15) NULL,
    sub_industry        varchar(9)  NULL,              -- GICS 8자리 (당일)
    sub_industry_next_day varchar(9) NULL,
    ISO_country_symbol  varchar(2)  NULL,
    ISO_country_symbol_next_day varchar(2) NULL,
    price_ISO_currency_symbol varchar(3) NULL,
    price_ISO_ccy_symbol_next_day varchar(3) NULL,
    std_IIF             float NULL,
    std_IIF_next_day    float NULL,
    foreign_inclusion_factor    float NULL,
    foreign_inc_factor_next_day float NULL,
    domestic_inclusion_factor   float NULL,
    domestic_inc_factor_next_day float NULL,
    family_std_flag     varchar(1) NULL,
    family_std_flag_next_day varchar(1) NULL,
    family_std_dom_flag varchar(1) NULL,
    family_std_dom_flag_next_day varchar(1) NULL,
    initial_mkt_cap_usd float NULL,                    -- 유동비율 곱하기 전
    initial_mkt_cap_usd_next_day float NULL,           -- 월간 2020 이전은 NULL (mkt_cap_usd 는 복원식으로 채움)
    price               float NULL,                    -- 현지통화 종가
    closing_number_of_shares float NULL,
    eod_number_of_shares_today float NULL,
    eod_number_of_shares_next_day float NULL,
    price_adjustment_factor float NULL,
    prelim_price_adj_fact_nxt_day float NULL,
    fx                  float NOT NULL,                -- 계산에 쓴 환율 (현지통화 / USD, USD = 1)
    mkt_cap_usd         float NOT NULL,                -- 유동 시총 (basis 기준, USD) = 비중 분자
    wgt                 float NOT NULL,                -- (지수, 주기, 기준, 날짜) 합 = 1
    udt                 datetime NOT NULL DEFAULT GETDATE(),   -- 행이 마지막으로 실제 바뀐 시각
    CONSTRAINT PK_msci_idx_wgt PRIMARY KEY (msci_index_code, freq, basis, calc_date, msci_security_code)
)
GO

IF OBJECT_ID('dbo.msci_idx_wgt_check') IS NULL
CREATE TABLE dbo.msci_idx_wgt_check (
    msci_index_code  varchar(6) NOT NULL,
    freq             char(1)    NOT NULL,
    basis            char(1)    NOT NULL,
    calc_date        varchar(8) NOT NULL,
    n_pub            int        NULL,                  -- T: 그날 공표 종목 수, N: 다음 영업일 공표 종목 수 (없으면 NULL)
    n                int        NULL,                  -- 고른 종목 수
    cap_ratio        float      NULL,                  -- T: 비조정 시총 / 공표, N: 다음날 시총 / 공표 (공표 없으면 NULL)
    fx_missing       int        NULL,
    passed           bit        NOT NULL,
    udt              datetime   NOT NULL DEFAULT GETDATE(),
    cty_alt          bit        NOT NULL CONSTRAINT DF_msci_idx_wgt_check_cty_alt DEFAULT 0,  -- 1 = N 국가를 다음 기간 플래그로 보완해 통과
    CONSTRAINT PK_msci_idx_wgt_check PRIMARY KEY (msci_index_code, freq, basis, calc_date)
)
GO

IF COL_LENGTH('dbo.msci_idx_wgt_check', 'cty_alt') IS NULL   -- 2026-10-29 추가분 (기존 테이블)
    ALTER TABLE dbo.msci_idx_wgt_check ADD cty_alt bit NOT NULL CONSTRAINT DF_msci_idx_wgt_check_cty_alt DEFAULT 0;
GO

CREATE OR ALTER PROCEDURE dbo.Store_MSCI_Idx_Wgt
    @msci_index_code varchar(6),
    @freq            char(1),           -- 'D' 또는 'M'
    @start_date      varchar(8),
    @end_date        varchar(8),
    @tol             float = 0.0001,    -- 시총 합 허용 오차 (1bp). 실측: 일별 ~1e-12, 월간 2002-04 이후 1bp 이내
    @dry_run         bit = 0,           -- 1 = 검증 결과만 돌려주고 저장하지 않음
    @n_tol           int = NULL         -- 종목 수 허용 차이. NULL = 일별 0, 월간 10
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;   -- 오류 나면 트랜잭션 전체 롤백 (반쯤 바뀐 상태로 남지 않게)
    IF @freq NOT IN ('D', 'M') BEGIN RAISERROR('freq 는 D 또는 M', 16, 1); RETURN; END
    DECLARE @P nvarchar(2) = CASE @freq WHEN 'M' THEN N'M_' ELSE N'' END, @sql nvarchar(max);
    DECLARE @nt int = ISNULL(@n_tol, IIF(@freq = 'M', 10, 0));

    -- 지수 규칙은 최신 지수명으로 정한다: 133333 은 2018-02 까지 이름이 'CHINA A' 였다가 'CHINA A ONSHORE' 로 바뀜
    -- (날짜별 이름으로 정하면 2018-02 이전 Onshore 가 China A 규칙으로 가서 0종목)
    DECLARE @name varchar(100) = (SELECT TOP 1 index_name FROM dbo.INDEX_REPORT WHERE msci_index_code = @msci_index_code ORDER BY calc_date DESC);
    IF @name IS NULL SET @name = (SELECT TOP 1 index_name FROM dbo.M_INDEX_REPORT WHERE msci_index_code = @msci_index_code ORDER BY calc_date DESC);

    -- 날짜별 지수 정보: 공표값, 지수 규칙, 국가목록 유무
    CREATE TABLE #ir (
        calc_date varchar(8) PRIMARY KEY, DM_flag varchar(1), EM_flag varchar(1), ir_cty varchar(2),
        acwi bit, dom bit, cna bit, n_pub int, ua_pub float, nd_pub float,
        n_pub_nx int, cl_nx varchar(8), has0 bit, has1 bit
    );
    SET @sql = N'
    INSERT INTO #ir (calc_date, DM_flag, EM_flag, ir_cty, acwi, n_pub, ua_pub, nd_pub)
    SELECT calc_date, MAX(DM_flag), MAX(EM_flag), MAX(ISO_country_symbol),
           MAX(CASE WHEN region_code = ''ACWI'' THEN 1 ELSE 0 END),
           MAX(count_of_securities), MAX(unadj_mkt_cap_eod00d_usd) * 1e6, MAX(initial_mkt_cap_eod00d_usd_nd) * 1e6
    FROM ' + @P + N'INDEX_REPORT WHERE msci_index_code = @idx AND calc_date BETWEEN @d0 AND @d1
    GROUP BY calc_date;';
    EXEC sp_executesql @sql, N'@idx varchar(6), @d0 varchar(8), @d1 varchar(8)',
         @idx = @msci_index_code, @d0 = @start_date, @d1 = @end_date;
    UPDATE #ir SET dom = IIF(@name LIKE 'CHINA A ONSHORE%', 1, 0), cna = IIF(@name = 'CHINA A', 1, 0);

    -- 다음 영업일 공표 종목 수 (= 다음날 구성 종목 수의 검증값) 와 다음 영업일 국가목록 날짜 — 일별 테이블에서.
    -- 종목 수는 3일 이내(주말 포함 바로 다음 영업일) 만: 원천에 하루가 빠지면 그 사이 편출입까지 섞여 틀린 실패가 난다 (2018-12-28 -> 12-31 결측 -> 01-01)
    UPDATE r SET n_pub_nx = (SELECT TOP 1 i.count_of_securities FROM dbo.INDEX_REPORT i
                             WHERE i.msci_index_code = @msci_index_code AND i.calc_date > r.calc_date
                               AND i.calc_date <= CONVERT(varchar(8), DATEADD(day, 3, CAST(r.calc_date AS date)), 112)
                             ORDER BY i.calc_date),
                 cl_nx    = (SELECT TOP 1 w.calc_date FROM dbo.COUNTRIES_WEIGHT w
                             WHERE w.msci_index_code = @msci_index_code AND w.calc_date > r.calc_date
                               AND w.calc_date <= CONVERT(varchar(8), DATEADD(day, 7, CAST(r.calc_date AS date)), 112)
                             ORDER BY w.calc_date)
    FROM #ir r;

    -- 국가목록: T = 같은 주기 목록 -> 없으면 일별 같은 날, N = 다음 영업일 일별 목록 -> 없으면 T 목록
    CREATE TABLE #cl (calc_date varchar(8), basis char(1), ISO_country_symbol varchar(2),
                      PRIMARY KEY (calc_date, basis, ISO_country_symbol));
    SET @sql = N'
    INSERT INTO #cl SELECT DISTINCT w.calc_date, ''T'', w.ISO_country_symbol
    FROM ' + @P + N'COUNTRIES_WEIGHT w JOIN #ir r ON r.calc_date = w.calc_date
    WHERE w.msci_index_code = @idx AND w.ISO_country_symbol IS NOT NULL;';
    EXEC sp_executesql @sql, N'@idx varchar(6)', @idx = @msci_index_code;
    INSERT INTO #cl SELECT DISTINCT w.calc_date, 'T', w.ISO_country_symbol
    FROM dbo.COUNTRIES_WEIGHT w JOIN #ir r ON r.calc_date = w.calc_date
    WHERE w.msci_index_code = @msci_index_code AND w.ISO_country_symbol IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM #cl c WHERE c.calc_date = w.calc_date);
    INSERT INTO #cl SELECT DISTINCT r.calc_date, 'N', w.ISO_country_symbol
    FROM #ir r JOIN dbo.COUNTRIES_WEIGHT w ON w.msci_index_code = @msci_index_code AND w.calc_date = r.cl_nx
    WHERE w.ISO_country_symbol IS NOT NULL;
    INSERT INTO #cl SELECT c.calc_date, 'N', c.ISO_country_symbol
    FROM #cl c JOIN #ir r ON r.calc_date = c.calc_date
    WHERE c.basis = 'T' AND r.cl_nx IS NULL;
    UPDATE r SET has0 = IIF(EXISTS (SELECT 1 FROM #cl c WHERE c.calc_date = r.calc_date AND c.basis = 'T'), 1, 0),
                 has1 = IIF(EXISTS (SELECT 1 FROM #cl c WHERE c.calc_date = r.calc_date AND c.basis = 'N'), 1, 0)
    FROM #ir r;

    -- N 보완 후보 'X': 국가목록이 없는 날, 다음 기간(같은 주기 다음 날짜, 40일 이내) 플래그로 본 국가.
    -- 공표 다음날 시총이 있어 검증할 수 있는 날만 만든다. 쓰는 건 당일 플래그로 고른 N 이 실패했을 때뿐 (#chk 아래)
    SET @sql = N'
    INSERT INTO #cl
    SELECT DISTINCT r.calc_date, ''X'', s.ISO_country_symbol
    FROM #ir r
    CROSS APPLY (SELECT MIN(i.calc_date) d FROM ' + @P + N'INDEX_REPORT i
                 WHERE i.msci_index_code = @idx AND i.calc_date > r.calc_date
                   AND i.calc_date <= CONVERT(varchar(8), DATEADD(day, 40, CAST(r.calc_date AS date)), 112)) nx
    JOIN ' + @P + N'SECURITY_REPORT s ON s.calc_date = nx.d AND s.family_std_flag = ''1'' AND s.ISO_country_symbol IS NOT NULL
    WHERE r.has1 = 0 AND r.ir_cty IS NULL AND r.nd_pub IS NOT NULL
      AND ((r.DM_flag = ''1'' AND s.DM_universe_flag = ''1'') OR (r.EM_flag = ''1'' AND s.EM_universe_flag = ''1'')
           OR (r.acwi = 1 AND ''1'' IN (s.DM_universe_flag, s.EM_universe_flag)));';
    EXEC sp_executesql @sql, N'@idx varchar(6)', @idx = @msci_index_code;

    CREATE TABLE #c (
        calc_date varchar(8), msci_security_code varchar(7), msci_issuer_code varchar(5), security_name varchar(25),
        isin varchar(12), bb_ticker varchar(25), RIC varchar(20), share_class varchar(20), share_type varchar(15),
        sub_industry varchar(9), sub_industry_next_day varchar(9), ISO_country_symbol varchar(2), ISO_country_symbol_next_day varchar(2),
        price_ISO_currency_symbol varchar(3), price_ISO_ccy_symbol_next_day varchar(3),
        std_IIF float, std_IIF_next_day float, foreign_inclusion_factor float, foreign_inc_factor_next_day float,
        domestic_inclusion_factor float, domestic_inc_factor_next_day float,
        family_std_flag varchar(1), family_std_flag_next_day varchar(1), family_std_dom_flag varchar(1), family_std_dom_flag_next_day varchar(1),
        initial_mkt_cap_usd float, initial_mkt_cap_usd_next_day float, price float, closing_number_of_shares float,
        eod_number_of_shares_today float, eod_number_of_shares_next_day float, price_adjustment_factor float, prelim_price_adj_fact_nxt_day float,
        fx float, cap_t float, cap_n float, m0 bit, m1 bit,     -- cap_t = 당일 비조정 유동시총, cap_n = 다음날 유동시총, m0/m1 = T/N 구성 여부
        m1x bit                                                 -- N 보완 후보('X' 국가) 로 본 다음날 구성 여부
    );

    SET @sql = N'
    WITH fx AS (SELECT calc_date, ISO_currency_symbol, MIN(spot_fx_eod00d) fx FROM {P}DAILY_TRACKER_XRATES
                WHERE calc_date BETWEEN @d0 AND @d1 GROUP BY calc_date, ISO_currency_symbol),
    sr AS (SELECT s.*, ROW_NUMBER() OVER (PARTITION BY s.calc_date, s.msci_security_code ORDER BY s.umarket) rn
           FROM {P}SECURITY_REPORT s
           WHERE s.calc_date BETWEEN @d0 AND @d1
             AND ''1'' IN (s.family_std_flag, s.family_std_flag_next_day, s.family_std_dom_flag, s.family_std_dom_flag_next_day))
    INSERT INTO #c
    SELECT sr.calc_date, sr.msci_security_code, sr.msci_issuer_code, sr.security_name, sr.isin,
           REPLACE(REPLACE(REPLACE(REPLACE(sr.bb_ticker, '' CG '', '' CH ''), '' CS '', '' CH ''), '' C1 '', '' CH ''), '' C2 '', '' CH ''),
           sr.RIC, sr.share_class, sr.share_type,
           sr.sub_industry, sr.sub_industry_next_day, sr.ISO_country_symbol, sr.ISO_country_symbol_next_day,
           sr.price_ISO_currency_symbol, sr.price_ISO_ccy_symbol_next_day,
           sr.std_IIF, sr.std_IIF_next_day, sr.foreign_inclusion_factor, sr.foreign_inc_factor_next_day,
           sr.domestic_inclusion_factor, sr.domestic_inc_factor_next_day,
           sr.family_std_flag, sr.family_std_flag_next_day, sr.family_std_dom_flag, sr.family_std_dom_flag_next_day,
           sr.initial_mkt_cap_usd, sr.initial_mkt_cap_usd_next_day, sr.price, sr.closing_number_of_shares,
           sr.eod_number_of_shares_today, sr.eod_number_of_shares_next_day, sr.price_adjustment_factor, sr.prelim_price_adj_fact_nxt_day,
           v.fxv,
           sr.price * sr.closing_number_of_shares * v.if0 * v.iif0 / v.fxv,
           COALESCE(sr.initial_mkt_cap_usd_next_day, sr.price * sr.eod_number_of_shares_next_day / v.fxv) * v.if1 * v.iif1,
           m.m0, m.m1, m.m1x
    FROM sr
    JOIN #ir ir ON ir.calc_date = sr.calc_date
    LEFT JOIN fx ON fx.calc_date = sr.calc_date AND fx.ISO_currency_symbol = sr.price_ISO_currency_symbol
    LEFT JOIN #cl l0 ON l0.calc_date = sr.calc_date AND l0.basis = ''T'' AND l0.ISO_country_symbol = sr.ISO_country_symbol
    LEFT JOIN #cl l1 ON l1.calc_date = sr.calc_date AND l1.basis = ''N'' AND l1.ISO_country_symbol = sr.ISO_country_symbol_next_day
    LEFT JOIN #cl lx ON lx.calc_date = sr.calc_date AND lx.basis = ''X'' AND lx.ISO_country_symbol = sr.ISO_country_symbol_next_day
    CROSS APPLY (SELECT CASE WHEN sr.price_ISO_currency_symbol = ''USD'' THEN 1.0 ELSE fx.fx END fxv,
                        IIF(ir.dom = 1, sr.family_std_dom_flag, sr.family_std_flag) f0,
                        IIF(ir.dom = 1, sr.family_std_dom_flag_next_day, sr.family_std_flag_next_day) f1,
                        IIF(ir.dom = 1, sr.domestic_inclusion_factor, sr.foreign_inclusion_factor) if0,
                        IIF(ir.dom = 1, sr.domestic_inc_factor_next_day, sr.foreign_inc_factor_next_day) if1,
                        IIF(ir.cna = 1, 1.0, ISNULL(sr.std_IIF, 1)) iif0,
                        IIF(ir.cna = 1, 1.0, ISNULL(sr.std_IIF_next_day, 1)) iif1,
                        -- 국가목록이 없을 때만 쓰는 플래그 대응 (단일국가 지수는 넓히지 않는다 — 다른 나라 dom flag 혼입)
                        CASE WHEN ir.ir_cty IS NULL AND ((ir.DM_flag = ''1'' AND sr.DM_universe_flag = ''1'')
                                                      OR (ir.EM_flag = ''1'' AND sr.EM_universe_flag = ''1'')
                                                      OR (ir.acwi = 1 AND ''1'' IN (sr.DM_universe_flag, sr.EM_universe_flag)))
                             THEN 1 ELSE 0 END fl) v
    CROSS APPLY (SELECT CASE WHEN v.f0 = ''1'' AND (ir.cna = 0 OR sr.price_ISO_currency_symbol = ''CNH'')
                              AND (l0.calc_date IS NOT NULL OR sr.ISO_country_symbol = ir.ir_cty OR (ir.has0 = 0 AND v.fl = 1))
                             THEN 1 ELSE 0 END m0,
                        CASE WHEN v.f1 = ''1'' AND (ir.cna = 0 OR sr.price_ISO_ccy_symbol_next_day = ''CNH'')
                              AND (l1.calc_date IS NOT NULL OR sr.ISO_country_symbol_next_day = ir.ir_cty OR (ir.has1 = 0 AND v.fl = 1))
                             THEN 1 ELSE 0 END m1,
                        CASE WHEN v.f1 = ''1'' AND (ir.cna = 0 OR sr.price_ISO_ccy_symbol_next_day = ''CNH'') AND lx.calc_date IS NOT NULL
                             THEN 1 ELSE 0 END m1x) m
    WHERE sr.rn = 1 AND (m.m0 = 1 OR m.m1 = 1 OR m.m1x = 1)';
    SET @sql = REPLACE(@sql, N'{P}', @P);
    EXEC sp_executesql @sql, N'@d0 varchar(8), @d1 varchar(8)', @d0 = @start_date, @d1 = @end_date;

    -- 기준·날짜별 검증 (구성 종목이 하나도 없는 날짜도 실패로 남게 #ir 에서 출발)
    SELECT r.calc_date, CAST('T' AS char(1)) basis, r.n_pub, COUNT(c.calc_date) n,
           SUM(c.cap_t) / NULLIF(r.ua_pub, 0) cap_ratio,
           SUM(CASE WHEN c.calc_date IS NOT NULL AND (c.fx IS NULL OR c.cap_t IS NULL) THEN 1 ELSE 0 END) fx_missing
    INTO #chk FROM #ir r LEFT JOIN #c c ON c.calc_date = r.calc_date AND c.m0 = 1
    GROUP BY r.calc_date, r.n_pub, r.ua_pub
    UNION ALL
    SELECT r.calc_date, 'N', r.n_pub_nx, COUNT(c.calc_date),
           SUM(c.cap_n) / NULLIF(r.nd_pub, 0),
           SUM(CASE WHEN c.calc_date IS NOT NULL AND (c.fx IS NULL OR c.cap_n IS NULL) THEN 1 ELSE 0 END)
    FROM #ir r LEFT JOIN #c c ON c.calc_date = r.calc_date AND c.m1 = 1
    GROUP BY r.calc_date, r.n_pub_nx, r.nd_pub;
    CREATE UNIQUE CLUSTERED INDEX ix_chk ON #chk (basis, calc_date);

    ALTER TABLE #chk ADD passed bit, cty_alt bit NOT NULL DEFAULT 0;
    UPDATE #chk SET passed = CASE WHEN ABS(n - n_pub) <= @nt AND n > 0 AND fx_missing = 0 AND ABS(cap_ratio - 1) <= @tol THEN 1 ELSE 0 END
    WHERE basis = 'T';
    UPDATE k SET passed = CASE WHEN k.n > 0 AND k.fx_missing = 0
                                AND (k.n_pub IS NULL OR ABS(k.n - k.n_pub) <= @nt)
                                AND (k.cap_ratio IS NULL OR ABS(k.cap_ratio - 1) <= @tol)
                                AND (k.cap_ratio IS NOT NULL OR t.passed = 1) THEN 1 ELSE 0 END
    FROM #chk k JOIN #chk t ON t.basis = 'T' AND t.calc_date = k.calc_date
    WHERE k.basis = 'N';

    -- N 보완: 당일 플래그로 고른 N 이 실패한 날, 'X' 국가로 다시 고른 구성이 공표 다음날 시총(+종목 수)과 맞으면 그것으로 바꾼다
    SELECT c.calc_date, COUNT(*) n, SUM(c.cap_n) / NULLIF(MAX(r.nd_pub), 0) cap_ratio,
           SUM(CASE WHEN c.fx IS NULL OR c.cap_n IS NULL THEN 1 ELSE 0 END) fx_missing
    INTO #alt
    FROM #c c JOIN #ir r ON r.calc_date = c.calc_date
    JOIN #chk k ON k.basis = 'N' AND k.calc_date = c.calc_date AND k.passed = 0
    WHERE c.m1x = 1
    GROUP BY c.calc_date;
    UPDATE k SET n = a.n, cap_ratio = a.cap_ratio, fx_missing = a.fx_missing, passed = 1, cty_alt = 1
    FROM #chk k JOIN #alt a ON a.calc_date = k.calc_date
    WHERE k.basis = 'N' AND a.n > 0 AND a.fx_missing = 0 AND ABS(a.cap_ratio - 1) <= @tol
      AND (k.n_pub IS NULL OR ABS(a.n - k.n_pub) <= @nt);
    UPDATE c SET m1 = c.m1x
    FROM #c c JOIN #chk k ON k.basis = 'N' AND k.calc_date = c.calc_date AND k.cty_alt = 1;

    IF @dry_run = 1
    BEGIN
        SELECT @msci_index_code msci_index_code, @freq freq, * FROM #chk ORDER BY calc_date, basis;
        RETURN;
    END

    -- 저장 후보: 통과한 (기준, 날짜) 의 구성 종목
    SELECT k.basis, x.*, k.mkt_cap_usd, k.mkt_cap_usd / SUM(k.mkt_cap_usd) OVER (PARTITION BY k.basis, x.calc_date) wgt
    INTO #n
    FROM #c x
    CROSS APPLY (SELECT 'T' basis, x.cap_t mkt_cap_usd WHERE x.m0 = 1
                 UNION ALL SELECT 'N', x.cap_n WHERE x.m1 = 1) k
    JOIN #chk c ON c.basis = k.basis AND c.calc_date = x.calc_date AND c.passed = 1;
    -- 없으면 DELETE/INSERT 의 NOT EXISTS 가 #n 전체를 행마다 훑어 지수 이력이 쌓일수록 느려진다 (EM 계열 연 1회분 3시간)
    CREATE UNIQUE CLUSTERED INDEX ix_n ON #n (basis, calc_date, msci_security_code);

    DECLARE @ins int, @upd int, @del int;
    BEGIN TRAN;
        -- 검증 로그: 결과가 달라진 (기준, 날짜) 만 갱신
        UPDATE k SET n_pub = c.n_pub, n = c.n, cap_ratio = c.cap_ratio, fx_missing = c.fx_missing, passed = c.passed,
                     cty_alt = c.cty_alt, udt = GETDATE()
        FROM dbo.msci_idx_wgt_check k JOIN #chk c ON c.basis = k.basis AND c.calc_date = k.calc_date
        WHERE k.msci_index_code = @msci_index_code AND k.freq = @freq
          AND (EXISTS (SELECT k.n_pub, k.n, k.fx_missing, k.passed, k.cty_alt EXCEPT SELECT c.n_pub, c.n, c.fx_missing, c.passed, c.cty_alt)
               OR ABS(ISNULL(k.cap_ratio, -1) - ISNULL(c.cap_ratio, -1)) > 1e-12);
        INSERT INTO dbo.msci_idx_wgt_check (msci_index_code, freq, basis, calc_date, n_pub, n, cap_ratio, fx_missing, passed, cty_alt)
        SELECT @msci_index_code, @freq, c.basis, c.calc_date, c.n_pub, c.n, c.cap_ratio, c.fx_missing, c.passed, c.cty_alt
        FROM #chk c
        WHERE NOT EXISTS (SELECT 1 FROM dbo.msci_idx_wgt_check k
                          WHERE k.msci_index_code = @msci_index_code AND k.freq = @freq AND k.basis = c.basis AND k.calc_date = c.calc_date);

        -- 통과한 (기준, 날짜) 에서 구성에서 빠진 종목
        DELETE w FROM dbo.msci_idx_wgt w JOIN #chk c ON c.basis = w.basis AND c.calc_date = w.calc_date AND c.passed = 1
        WHERE w.msci_index_code = @msci_index_code AND w.freq = @freq
          AND NOT EXISTS (SELECT 1 FROM #n n WHERE n.basis = w.basis AND n.calc_date = w.calc_date AND n.msci_security_code = w.msci_security_code);
        SET @del = @@ROWCOUNT;

        -- 값이 하나라도 바뀐 종목만 (EXCEPT = NULL 끼리 같다고 보는 비교)
        UPDATE w SET msci_issuer_code = n.msci_issuer_code, security_name = n.security_name, isin = n.isin, bb_ticker = n.bb_ticker,
                     RIC = n.RIC, share_class = n.share_class, share_type = n.share_type,
                     sub_industry = n.sub_industry, sub_industry_next_day = n.sub_industry_next_day,
                     ISO_country_symbol = n.ISO_country_symbol, ISO_country_symbol_next_day = n.ISO_country_symbol_next_day,
                     price_ISO_currency_symbol = n.price_ISO_currency_symbol, price_ISO_ccy_symbol_next_day = n.price_ISO_ccy_symbol_next_day,
                     std_IIF = n.std_IIF, std_IIF_next_day = n.std_IIF_next_day,
                     foreign_inclusion_factor = n.foreign_inclusion_factor, foreign_inc_factor_next_day = n.foreign_inc_factor_next_day,
                     domestic_inclusion_factor = n.domestic_inclusion_factor, domestic_inc_factor_next_day = n.domestic_inc_factor_next_day,
                     family_std_flag = n.family_std_flag, family_std_flag_next_day = n.family_std_flag_next_day,
                     family_std_dom_flag = n.family_std_dom_flag, family_std_dom_flag_next_day = n.family_std_dom_flag_next_day,
                     initial_mkt_cap_usd = n.initial_mkt_cap_usd, initial_mkt_cap_usd_next_day = n.initial_mkt_cap_usd_next_day,
                     price = n.price, closing_number_of_shares = n.closing_number_of_shares,
                     eod_number_of_shares_today = n.eod_number_of_shares_today, eod_number_of_shares_next_day = n.eod_number_of_shares_next_day,
                     price_adjustment_factor = n.price_adjustment_factor, prelim_price_adj_fact_nxt_day = n.prelim_price_adj_fact_nxt_day,
                     fx = n.fx, mkt_cap_usd = n.mkt_cap_usd, wgt = n.wgt, udt = GETDATE()
        FROM dbo.msci_idx_wgt w JOIN #n n ON n.basis = w.basis AND n.calc_date = w.calc_date AND n.msci_security_code = w.msci_security_code
        WHERE w.msci_index_code = @msci_index_code AND w.freq = @freq
          AND (ABS(w.wgt - n.wgt) > 1e-12
               OR EXISTS (SELECT w.msci_issuer_code, w.security_name, w.isin, w.bb_ticker, w.RIC, w.share_class, w.share_type,
                                 w.sub_industry, w.sub_industry_next_day, w.ISO_country_symbol, w.ISO_country_symbol_next_day,
                                 w.price_ISO_currency_symbol, w.price_ISO_ccy_symbol_next_day, w.std_IIF, w.std_IIF_next_day,
                                 w.foreign_inclusion_factor, w.foreign_inc_factor_next_day, w.domestic_inclusion_factor, w.domestic_inc_factor_next_day,
                                 w.family_std_flag, w.family_std_flag_next_day, w.family_std_dom_flag, w.family_std_dom_flag_next_day,
                                 w.initial_mkt_cap_usd, w.initial_mkt_cap_usd_next_day, w.price, w.closing_number_of_shares,
                                 w.eod_number_of_shares_today, w.eod_number_of_shares_next_day, w.price_adjustment_factor,
                                 w.prelim_price_adj_fact_nxt_day, w.fx, w.mkt_cap_usd
                          EXCEPT
                          SELECT n.msci_issuer_code, n.security_name, n.isin, n.bb_ticker, n.RIC, n.share_class, n.share_type,
                                 n.sub_industry, n.sub_industry_next_day, n.ISO_country_symbol, n.ISO_country_symbol_next_day,
                                 n.price_ISO_currency_symbol, n.price_ISO_ccy_symbol_next_day, n.std_IIF, n.std_IIF_next_day,
                                 n.foreign_inclusion_factor, n.foreign_inc_factor_next_day, n.domestic_inclusion_factor, n.domestic_inc_factor_next_day,
                                 n.family_std_flag, n.family_std_flag_next_day, n.family_std_dom_flag, n.family_std_dom_flag_next_day,
                                 n.initial_mkt_cap_usd, n.initial_mkt_cap_usd_next_day, n.price, n.closing_number_of_shares,
                                 n.eod_number_of_shares_today, n.eod_number_of_shares_next_day, n.price_adjustment_factor,
                                 n.prelim_price_adj_fact_nxt_day, n.fx, n.mkt_cap_usd));
        SET @upd = @@ROWCOUNT;

        -- 새로 들어온 종목
        INSERT INTO dbo.msci_idx_wgt (msci_index_code, freq, basis, calc_date, msci_security_code, msci_issuer_code, security_name, isin, bb_ticker,
               RIC, share_class, share_type, sub_industry, sub_industry_next_day, ISO_country_symbol, ISO_country_symbol_next_day,
               price_ISO_currency_symbol, price_ISO_ccy_symbol_next_day, std_IIF, std_IIF_next_day,
               foreign_inclusion_factor, foreign_inc_factor_next_day, domestic_inclusion_factor, domestic_inc_factor_next_day,
               family_std_flag, family_std_flag_next_day, family_std_dom_flag, family_std_dom_flag_next_day,
               initial_mkt_cap_usd, initial_mkt_cap_usd_next_day, price, closing_number_of_shares,
               eod_number_of_shares_today, eod_number_of_shares_next_day, price_adjustment_factor, prelim_price_adj_fact_nxt_day,
               fx, mkt_cap_usd, wgt)
        SELECT @msci_index_code, @freq, n.basis, n.calc_date, n.msci_security_code, n.msci_issuer_code, n.security_name, n.isin, n.bb_ticker,
               n.RIC, n.share_class, n.share_type, n.sub_industry, n.sub_industry_next_day, n.ISO_country_symbol, n.ISO_country_symbol_next_day,
               n.price_ISO_currency_symbol, n.price_ISO_ccy_symbol_next_day, n.std_IIF, n.std_IIF_next_day,
               n.foreign_inclusion_factor, n.foreign_inc_factor_next_day, n.domestic_inclusion_factor, n.domestic_inc_factor_next_day,
               n.family_std_flag, n.family_std_flag_next_day, n.family_std_dom_flag, n.family_std_dom_flag_next_day,
               n.initial_mkt_cap_usd, n.initial_mkt_cap_usd_next_day, n.price, n.closing_number_of_shares,
               n.eod_number_of_shares_today, n.eod_number_of_shares_next_day, n.price_adjustment_factor, n.prelim_price_adj_fact_nxt_day,
               n.fx, n.mkt_cap_usd, n.wgt
        FROM #n n
        WHERE NOT EXISTS (SELECT 1 FROM dbo.msci_idx_wgt w WHERE w.msci_index_code = @msci_index_code AND w.freq = @freq
                          AND w.basis = n.basis AND w.calc_date = n.calc_date AND w.msci_security_code = n.msci_security_code);
        SET @ins = @@ROWCOUNT;
    COMMIT;

    SELECT @msci_index_code msci_index_code, @freq freq, COUNT(*) / 2 dates,
           SUM(CASE WHEN basis = 'T' THEN CAST(passed AS int) END) passed_t,
           SUM(CASE WHEN basis = 'N' THEN CAST(passed AS int) END) passed_n,
           MIN(CASE WHEN passed = 0 AND basis = 'T' THEN calc_date END) first_failed_t,
           MIN(CASE WHEN passed = 0 AND basis = 'N' THEN calc_date END) first_failed_n,
           @ins inserted, @upd updated, @del deleted
    FROM #chk;
END
GO

-- bbg 업로드용: 기존 msci_bbu_csv 와 같은 컬럼·순서 (일별, 다음날 기준만). sedol·cusip 은 원천 NULL.
-- 기존과 같게 ISO_country_symbol_next_day 칸에는 당일 국가를 넣는다 (기존 INSERT 가 위치로 넣어 생긴 관례).
CREATE OR ALTER VIEW dbo.View_msci_idx_wgt_bbu AS
SELECT CONVERT(varchar(10), CAST(w.calc_date AS datetime), 121) ddt, w.msci_index_code, ir.index_name, w.bb_ticker, w.wgt,
       w.mkt_cap_usd initial_mkt_cap_eod_usd_nd, w.calc_date, w.security_name, w.RIC, w.msci_security_code,
       CAST(NULL AS varchar(7)) sedol, w.isin, CAST(NULL AS varchar(9)) cusip, w.share_class, w.share_type,
       w.sub_industry, w.sub_industry_next_day, w.std_IIF, w.std_IIF_next_day,
       w.foreign_inclusion_factor, w.foreign_inc_factor_next_day, w.domestic_inclusion_factor, w.domestic_inc_factor_next_day,
       w.initial_mkt_cap_usd, w.initial_mkt_cap_usd_next_day,
       w.family_std_flag, w.family_std_flag_next_day, w.family_std_dom_flag, w.family_std_dom_flag_next_day,
       w.ISO_country_symbol ISO_country_symbol_next_day, w.price_ISO_currency_symbol, w.price_ISO_ccy_symbol_next_day,
       w.price PriceInLocalCcy, w.closing_number_of_shares, w.eod_number_of_shares_today, w.eod_number_of_shares_next_day
FROM dbo.msci_idx_wgt w
OUTER APPLY (SELECT MAX(i.index_name) index_name FROM dbo.INDEX_REPORT i
             WHERE i.msci_index_code = w.msci_index_code AND i.calc_date = w.calc_date) ir
WHERE w.freq = 'D' AND w.basis = 'N'
GO

/* ---- 최초 적재 (1회) — 연도 단위로 나눠 돌리면 잠금·로그 부담이 작다 ----
EXEC dbo.Store_MSCI_Idx_Wgt '990100', 'M', '19970101', '20261231';   -- 1997~2002-03 은 검증 실패로 저장 안 됨 (공표 대비 +2~4종목)
EXEC dbo.Store_MSCI_Idx_Wgt '990100', 'D', '20170401', '20171231';   -- 이후 연도별 반복 ... '20260101', '20261231'

---- 확인 ----
SELECT * FROM dbo.msci_idx_wgt_check WHERE passed = 0 ORDER BY msci_index_code, freq, basis, calc_date;
EXEC dbo.Store_MSCI_Idx_Wgt '891800', 'D', '20190501', '20190531', @dry_run = 1;   -- 저장 없이 검증만
*/

/* ---- SQL Server Agent 작업 (2026-10-28 생성) ----
   기존 'MSCI Daily. 15분 소요' (07:40 부터 2시간 간격, Job_Batch 포함) 와 분리된 별도 작업 — 기존 흐름에 영향 없음.
   매일 08:10 (2026-10-29 사용자 요청으로 12:30 -> 08:10; 07:40 MSCI Daily 실행은 최근 8~10분 소요라 ~07:50 종료):
   일별 최근 10일 + 월간 최근 2개월 재처리. 바뀐 행만 반영하므로 몇 번 돌아도 결과·udt 가 같다.
   07:40 적재가 늦어 그날 원천이 없으면 다음 날 08:10 실행(10일 창)에서 채워진다.
   실패 날짜는 msci_idx_wgt_check.passed = 0 으로 남는다.
USE msdb;
EXEC dbo.sp_add_job @job_name = N'QMind MSCI Index Weights', @owner_login_name = N'sa',
     @description = N'MXWO(990100) 구성·비중을 검증 통과분만 MSCIDB.dbo.msci_idx_wgt 에 저장 (QMind 리스크 모델용)';
EXEC dbo.sp_add_jobstep @job_name = N'QMind MSCI Index Weights', @step_name = N'Store_MSCI_Idx_Wgt 990100',
     @subsystem = N'TSQL', @database_name = N'MSCIDB', @command = N'
DECLARE @e varchar(8) = CONVERT(varchar(8), GETDATE(), 112);
DECLARE @d varchar(8) = CONVERT(varchar(8), DATEADD(day, -10, GETDATE()), 112);
DECLARE @m varchar(8) = CONVERT(varchar(8), DATEADD(month, -2, GETDATE()), 112);
EXEC dbo.Store_MSCI_Idx_Wgt ''990100'', ''D'', @d, @e;
EXEC dbo.Store_MSCI_Idx_Wgt ''990100'', ''M'', @m, @e;';
-- 2026-10-29: sp_update_jobstep 으로 892400(ACWI)·891800(EM)·133333(CHINA A ONSHORE)·718708(CHINA A) 의 D·M 추가 (5지수 전체 ~8초).
--             명령은 CRLF 줄바꿈 (LF 만 쓰면 SSMS 작업 단계 창에 한 줄로 보여 주석처럼 보임)
EXEC dbo.sp_add_jobschedule @job_name = N'QMind MSCI Index Weights', @name = N'Daily 08:10',
     @freq_type = 4, @freq_interval = 1, @active_start_time = 81000;   -- 처음엔 'Daily 12:30' (123000), 10-29 sp_update_schedule 로 변경
EXEC dbo.sp_add_jobserver @job_name = N'QMind MSCI Index Weights';
*/
