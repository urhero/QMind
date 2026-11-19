/* ============================================================================
   GICS 회사·종목·이력 테이블 이어 쌓기 (xf_target gic_company / gic_security / gic_history) - 2026-10-31, 11-04 원본 통합
   - 배경: GICS History with CUSIP Daily 구독을 끊어 gic_company / gic_security / gic_history 가 2025-12-19 에 멈췄다.
     ClariFI(clarifi_usp_xf_gicsHistory 등)가 이 원본 이름을 읽으므로 원본 표를 다른 구독 테이블로 매일 갱신한다.
   - 2026-10-31 ~ 11-04 에는 *_live 표에 따로 쌓았고, 11-04 에 원본으로 합치고 *_live 를 지웠다 (아래 1회성 블록).
     원본 형식(칸·길이)은 Loader 표 그대로 둔다: udt 칸 없음, tic varchar(8).
   - Loader 구독(1306)은 활성으로 남아 있지만 2025-12-20 뒤 파일이 없다. 파일이 다시 오면 Loader 와 이 프로시저가 같은 표를 쓴다.
   - 멈춘 표 사본 (*_frozen): 처음 한 번만 복사. 1999 이전 이력과 co_hgic 에 없는 회사는 멈춘 표에만 있고,
     구독을 끊어 다시 받을 수 없다. 원본이 재구축으로 비워져도 다음 실행이 사본에서 다시 채운다.
   - gic_history: co_hgic 에 있는 회사는 co_hgic 가 정본 (날짜가 있어 S&P 사후 수정까지 따라간다).
       co_hgic 첫날(대부분 1999-06-30) 이전 행만 멈춘 표에서 가져온다. 이전 행이 첫날을 덮으면 코드가 같을 때
       co_hgic 첫 행의 시작일을 앞으로 당기고, 다르면 이전 행을 첫날 전날로 닫는다.
       co_hgic 에 없는 회사는 건드리지 않는다 (마지막 값 유지).
   - gic_company: gic_ccompany > company > 저장값. 대상 = gic_history 또는 gic_ccompany 에 있는 회사.
   - gic_security: gic_csecurity > security·sec_idcurrent > 저장값. 대상 = 저장된 줄, gic_csecurity,
       gic_company 회사의 security 줄. SEDOL 은 gic_csecurity 에만 있다 (security 는 전부 NULL).
       sec_idcurrent 티커는 8자 이하만 쓴다 (원본 칸 길이. 넘으면 저장값 유지).
   - 행을 지우지 않고 NULL 로 덮지 않는다: 원천에서 사라진 값은 마지막 값으로 남는다 (gic_history 의 사후 수정 삭제만 예외).
   - 멈춤 방지:
       원천 행 수가 직전 실행의 @min_ratio 미만이면 아무것도 고치지 않고 오류 (재적재 중 빈 표로 덮지 않게).
       gic_ccompany 현재 세부산업과 co_hgic 열린 행이 다른 회사 수를 gic_update_log.gics_mismatch 에 남기고,
       @max_mismatch 를 넘으면 저장 후 오류로 끝낸다 (한쪽 구독이 소리 없이 멈추면 늘어난다. GICS 변경은 월 135~188행).
   - 실행 시각: 매일 14:00. gic_c* 는 18:00 에 D일 파일을 받고 co_hgic 는 다음 01시대에 같은 D일 파일을 받는다.
       그 사이에는 그날 GICS 변경만큼 gics_mismatch 가 생기므로 (10-31 19:00 첫 예약 실행 106건 오류),
       co_hgic·company·security(Core 2) 11시대 적재(화~토, 최근 30일 최대 11:06)와 드문 13시대 추가 파일(10-02 co_hgic 22행) 뒤, gic_c*·sec_idcurrent 18:00 적재 전에 돌린다.
   - @dry_run = 1: 저장하지 않고 건수만 돌려준다.
   ============================================================================ */
USE [xf_target]
GO

