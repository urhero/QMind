-- ClariFI Security Master attribute $isin_ciq replacement (company_id, issue_number, value, start_date, end_date, id)
-- Parts 1-2: query from ClariFI Support (sec_idhist ISIN + sec_idcurrent for lines without ISIN history).
-- Parts 3-6: fill the years before sec_idhist ISIN history starts. US/CA-listed lines only have ISIN
-- history from 2013-01-01 (their earlier identity is kept as CUSIP), so without these parts about 40% of
-- MXWO weight before 2013 has no ISIN. Same rules as db/factor_direct.py backfill_isin_history (research.md 2.4).
-- No CTE / temp objects. Read-only. ClariFI's query column holds about 8,192 characters, so paste
-- clarifi_isin_attribute_paste.sql (same query without comments/indentation, 6.5k chars, identical output).
-- "first ISIN" of a line = its earliest sec_idhist ISIN row.

-- 1. sec_idhist ISIN history
select gvkey as company_id
      ,iid as issue_number
      ,itemvalue as value
      ,(case WHEN efffrom = '1991-01-31' THEN cast(NULL as datetime) ELSE efffrom END) as start_date
      ,(case WHEN effthru = '2900-01-01' THEN cast(NULL as datetime) ELSE effthru END) as end_date
      ,NULL as id
from sec_idhist
where item = 'ISIN'
UNION
-- 2. lines without ISIN history: current ISIN, open dates
select s.gvkey as company_id
      ,s.iid as issue_number
      ,s.itemvalue as value
      ,cast(NULL as datetime) as start_date
      ,cast(NULL as datetime) as end_date
      ,NULL as id
from sec_idcurrent s
where (s.gvkey + s.iid) NOT IN (select (gvkey + iid) from sec_idhist where item = 'ISIN')
  and s.item = 'isin'
UNION
-- 3. first ISIN is US/CA: every earlier CUSIP period -> country + CUSIP + Luhn check digit (letters A=10 .. Z=35)
select x.gvkey as company_id
      ,x.iid as issue_number
      ,x.base + cast((10 - sum(case when k.n % 2 = 1
                                    then (2 * cast(substring(x.ds, len(x.ds) - k.n + 1, 1) as int)) / 10
                                       + (2 * cast(substring(x.ds, len(x.ds) - k.n + 1, 1) as int)) % 10
                                    else cast(substring(x.ds, len(x.ds) - k.n + 1, 1) as int) end) % 10) % 10 as varchar(1)) as value
      ,x.start_date
      ,x.end_date
      ,NULL as id
from (
    select c.gvkey, c.iid, f.cc + upper(left(c.itemvalue, 9)) as base
          ,(select case when substring(f.cc + upper(left(c.itemvalue, 9)), p.n, 1) like '[0-9]'
                        then substring(f.cc + upper(left(c.itemvalue, 9)), p.n, 1)
                        else cast(ascii(substring(f.cc + upper(left(c.itemvalue, 9)), p.n, 1)) - 55 as varchar(2)) end
            from (values (1),(2),(3),(4),(5),(6),(7),(8),(9),(10),(11)) p(n)
            order by p.n
            for xml path('')) as ds
          ,(case WHEN c.efffrom = '1900-01-01' THEN cast(NULL as datetime) ELSE c.efffrom END) as start_date
          ,(case WHEN c.effthru < f.first_from THEN c.effthru ELSE dateadd(day, -1, f.first_from) END) as end_date
    from sec_idhist c
    join (select h.gvkey, h.iid, h.efffrom as first_from, left(h.itemvalue, 2) as cc
          from sec_idhist h
          where h.item = 'ISIN'
            and h.efffrom = (select min(h2.efffrom) from sec_idhist h2
                             where h2.item = 'ISIN' and h2.gvkey = h.gvkey and h2.iid = h.iid)) f
      on f.gvkey = c.gvkey and f.iid = c.iid and f.cc in ('US', 'CA')
    where c.item = 'CUSIP' and c.efffrom < f.first_from
      and upper(left(c.itemvalue, 9)) not like '%[^A-Z0-9]%' and len(c.itemvalue) >= 9
) x
cross join (select top 22 row_number() over (order by (select null)) as n from sys.all_objects) k
where k.n <= len(x.ds)
group by x.gvkey, x.iid, x.base, x.ds, x.start_date, x.end_date
UNION
-- 4. first ISIN is not US/CA (US-listed foreign issuers, IE/CH/PA...): the CUSIP that was in force when that ISIN
--    starts is the same security, so carry the first ISIN back over that CUSIP period
select c.gvkey as company_id
      ,c.iid as issue_number
      ,f.isin as value
      ,(case WHEN c.efffrom = '1900-01-01' THEN cast(NULL as datetime) ELSE c.efffrom END) as start_date
      ,dateadd(day, -1, f.first_from) as end_date
      ,NULL as id
from sec_idhist c
join (select h.gvkey, h.iid, h.efffrom as first_from, h.itemvalue as isin
      from sec_idhist h
      where h.item = 'ISIN'
        and h.efffrom = (select min(h2.efffrom) from sec_idhist h2
                         where h2.item = 'ISIN' and h2.gvkey = h.gvkey and h2.iid = h.iid)) f
  on f.gvkey = c.gvkey and f.iid = c.iid and left(f.isin, 2) not in ('US', 'CA')
where c.item = 'CUSIP' and c.efffrom < f.first_from and c.effthru >= f.first_from
UNION
-- 5. nothing above for the line (no earlier CUSIP, e.g. non-US lines whose history starts late):
--    open start for the first ISIN, only if no other line ever had that ISIN (excludes spin-offs like Corteva)
select f.gvkey as company_id
      ,f.iid as issue_number
      ,f.isin as value
      ,cast(NULL as datetime) as start_date
      ,dateadd(day, -1, f.first_from) as end_date
      ,NULL as id
