/* ============================================================================
   dbo.spg_msci_wgt_Daily — MSCI 지수 구성 비중 조회 (ddt, isin, wgt)
   2026-10-29: 원천을 msci_bbu_csv (Job_Batch 가 매번 비우고 최근 ~8일만 채우는 임시 테이블)
               -> msci_idx_wgt (검증 통과분 이력, db/sql/msci_idx_wgt.sql) 로 변경.
   - 비중 기준: 다음날 (basis 'N') — 기존 msci_bbu_csv 와 같은 비중 (2026-10-19~28 4지수 행 단위 일치 확인)
   - 날짜마다 일별(freq 'D') 이 있으면 일별, 없으면 월말(freq 'M'):
     일별이 시작되기 전(2017-04-28 이전) 과 일별 검증에 실패한 날은 월말 값만 나온다.
   - msci_idx_wgt 에 없는 지수(BIC, CHINA, EM EASTERN EUROPE, AC ASIA ex JAPAN 등 8개) 는 기존처럼 msci_bbu_csv 에서 준다.
     msci_idx_wgt 에 있는 지수 = 990100 WORLD, 892400 ACWI, 891800 EM, 133333 CHINA A ONSHORE, 718708 CHINA A
     (718708 은 msci_bbu_csv 에 없던 지수 — 여기서만 나온다)
   - 입력 날짜는 'yyyy-mm-dd' / 'yyyymmdd' 모두 허용, 출력 컬럼·형식 (ddt date, isin, wgt float) 은 기존과 같다.
   - 정렬: 날짜, 같은 날짜 안은 MSCI 종목코드 순 (2026-10-29 성능 수정). 일별/월말을 UNION ALL 로 나누면
     두 PK seek 결과가 이미 이 순서라 merge 로 합치기만 한다. 이전의 'ORDER BY calc_date, isin' 은 결과 전체를
     정렬하다 tempdb 로 넘쳐 990100 11년치(378만 행)가 첫 행까지 14초 -> 1초.
   ============================================================================ */
USE [MSCIDB]
GO

CREATE OR ALTER PROCEDURE [dbo].[spg_msci_wgt_Daily]
	@start_date varchar(10),
	@end_date varchar(10),
	@msci_index_code varchar(6)
AS
BEGIN
	SET NOCOUNT ON;
	SET @start_date = REPLACE(@start_date, '-', '');
	SET @end_date = REPLACE(@end_date, '-', '');

	IF EXISTS (SELECT 1 FROM dbo.msci_idx_wgt WHERE msci_index_code = @msci_index_code)
		SELECT CAST(w.calc_date AS date) ddt, w.isin, w.wgt
		FROM (SELECT calc_date, msci_security_code, isin, wgt
		      FROM dbo.msci_idx_wgt
		      WHERE msci_index_code = @msci_index_code AND freq = 'D' AND basis = 'N'
		        AND calc_date BETWEEN @start_date AND @end_date AND isin IS NOT NULL
		      UNION ALL
		      SELECT m.calc_date, m.msci_security_code, m.isin, m.wgt
		      FROM dbo.msci_idx_wgt m
		      WHERE m.msci_index_code = @msci_index_code AND m.freq = 'M' AND m.basis = 'N'
		        AND m.calc_date BETWEEN @start_date AND @end_date AND m.isin IS NOT NULL
		        AND NOT EXISTS (SELECT 1 FROM dbo.msci_idx_wgt d
		                        WHERE d.msci_index_code = @msci_index_code AND d.freq = 'D'
		                          AND d.basis = 'N' AND d.calc_date = m.calc_date)) w
		ORDER BY w.calc_date, w.msci_security_code;
	ELSE
		-- msci_idx_wgt 에 아직 없는 지수: 기존 그대로
		SELECT ddt, isin, wgt
		FROM [MSCIDB].[dbo].[msci_bbu_csv]
		WHERE msci_index_code = @msci_index_code
		  AND ddt >= @start_date AND ddt <= @end_date
		  AND isin IS NOT NULL AND wgt IS NOT NULL;
END
GO

/* ---- 사용 예 ----
EXEC dbo.spg_msci_wgt_Daily '2026-10-01', '2026-10-28', '990100';   -- 일별
EXEC dbo.spg_msci_wgt_Daily '2015-01-01', '2015-12-31', '990100';   -- 일별 이전: 월말 12개

---- 이전 정의 (2024-03-13 수정본, 롤백용) ----
CREATE PROCEDURE [dbo].[spg_msci_wgt_Daily]
	@start_date varchar(10),
	@end_date varchar(10),
	@msci_index_code varchar(6)
as
begin
	set @start_date = replace(@start_date, '-', '')
	set @end_date = replace(@end_date, '-', '')

	SELECT
		ddt,
		--spg_ticker_iso,
		isin,
		wgt
	FROM [MSCIDB].[dbo].[msci_bbu_csv]
	WHERE msci_index_code = @msci_index_code
	and ddt>=@start_date and ddt<=@end_date
	and isin is not null and wgt is not null
end
*/