IF OBJECT_ID('dbo.gic_company_frozen') IS NULL
BEGIN
    SELECT gvkey, conm, costat, fic, loc INTO dbo.gic_company_frozen FROM dbo.gic_company;
    ALTER TABLE dbo.gic_company_frozen ADD CONSTRAINT PK_gic_company_frozen PRIMARY KEY (gvkey);
END
IF OBJECT_ID('dbo.gic_security_frozen') IS NULL
BEGIN
    SELECT gvkey, iid, cusip, dsci, exchg, isin, secstat, sedol, tic, tpci INTO dbo.gic_security_frozen FROM dbo.gic_security;
    ALTER TABLE dbo.gic_security_frozen ADD CONSTRAINT PK_gic_security_frozen PRIMARY KEY (gvkey, iid);
END
IF OBJECT_ID('dbo.gic_history_frozen') IS NULL
BEGIN
    SELECT gvkey, indfrom, indthru, ggroup, gind, gsector, gsubind INTO dbo.gic_history_frozen FROM dbo.gic_history;
    ALTER TABLE dbo.gic_history_frozen ADD CONSTRAINT PK_gic_history_frozen PRIMARY KEY (gvkey, indfrom);
END
GO

IF OBJECT_ID('dbo.gic_update_log') IS NULL
CREATE TABLE dbo.gic_update_log (
    run_at        datetime NOT NULL CONSTRAINT PK_gic_update_log PRIMARY KEY,
    n_co_hgic     int NOT NULL,        -- 원천 행 수 (다음 실행의 비교 기준)
    n_ccompany    int NOT NULL,
    n_csecurity   int NOT NULL,
    n_company     int NOT NULL,
    n_security    int NOT NULL,
    n_idcurrent   int NOT NULL,
    h_ins         int NOT NULL,        -- 이번에 실제로 바뀐 행 수 (h = history, c = company, s = security)
    h_upd         int NOT NULL,
    h_del         int NOT NULL,
    c_ins         int NOT NULL,
    c_upd         int NOT NULL,
    s_ins         int NOT NULL,
    s_upd         int NOT NULL,
    gics_mismatch int NOT NULL         -- gic_ccompany 와 co_hgic 열린 행의 세부산업이 다른 회사 수
);
GO

-- 1회성 (2026-11-04): *_live 를 원본으로 옮기고 지운다. *_live 가 없으면 건너뛴다.
IF OBJECT_ID('dbo.gic_company_live') IS NOT NULL
BEGIN
    SET XACT_ABORT ON;
    BEGIN TRANSACTION;

    DELETE dbo.gic_history;
    INSERT dbo.gic_history (gvkey, indfrom, indthru, ggroup, gind, gsector, gsubind)
    SELECT gvkey, indfrom, indthru, ggroup, gind, gsector, gsubind FROM dbo.gic_history_live;
    DELETE dbo.gic_company;
    INSERT dbo.gic_company (gvkey, conm, costat, fic, loc)
    SELECT gvkey, conm, costat, fic, loc FROM dbo.gic_company_live;
    DELETE dbo.gic_security;
    INSERT dbo.gic_security (gvkey, iid, cusip, dsci, exchg, isin, secstat, sedol, tic, tpci)
    SELECT gvkey, iid, cusip, dsci, exchg, isin, secstat, sedol, tic, tpci FROM dbo.gic_security_live;

    IF EXISTS (SELECT gvkey, indfrom, indthru, ggroup, gind, gsector, gsubind FROM dbo.gic_history_live
               EXCEPT SELECT gvkey, indfrom, indthru, ggroup, gind, gsector, gsubind FROM dbo.gic_history)
       OR EXISTS (SELECT gvkey, conm, costat, fic, loc FROM dbo.gic_company_live EXCEPT SELECT gvkey, conm, costat, fic, loc FROM dbo.gic_company)
       OR EXISTS (SELECT gvkey, iid, cusip, dsci, exchg, isin, secstat, sedol, tic, tpci FROM dbo.gic_security_live
                  EXCEPT SELECT gvkey, iid, cusip, dsci, exchg, isin, secstat, sedol, tic, tpci FROM dbo.gic_security)
        THROW 50000, 'gic_* merge: copied rows differ from *_live. Rolled back.', 1;

    DROP TABLE dbo.gic_history_live;
    DROP TABLE dbo.gic_company_live;
    DROP TABLE dbo.gic_security_live;
    COMMIT TRANSACTION;