from (select h.gvkey, h.iid, h.efffrom as first_from, h.itemvalue as isin
      from sec_idhist h
      where h.item = 'ISIN'
        and h.efffrom = (select min(h2.efffrom) from sec_idhist h2
                         where h2.item = 'ISIN' and h2.gvkey = h.gvkey and h2.iid = h.iid)) f
where f.first_from > '1991-01-31'
  and not exists (select 1 from sec_idhist c
                  where c.item = 'CUSIP' and c.gvkey = f.gvkey and c.iid = f.iid and c.efffrom < f.first_from
                    and (left(f.isin, 2) in ('US', 'CA') or c.effthru >= f.first_from))
  and not exists (select 1 from sec_idhist o
                  where o.item = 'ISIN' and o.itemvalue = f.isin and (o.gvkey <> f.gvkey or o.iid <> f.iid))
UNION
-- 6. MSCI index data (Xpressfeed trial, 2005-06 ~ 2025-07): ISIN -> trading item -> line, only before parts 1/4/5 start
--    giving the same ISIN to that line (late ISIN history such as Vivendi/Nordea/TE, lines without any ISIN history
--    such as Sunoco, old ISINs before re-domiciling such as BMG/KYG, Anglo American's old GB ISIN)
select m.gvkey as company_id
      ,m.iid as issue_number
      ,m.isin as value
      ,m.start_date
      ,(case WHEN m.cover_from IS NOT NULL AND (m.end_date IS NULL OR m.end_date >= m.cover_from)
             THEN dateadd(day, -1, m.cover_from) ELSE m.end_date END) as end_date
      ,NULL as id
from (
    select q.*
          -- first day the same line already has this ISIN: part 4 (CUSIP span of a non-US/CA first ISIN) else sec_idhist
          ,(case WHEN q.isin = q.line_first_isin AND left(q.isin, 2) not in ('US', 'CA') AND q.span_from IS NOT NULL
                 THEN q.span_from ELSE q.first_hist END) as cover_from
    from (
        select g.gvkey, g.iid, i.identifierValue as isin
              ,cast(i.fromDate as datetime) as start_date
              ,cast(i.toDate as datetime) as end_date
              ,(select min(h.efffrom) from sec_idhist h
                where h.item = 'ISIN' and h.gvkey = g.gvkey and h.iid = g.iid and h.itemvalue = i.identifierValue) as first_hist
              ,(select min(h.efffrom) from sec_idhist h
                where h.item = 'ISIN' and h.gvkey = g.gvkey and h.iid = g.iid) as line_first
              ,(select top 1 h.itemvalue from sec_idhist h
                where h.item = 'ISIN' and h.gvkey = g.gvkey and h.iid = g.iid order by h.efffrom) as line_first_isin
              ,(select min(c.efffrom) from sec_idhist c
                where c.item = 'CUSIP' and c.gvkey = g.gvkey and c.iid = g.iid
                  and c.efffrom < (select min(h.efffrom) from sec_idhist h where h.item = 'ISIN' and h.gvkey = g.gvkey and h.iid = g.iid)
                  and c.effthru >= (select min(h.efffrom) from sec_idhist h where h.item = 'ISIN' and h.gvkey = g.gvkey and h.iid = g.iid)) as span_from
        from MSCIConstituentIdentifier i
        join MSCIConstituent mc on mc.constituentID = i.constituentID
        join ciqGvkeyIID g on g.objectId = mc.tradingItemId
        where i.identifierTypeID = 3
    ) q
) m
where (m.cover_from IS NULL OR m.start_date < m.cover_from)
  -- part 5 already opens this first ISIN (whole past covered)
  and not (m.line_first_isin IS NOT NULL and m.isin = m.line_first_isin and m.line_first > '1991-01-31'
           and not exists (select 1 from sec_idhist c
                           where c.item = 'CUSIP' and c.gvkey = m.gvkey and c.iid = m.iid and c.efffrom < m.line_first
                             and (left(m.isin, 2) in ('US', 'CA') or c.effthru >= m.line_first))
           and not exists (select 1 from sec_idhist o
                           where o.item = 'ISIN' and o.itemvalue = m.isin and (o.gvkey <> m.gvkey or o.iid <> m.iid)))
  -- sec_idhist says this ISIN (or its CUSIP body) belongs to another company: trust sec_idhist, skip MSCI
  and not exists (select 1 from (select itemvalue as isin, min(gvkey) as g1, max(gvkey) as g2
                                 from sec_idhist where item = 'ISIN' group by itemvalue) io
                  where io.isin = m.isin and (io.g1 <> m.gvkey or io.g2 <> m.gvkey))
  and not exists (select 1 from (select upper(left(itemvalue, 9)) as body, min(gvkey) as g1, max(gvkey) as g2
                                 from sec_idhist where item = 'CUSIP' group by upper(left(itemvalue, 9))) cb
                  where left(m.isin, 2) in ('US', 'CA') and cb.body = substring(m.isin, 3, 9)
                    and (cb.g1 <> m.gvkey or cb.g2 <> m.gvkey))
  -- part 3 already builds this US/CA ISIN from the line's CUSIP
  and not exists (select 1 from sec_idhist c
                  where c.item = 'CUSIP' and c.gvkey = m.gvkey and c.iid = m.iid
                    and m.line_first_isin IS NOT NULL and left(m.line_first_isin, 2) = left(m.isin, 2) and left(m.isin, 2) in ('US', 'CA')
                    and upper(left(c.itemvalue, 9)) = substring(m.isin, 3, 9))
