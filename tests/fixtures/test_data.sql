SELECT *
  FROM [GLOBAL].[dbo].[clarifi_mxcn1a_afl]
  where fld in (
	'M_RETURN',
	'MXCN1A_WGT',
	'EBIT to Price (EBITP)', --Valuation
	'Book to Price (BP)', --Valuation
	'Dividend Growth (DIVIDENDGROWTH)', --Historical Growth
	'Sales Acceleration (SalesAcc)', --Historical Growth
	'Asset Adjusted Capital Investments (InvToAsset)', --Earnings Quality
	'Inventory Turnover (InvTurn)', --Earnings Quality
	'Net Income Stability (NIStab)', --Earnings Quality
	'Net Profit Margin (NetProfitMargin)' --Earnings Quality

  )
  and ddt > '2025-06-29' and ddt <'2026-01-01'
  and isin in (
	'CNE000000TK5',
	'CNE1000021D0',
	'CNE100002615',
	'CNE0000011D3',
	'CNE000000S01',
	'CNE000000ZC9',
	'CNE000000WF9',
	'CNE0000017M1',
	'CNE000001DL5',
	'CNE000001C81'
  )
 ORDER BY [fld] ASC; 