END
GO

IF OBJECT_ID('dbo.usp_gic_update') IS NOT NULL DROP PROCEDURE dbo.usp_gic_update
GO
CREATE PROCEDURE dbo.usp_gic_update
    @dry_run      bit   = 0,
    @min_ratio    float = 0.95,
    @max_mismatch int   = 100
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @now datetime = getdate();

    -- 원천은 트랜잭션 밖에서 한 번에 읽어 Loader 와 잠금이 겹치지 않게 한다
    SELECT gvkey, indfrom, indthru, ggroup, gind, gsector, gsubind INTO #co FROM dbo.co_hgic WHERE indtype = 'GICS';
    CREATE UNIQUE CLUSTERED INDEX cx ON #co (gvkey, indfrom);
    SELECT gvkey, conm, costat, fic, loc, gsubind INTO #cc FROM dbo.gic_ccompany;
    CREATE UNIQUE CLUSTERED INDEX cx ON #cc (gvkey);
    SELECT gvkey, iid, cusip, dsci, exchg, isin, secstat, sedol, tic, tpci INTO #cs FROM dbo.gic_csecurity;
    CREATE UNIQUE CLUSTERED INDEX cx ON #cs (gvkey, iid);
    SELECT gvkey, conm, costat, fic, loc INTO #comp FROM dbo.company;
    CREATE UNIQUE CLUSTERED INDEX cx ON #comp (gvkey);
    SELECT gvkey, iid, dsci, exchg, secstat, tpci INTO #sec FROM dbo.security;
    CREATE UNIQUE CLUSTERED INDEX cx ON #sec (gvkey, iid);
    SELECT gvkey, iid,
           MAX(CASE WHEN item = 'ISIN'  THEN itemvalue END) isin,
           MAX(CASE WHEN item = 'CUSIP' THEN itemvalue END) cusip,
           MAX(CASE WHEN item = 'TIC' AND LEN(itemvalue) <= 8 THEN itemvalue END) tic  -- gic_security.tic 는 varchar(8)
    INTO #idc FROM dbo.sec_idcurrent WHERE item IN ('ISIN', 'CUSIP', 'TIC') GROUP BY gvkey, iid;
    CREATE UNIQUE CLUSTERED INDEX cx ON #idc (gvkey, iid);

    DECLARE @n_co int = (SELECT COUNT(*) FROM #co), @n_cc int = (SELECT COUNT(*) FROM #cc), @n_cs int = (SELECT COUNT(*) FROM #cs),
            @n_comp int = (SELECT COUNT(*) FROM #comp), @n_sec int = (SELECT COUNT(*) FROM #sec), @n_idc int = (SELECT COUNT(*) FROM #idc);
    DECLARE @p_co int = 1, @p_cc int = 1, @p_cs int = 1, @p_comp int = 1, @p_sec int = 1, @p_idc int = 1;
    SELECT TOP 1 @p_co = n_co_hgic, @p_cc = n_ccompany, @p_cs = n_csecurity, @p_comp = n_company, @p_sec = n_security, @p_idc = n_idcurrent
    FROM dbo.gic_update_log ORDER BY run_at DESC;

    IF @n_co < @min_ratio * @p_co OR @n_cc < @min_ratio * @p_cc OR @n_cs < @min_ratio * @p_cs
       OR @n_comp < @min_ratio * @p_comp OR @n_sec < @min_ratio * @p_sec OR @n_idc < @min_ratio * @p_idc
    BEGIN
        RAISERROR('usp_gic_update: source row count dropped below @min_ratio of the last run (co_hgic %d, gic_ccompany %d, gic_csecurity %d, company %d, security %d, sec_idcurrent %d). Nothing changed.',
                  16, 1, @n_co, @n_cc, @n_cs, @n_comp, @n_sec, @n_idc);
        RETURN;
    END

    -- gic_history 목표 행: co_hgic + 그 첫날 이전의 멈춘 표 행
    SELECT f.gvkey, f.indfrom, f.indthru, f.ggroup, f.gind, f.gsector, f.gsubind, c.cofirst
    INTO #pre
    FROM dbo.gic_history_frozen f
    JOIN (SELECT gvkey, MIN(indfrom) cofirst FROM #co GROUP BY gvkey) c ON c.gvkey = f.gvkey
    WHERE f.indfrom < c.cofirst;

    SELECT p.gvkey, p.indfrom pre_from, p.cofirst,
           CASE WHEN p.indthru IS NULL OR p.indthru >= p.cofirst THEN 1 ELSE 0 END covers,
           CASE WHEN p.gsubind = c.gsubind THEN 1 ELSE 0 END same_code
    INTO #last_pre
    FROM (SELECT gvkey, indfrom, indthru, gsubind, cofirst, ROW_NUMBER() OVER (PARTITION BY gvkey ORDER BY indfrom DESC) rn FROM #pre) p
    JOIN #co c ON c.gvkey = p.gvkey AND c.indfrom = p.cofirst
    WHERE p.rn = 1;

    SELECT c.gvkey, COALESCE(l.pre_from, c.indfrom) indfrom, c.indthru, c.ggroup, c.gind, c.gsector, c.gsubind
    INTO #h
    FROM #co c
    LEFT JOIN #last_pre l ON l.gvkey = c.gvkey AND l.cofirst = c.indfrom AND l.covers = 1 AND l.same_code = 1
    UNION ALL
    SELECT p.gvkey, p.indfrom, CASE WHEN l.covers = 1 THEN DATEADD(day, -1, p.cofirst) ELSE p.indthru END, p.ggroup, p.gind, p.gsector, p.gsubind
    FROM #pre p
    LEFT JOIN #last_pre l ON l.gvkey = p.gvkey AND l.pre_from = p.indfrom
    WHERE NOT (l.covers = 1 AND l.same_code = 1 AND l.pre_from IS NOT NULL);
    CREATE UNIQUE CLUSTERED INDEX cx ON #h (gvkey, indfrom);

    DECLARE @mismatch int = (SELECT COUNT(*) FROM #cc c WHERE NOT EXISTS (SELECT 1 FROM #co o WHERE o.gvkey = c.gvkey AND o.indthru IS NULL AND o.gsubind = c.gsubind));
    DECLARE @h_ins int, @h_upd int, @h_del int, @c_ins int, @c_upd int, @s_ins int, @s_upd int;

    BEGIN TRANSACTION;

    -- gic_history
    DELETE t FROM dbo.gic_history t
    WHERE EXISTS (SELECT 1 FROM #co g WHERE g.gvkey = t.gvkey)
      AND NOT EXISTS (SELECT 1 FROM #h h WHERE h.gvkey = t.gvkey AND h.indfrom = t.indfrom);
    SET @h_del = @@ROWCOUNT;

    UPDATE t SET indthru = h.indthru, ggroup = h.ggroup, gind = h.gind, gsector = h.gsector, gsubind = h.gsubind
    FROM dbo.gic_history t JOIN #h h ON h.gvkey = t.gvkey AND h.indfrom = t.indfrom
    WHERE EXISTS (SELECT t.indthru, t.ggroup, t.gind, t.gsector, t.gsubind EXCEPT SELECT h.indthru, h.ggroup, h.gind, h.gsector, h.gsubind);
    SET @h_upd = @@ROWCOUNT;

    INSERT dbo.gic_history (gvkey, indfrom, indthru, ggroup, gind, gsector, gsubind)
    SELECT h.gvkey, h.indfrom, h.indthru, h.ggroup, h.gind, h.gsector, h.gsubind FROM #h h
    WHERE NOT EXISTS (SELECT 1 FROM dbo.gic_history t WHERE t.gvkey = h.gvkey AND t.indfrom = h.indfrom);
    SET @h_ins = @@ROWCOUNT;

    -- co_hgic 에 없고 아직 저장되지 않은 회사는 멈춘 표 값으로 (원본이 비워졌을 때만 해당)
    INSERT dbo.gic_history (gvkey, indfrom, indthru, ggroup, gind, gsector, gsubind)
    SELECT f.gvkey, f.indfrom, f.indthru, f.ggroup, f.gind, f.gsector, f.gsubind FROM dbo.gic_history_frozen f
    WHERE NOT EXISTS (SELECT 1 FROM #co g WHERE g.gvkey = f.gvkey)
      AND NOT EXISTS (SELECT 1 FROM dbo.gic_history t WHERE t.gvkey = f.gvkey);
    SET @h_ins += @@ROWCOUNT;

    -- gic_company
    INSERT dbo.gic_company (gvkey, conm, costat, fic, loc)
    SELECT f.gvkey, f.conm, f.costat, f.fic, f.loc FROM dbo.gic_company_frozen f
    WHERE NOT EXISTS (SELECT 1 FROM dbo.gic_company t WHERE t.gvkey = f.gvkey);
    SET @c_ins = @@ROWCOUNT;

    INSERT dbo.gic_company (gvkey, conm, costat, fic, loc)
    SELECT u.gvkey, COALESCE(c.conm, x.conm), COALESCE(c.costat, x.costat), COALESCE(c.fic, x.fic), COALESCE(c.loc, x.loc)
    FROM (SELECT gvkey FROM dbo.gic_history UNION SELECT gvkey FROM #cc) u
    LEFT JOIN #cc c ON c.gvkey = u.gvkey
    LEFT JOIN #comp x ON x.gvkey = u.gvkey
    WHERE NOT EXISTS (SELECT 1 FROM dbo.gic_company t WHERE t.gvkey = u.gvkey);
    SET @c_ins += @@ROWCOUNT;

    UPDATE t SET conm = n.conm, costat = n.costat, fic = n.fic, loc = n.loc
    FROM dbo.gic_company t
    CROSS APPLY (
        SELECT COALESCE(c.conm, x.conm, t.conm) conm, COALESCE(c.costat, x.costat, t.costat) costat,
               COALESCE(c.fic, x.fic, t.fic) fic, COALESCE(c.loc, x.loc, t.loc) loc
        FROM (SELECT 1 one) o
        LEFT JOIN #cc c ON c.gvkey = t.gvkey
        LEFT JOIN #comp x ON x.gvkey = t.gvkey
    ) n
    WHERE EXISTS (SELECT t.conm, t.costat, t.fic, t.loc EXCEPT SELECT n.conm, n.costat, n.fic, n.loc);
    SET @c_upd = @@ROWCOUNT;

    -- gic_security
    INSERT dbo.gic_security (gvkey, iid, cusip, dsci, exchg, isin, secstat, sedol, tic, tpci)
    SELECT f.gvkey, f.iid, f.cusip, f.dsci, f.exchg, f.isin, f.secstat, f.sedol, f.tic, f.tpci FROM dbo.gic_security_frozen f
    WHERE NOT EXISTS (SELECT 1 FROM dbo.gic_security t WHERE t.gvkey = f.gvkey AND t.iid = f.iid);
    SET @s_ins = @@ROWCOUNT;

    INSERT dbo.gic_security (gvkey, iid, cusip, dsci, exchg, isin, secstat, sedol, tic, tpci)
    SELECT u.gvkey, u.iid, COALESCE(c.cusip, i.cusip), COALESCE(c.dsci, s.dsci), COALESCE(c.exchg, s.exchg), COALESCE(c.isin, i.isin),
           COALESCE(c.secstat, s.secstat), c.sedol, COALESCE(c.tic, i.tic), COALESCE(c.tpci, s.tpci)
    FROM (SELECT gvkey, iid FROM #cs
          UNION SELECT s0.gvkey, s0.iid FROM #sec s0 JOIN dbo.gic_company g ON g.gvkey = s0.gvkey) u
    LEFT JOIN #cs c ON c.gvkey = u.gvkey AND c.iid = u.iid
    LEFT JOIN #sec s ON s.gvkey = u.gvkey AND s.iid = u.iid
    LEFT JOIN #idc i ON i.gvkey = u.gvkey AND i.iid = u.iid
    WHERE NOT EXISTS (SELECT 1 FROM dbo.gic_security t WHERE t.gvkey = u.gvkey AND t.iid = u.iid);
    SET @s_ins += @@ROWCOUNT;

    UPDATE t SET cusip = n.cusip, dsci = n.dsci, exchg = n.exchg, isin = n.isin, secstat = n.secstat, sedol = n.sedol, tic = n.tic, tpci = n.tpci
    FROM dbo.gic_security t
    CROSS APPLY (
        SELECT COALESCE(c.cusip, i.cusip, t.cusip) cusip, COALESCE(c.dsci, s.dsci, t.dsci) dsci, COALESCE(c.exchg, s.exchg, t.exchg) exchg,
               COALESCE(c.isin, i.isin, t.isin) isin, COALESCE(c.secstat, s.secstat, t.secstat) secstat, COALESCE(c.sedol, t.sedol) sedol,
               COALESCE(c.tic, i.tic, t.tic) tic, COALESCE(c.tpci, s.tpci, t.tpci) tpci
        FROM (SELECT 1 one) o
        LEFT JOIN #cs c ON c.gvkey = t.gvkey AND c.iid = t.iid
        LEFT JOIN #sec s ON s.gvkey = t.gvkey AND s.iid = t.iid
        LEFT JOIN #idc i ON i.gvkey = t.gvkey AND i.iid = t.iid
    ) n
    WHERE EXISTS (SELECT t.cusip, t.dsci, t.exchg, t.isin, t.secstat, t.sedol, t.tic, t.tpci
                  EXCEPT SELECT n.cusip, n.dsci, n.exchg, n.isin, n.secstat, n.sedol, n.tic, n.tpci);
    SET @s_upd = @@ROWCOUNT;

    INSERT dbo.gic_update_log VALUES (@now, @n_co, @n_cc, @n_cs, @n_comp, @n_sec, @n_idc, @h_ins, @h_upd, @h_del, @c_ins, @c_upd, @s_ins, @s_upd, @mismatch);

    IF @dry_run = 1 ROLLBACK TRANSACTION; ELSE COMMIT TRANSACTION;

    SELECT @now run_at, @dry_run dry_run, @h_ins h_ins, @h_upd h_upd, @h_del h_del, @c_ins c_ins, @c_upd c_upd, @s_ins s_ins, @s_upd s_upd, @mismatch gics_mismatch;

    IF @mismatch > @max_mismatch
        RAISERROR('usp_gic_update: %d companies differ between gic_ccompany and co_hgic. One of the subscriptions may have stopped.', 16, 1, @mismatch);
END
GO

-- AGENT JOB
USE [msdb]
GO
IF NOT EXISTS (SELECT 1 FROM msdb.dbo.sysjobs WHERE name = N'QMind GICS Tables')
BEGIN
    DECLARE @job uniqueidentifier;
    EXEC msdb.dbo.sp_add_job @job_name = N'QMind GICS Tables', @owner_login_name = N'xf_target', @notify_level_eventlog = 2, @job_id = @job OUTPUT;
    EXEC msdb.dbo.sp_add_jobstep @job_id = @job, @step_name = N'usp_gic_update', @subsystem = N'TSQL', @database_name = N'xf_target',
         @command = N'EXEC dbo.usp_gic_update', @on_fail_action = 2;
    EXEC msdb.dbo.sp_add_jobschedule @job_id = @job, @name = N'Daily 14:00', @freq_type = 4, @freq_interval = 1, @freq_subday_type = 1, @active_start_time = 140000;
    EXEC msdb.dbo.sp_add_jobserver @job_id = @job, @server_name = N'(local)';
END
EXEC msdb.dbo.sp_update_job @job_name = N'QMind GICS Tables',
     @description = N'xf_target gic_company/gic_security/gic_history daily update (db/sql/gic_tables.sql)';
GO
