# -*- coding: utf-8 -*-
"""ClariFI 없이 S&P 원천에서 직접 적재 (db/factor_direct.py, 2026-10-30)."""
import numpy as np
import pandas as pd
import pytest

from db.factor_direct import (
    assemble_rows,
    check_coverage,
    backfill_isin_history,
    isin_check_digit,
    compare_with_table,
    insert_tuples,
    latest_basket,
    map_fields,
    msci_fallback_lines,
    month_returns,
    pick_ticker,
    safe_table,
    select_lines,
    validate_month_end,
)

D = pd.Timestamp("2026-09-30")


def _hist(*rows):
    """(gvkey, iid, isin, efffrom, effthru) - None = 열린 기간."""
    df = pd.DataFrame(rows, columns=["gvkey", "iid", "isin", "efffrom", "effthru"])
    df["efffrom"] = pd.to_datetime(df["efffrom"])
    df["effthru"] = pd.to_datetime(df["effthru"])
    return df


def _primary(*rows):
    """(gvkey, iid, item, efffrom, effthru)."""
    df = pd.DataFrame(rows, columns=["gvkey", "iid", "item", "efffrom", "effthru"])
    df["efffrom"] = pd.to_datetime(df["efffrom"])
    df["effthru"] = pd.to_datetime(df["effthru"])
    return df


def _company(**loc):
    return pd.DataFrame({"gvkey": list(loc), "loc": list(loc.values())})


def _security(*rows):
    """(gvkey, iid, exchg, secstat)."""
    return pd.DataFrame(rows, columns=["gvkey", "iid", "exchg", "secstat"])


NO_PRIMARY = _primary()
NO_SEEN = pd.Series(dtype="datetime64[ns]")


def _pick(isins, hist, primary=NO_PRIMARY, company=None, security=None, seen=NO_SEEN):
    company = company if company is not None else pd.DataFrame({"gvkey": [], "loc": []})
    security = security if security is not None else pd.DataFrame({"gvkey": [], "iid": [], "exchg": [], "secstat": []})
    out = select_lines(pd.Series(isins), D, hist, primary, company, security, seen)
    return dict(zip(out["isin"], out["gvkeyiid"]))


class TestSelectLines:
    def test_single_candidate_taken_without_primary_flag(self):
        # 중국 종목 다수는 primary 플래그가 없다 - 플래그를 요구하면 96% 가 사라졌던 실제 사례
        assert _pick(["CN1"], _hist(("000001", "01W", "CN1", None, None))) == {"CN1": "00000101W"}

    def test_isin_uses_holder_on_that_date(self):
        hist = _hist(
            ("000001", "01", "US1", "2010-01-01", "2020-12-31"),
            ("000002", "01", "US1", "2021-01-01", None),
        )
        assert _pick(["US1"], hist) == {"US1": "00000201"}

    def test_isin_without_candidate_is_absent(self):
        assert _pick(["XX9"], _hist(("000001", "01", "US1", None, None))) == {}

    def test_previously_loaded_line_beats_primary_rule(self):
        # GFL: 소재지는 USA 라 규칙은 01 을 고르지만 테이블에는 내내 01C 로 쌓여 있다
        hist = _hist(("000001", "01", "CA1", None, None), ("000001", "01C", "CA1", None, None))
        primary = _primary(("000001", "01", "PRIHISTUSA", None, None), ("000001", "01C", "PRIHISTCAN", None, None))
        security = _security(("000001", "01", 11, "A"), ("000001", "01C", 7, "A"))
        seen = pd.Series({"00000101C": pd.Timestamp("2026-08-31")})
        assert _pick(["CA1"], hist, primary, _company(**{"000001": "USA"}), security, seen) == {"CA1": "00000101C"}

    def test_most_recently_loaded_line_wins(self):
        hist = _hist(("000001", "01W", "GB1", None, None), ("000001", "02W", "GB1", None, None))
        security = _security(("000001", "01W", 194, "A"), ("000001", "02W", 194, "A"))
        seen = pd.Series({"00000101W": pd.Timestamp("2020-01-31"), "00000102W": pd.Timestamp("2026-08-31")})
        assert _pick(["GB1"], hist, security=security, seen=seen) == {"GB1": "00000102W"}

    def test_inactive_loaded_line_does_not_beat_active_line(self):
        # 상장 폐지된 옛 라인을 계속 고르면 값이 비어 종목이 통째로 사라진다
        hist = _hist(("000001", "01W", "GB1", None, None), ("000001", "02W", "GB1", None, None))
        security = _security(("000001", "01W", 194, "I"), ("000001", "02W", 194, "A"))
        seen = pd.Series({"00000101W": pd.Timestamp("2026-08-31")})
        assert _pick(["GB1"], hist, security=security, seen=seen) == {"GB1": "00000102W"}

    def test_domicile_primary_beats_other_region_primary(self):
        # 오스트리아 회사: 미국 OTC 라인의 prihistusa 보다 소재지(ROW) primary 가 우선
        hist = _hist(("000001", "01", "AT1", None, None), ("000001", "01W", "AT1", None, None))
        primary = _primary(("000001", "01", "PRIHISTUSA", None, None), ("000001", "01W", "PRIHISTROW", None, None))
        security = _security(("000001", "01", 11, "A"), ("000001", "01W", 273, "A"))
        assert _pick(["AT1"], hist, primary, _company(**{"000001": "AUT"}), security) == {"AT1": "00000101W"}

    def test_without_domicile_primary_order_is_usa_can_row(self):
        hist = _hist(("000001", "01", "JE1", None, None), ("000001", "01C", "JE1", None, None), ("000001", "01W", "JE1", None, None))
        primary = _primary(
            ("000001", "01", "PRIHISTCAN", None, None),
            ("000001", "01C", "PRIHISTUSA", None, None),
            ("000001", "01W", "PRIHISTROW", None, None),
        )
        # 소재지 USA 인데 PRIHISTUSA 는 01C -> 소재지 primary 로 01C
        security = _security(("000001", "01", 7, "A"), ("000001", "01C", 7, "A"), ("000001", "01W", 7, "A"))
        assert _pick(["JE1"], hist, primary, _company(**{"000001": "USA"}), security) == {"JE1": "00000101C"}
        # 소재지 CAN 이고 PRIHISTCAN 은 01 -> 01; 소재지 primary 가 없으면 USA 다음 CAN 다음 ROW
        no_can = primary[primary["item"] != "PRIHISTCAN"]
        assert _pick(["JE1"], hist, no_can, _company(**{"000001": "CAN"}), security) == {"JE1": "00000101C"}

    def test_expired_primary_flag_is_ignored(self):
        hist = _hist(("000001", "01", "AT1", None, None), ("000001", "01W", "AT1", None, None))
        primary = _primary(("000001", "01", "PRIHISTROW", "2000-01-01", "2010-12-31"), ("000001", "01W", "PRIHISTROW", "2011-01-01", None))
        security = _security(("000001", "01", 11, "A"), ("000001", "01W", 273, "A"))
        assert _pick(["AT1"], hist, primary, _company(**{"000001": "AUT"}), security) == {"AT1": "00000101W"}

    def test_otc_line_loses_when_no_primary_decides(self):
        # Rio Tinto AU 라인: 플래그 없는 두 라인 중 미국 OTC(거래소 19) 를 고르면 안 된다
        hist = _hist(("000001", "02", "AU1", None, None), ("000001", "15W", "AU1", None, None))
        security = _security(("000001", "02", 19, "A"), ("000001", "15W", 106, "A"))
        assert _pick(["AU1"], hist, company=_company(**{"000001": "GBR"}), security=security) == {"AU1": "00000115W"}


class TestMonthReturns:
    @staticmethod
    def _px(*rows):
        return pd.DataFrame(rows, columns=["gvkeyiid", "datadate", "prccd", "ajexdi", "curcdd"]).assign(
            datadate=lambda d: pd.to_datetime(d["datadate"])
        )

    @staticmethod
    def _fx(*rows):
        return pd.DataFrame(rows, columns=["tocurd", "datadate", "exratd"]).assign(datadate=lambda d: pd.to_datetime(d["datadate"]))

    def test_local_price_converted_to_usd_at_each_date(self):
        # usd0 = 100 * 1.3/9.1 = 100/7, usd1 = 110 * 1.3/8.45 = 110*2/13 -> 1540/1300 - 1
        px1 = self._px(("A", "2026-09-30", 110.0, 1.0, "CNY"))
        px0 = self._px(("A", "2026-08-31", 100.0, 1.0, "CNY"))
        fx = self._fx(("USD", "2026-08-31", 1.3), ("CNY", "2026-08-31", 9.1), ("USD", "2026-09-30", 1.3), ("CNY", "2026-09-30", 8.45))
        assert month_returns(px1, px0, fx)["A"] == pytest.approx(240 / 1300)

    def test_split_adjustment_applied(self):
        # 2:1 분할: 50/2 = 25 vs 20/1 -> +25%
        px1 = self._px(("A", "2026-09-30", 50.0, 2.0, "USD"))
        px0 = self._px(("A", "2026-08-31", 20.0, 1.0, "USD"))
        fx = self._fx(("USD", "2026-08-31", 1.3), ("USD", "2026-09-30", 1.3))
        assert month_returns(px1, px0, fx)["A"] == pytest.approx(0.25)

    def test_stale_price_uses_fx_of_its_own_date(self):
        # 거래정지: 전월 가격이 3월 말 값이면 환율도 3월 말 (월말 환율을 쓰면 718708 에서 최대 4% 틀림)
        px1 = self._px(("A", "2026-09-30", 100.0, 1.0, "CNY"))
        px0 = self._px(("A", "2026-03-31", 100.0, 1.0, "CNY"))
        fx = self._fx(("USD", "2026-03-31", 1.4), ("CNY", "2026-03-31", 7.0), ("USD", "2026-08-31", 1.3), ("CNY", "2026-08-31", 9.1), ("USD", "2026-09-30", 1.3), ("CNY", "2026-09-30", 6.5))
        # usd1 = 100 * 1.3/6.5 = 20, usd0 = 100 * 1.4/7 = 20 -> 0
        assert month_returns(px1, px0, fx)["A"] == pytest.approx(0.0)

    def test_new_listing_without_previous_price_is_nan(self):
        px1 = self._px(("A", "2026-09-30", 100.0, 1.0, "USD"), ("B", "2026-09-30", 11.0, 1.0, "USD"))
        px0 = self._px(("B", "2026-08-31", 10.0, 1.0, "USD"))
        fx = self._fx(("USD", "2026-08-31", 1.3), ("USD", "2026-09-30", 1.3))
        out = month_returns(px1, px0, fx)
        assert np.isnan(out["A"])
        assert out["B"] == pytest.approx(0.1)


class TestPickTicker:
    @staticmethod
    def _sym(*rows):
        return pd.DataFrame(rows, columns=["gvkeyiid", "symbolValue", "symbolStartDate", "activeFlag"]).assign(
            symbolStartDate=lambda d: pd.to_datetime(d["symbolStartDate"])
        )

    def test_latest_active_symbol_wins(self):
        sym = self._sym(("A", "OLD", "2000-01-01", 1), ("A", "NEW", "2020-01-01", 1), ("A", "NEWER", "2025-01-01", 0))
        assert pick_ticker(sym, pd.Series(dtype=object))["A"] == "NEW"

    def test_current_tic_when_no_active_symbol(self):
        sym = self._sym(("A", "DEAD", "2000-01-01", 0))
        assert pick_ticker(sym, pd.Series({"A": "TIC"}))["A"] == "TIC"

    def test_last_symbol_when_nothing_else(self):
        sym = self._sym(("A", "D1", "2000-01-01", 0), ("A", "D2", "2010-01-01", 0))
        assert pick_ticker(sym, pd.Series(dtype=object))["A"] == "D2"


class TestMapFields:
    FACTORS = pd.DataFrame(
        {
            "factorId": [954, 7, 1201],
            "factorName": ["3M Revision", "Current Ratio Chg", "Net Arbitrage Trading"],
            "factorAbbreviation": ["Rev3MFY1C", "Chg1YCACL", "IO_NAT"],
        }
    )

    def test_abbreviation_with_and_without_space(self):
        mapped, unmapped = map_fields(["3M Revision (Rev3MFY1C)", "Current Ratio Chg(Chg1YCACL)"], self.FACTORS)
        assert dict(zip(mapped["fld"], mapped["factorId"])) == {"3M Revision (Rev3MFY1C)": 954, "Current Ratio Chg(Chg1YCACL)": 7}
        assert unmapped == []

    def test_placeholder_abbreviation_falls_back_to_factor_name(self):
        mapped, _ = map_fields(["Net Arbitrage Trading (1)"], self.FACTORS)
        assert mapped["factorId"].tolist() == [1201]

    def test_renamed_field_keeps_only_latest_name(self):
        # 1M Price Momentum (PM1M) -> 1M Price Reversal (PM1M) 개명: 둘 다 쓰면 같은 종목-약어 행이 중복된다
        mapped, unmapped = map_fields(["Old Name (Rev3MFY1C)", "New Name (Rev3MFY1C)"], self.FACTORS)
        assert mapped["fld"].tolist() == ["New Name (Rev3MFY1C)"]
        assert unmapped == []

    def test_real_abbreviation_beats_placeholder_for_same_factor(self):
        # 'Net Arbitrage Trading (1)' 은 download 가 약어 '1' 로 파싱해 버린다 - 더 새 이름이어도 이기면 안 된다
        mapped, _ = map_fields(["Net Arbitrage Trading (IO_NAT)", "Net Arbitrage Trading (1)"], self.FACTORS)
        assert mapped["fld"].tolist() == ["Net Arbitrage Trading (IO_NAT)"]

    def test_case_only_variants_both_kept(self):
        # ClariFI 는 'Ownership Level' / 'ownership level' 두 철자를 같은 값으로 둘 다 쓴다
        mapped, _ = map_fields(["Chg of Level (Rev3MFY1C)", "Chg of level (Rev3MFY1C)"], self.FACTORS)
        assert sorted(mapped["fld"]) == ["Chg of Level (Rev3MFY1C)", "Chg of level (Rev3MFY1C)"]
        assert set(mapped["factorId"]) == {954}

    def test_return_and_weight_are_not_source_factors(self):
        mapped, unmapped = map_fields(["M_RETURN", "MXWO_WGT"], self.FACTORS)
        assert mapped.empty
        assert unmapped == []

    def test_unknown_field_reported(self):
        mapped, unmapped = map_fields(["Nope (XYZ)"], self.FACTORS)
        assert mapped.empty
        assert unmapped == ["Nope (XYZ)"]


class TestAssembleRows:
    LINES = pd.DataFrame({"gvkeyiid": ["A", "B"], "isin": ["IA", "IB"], "wgt": [0.6, 0.4]})
    ATTRS = pd.DataFrame(
        {"gvkeyiid": ["A", "B"], "ticker": ["TA", "TB"], "isin": ["CA", None], "sec": ["Energy", None], "country": ["USA", "CHN"]}
    )
    FIELD_MAP = pd.DataFrame({"fld": ["F1 (F1)", "F2 (F2)"], "factorId": [1, 2]})
    VALUES = pd.DataFrame({"gvkeyiid": ["A"], "factorId": [1], "factorValue": [1.5]})
    RETURNS = pd.Series({"A": 0.02})

    def _rows(self):
        return assemble_rows(self.LINES, ["F1 (F1)", "F2 (F2)", "M_RETURN", "XX_WGT"], self.FIELD_MAP, self.VALUES, self.RETURNS, self.ATTRS, D)

    def _val(self, gvkeyiid, fld):
        r = self._rows()
        return r[(r["gvkeyiid"] == gvkeyiid) & (r["fld"] == fld)]["val"].iloc[0]

    def test_every_security_field_pair_has_a_row(self):
        # ClariFI 와 같은 교차 형태: 값이 없어도 NULL 행
        r = self._rows()
        assert len(r) == 8
        assert not r.duplicated(["gvkeyiid", "fld"]).any()
        assert list(r.columns) == ["gvkeyiid", "ticker", "isin", "ddt", "val", "fld", "sec", "country"]

    def test_values_routed_to_their_fields(self):
        assert self._val("A", "F1 (F1)") == 1.5
        assert np.isnan(self._val("A", "F2 (F2)"))
        assert np.isnan(self._val("B", "F1 (F1)"))
        assert self._val("A", "M_RETURN") == 0.02
        assert np.isnan(self._val("B", "M_RETURN"))
        assert self._val("B", "XX_WGT") == 0.4

    def test_missing_sector_becomes_undefined(self):
        # download 가 sec == 'Undefined' 를 거른다. NULL 이면 그 필터를 빠져나가 섹터 집계가 깨진다
        r = self._rows()
        assert set(r[r["gvkeyiid"] == "B"]["sec"]) == {"Undefined"}

    def test_isin_falls_back_to_basket_isin(self):
        r = self._rows()
        assert set(r[r["gvkeyiid"] == "A"]["isin"]) == {"CA"}
        assert set(r[r["gvkeyiid"] == "B"]["isin"]) == {"IB"}

    def test_ddt_is_month_end(self):
        assert set(self._rows()["ddt"]) == {D}


class TestCompareWithTable:
    @staticmethod
    def _built(*rows):
        return pd.DataFrame(rows, columns=["gvkeyiid", "ticker", "isin", "ddt", "val", "fld", "sec", "country"])

    @staticmethod
    def _table(*rows):
        return pd.DataFrame(rows, columns=["gvkeyiid", "ticker", "isin", "ddt", "val", "fld", "sec", "country", "updated_at"]).assign(
            updated_at=lambda d: pd.to_datetime(d["updated_at"])
        )

    LINES = pd.DataFrame({"gvkeyiid": ["A", "NEW"], "isin": ["IA", "IN"], "wgt": [0.75, 0.25]})

    def test_latest_export_is_the_one_compared(self):
        # 같은 달을 두 번 내보내면 fetch_snp 처럼 updated_at 최신만 본다
        built = self._built(("A", "T", "I", D, 1.0, "F", "S", "C"))
        table = self._table(("A", "T", "I", D, 9.0, "F", "S", "C", "2026-10-01"), ("A", "T", "I", D, 1.0, "F", "S", "C", "2026-10-02"))
        r = compare_with_table(built, table, self.LINES)
        assert (r["both"], r["equal"], r["differ"]) == (1, 1, 0)

    def test_value_differences_counted_by_kind(self):
        built = self._built(
            ("A", "T", "I", D, 1.0, "F1", "S", "C"),
            ("A", "T", "I", D, 2.0, "F2", "S", "C"),
            ("A", "T", "I", D, np.nan, "F3", "S", "C"),
            ("A", "T", "I", D, 4.0, "F4", "S", "C"),
            ("A", "T", "I", D, np.nan, "F5", "S", "C"),
        )
        table = self._table(
            ("A", "T", "I", D, 1.0, "F1", "S", "C", "2026-10-01"),
            ("A", "T", "I", D, 2.5, "F2", "S", "C", "2026-10-01"),
            ("A", "T", "I", D, 3.0, "F3", "S", "C", "2026-10-01"),
            ("A", "T", "I", D, np.nan, "F4", "S", "C", "2026-10-01"),
            ("A", "T", "I", D, np.nan, "F5", "S", "C", "2026-10-01"),
        )
        r = compare_with_table(built, table, self.LINES)
        assert (r["both"], r["equal"], r["differ"]) == (2, 1, 1)
        assert r["table_value_direct_null"] == 1
        assert r["direct_value_table_null"] == 1

    def test_names_only_on_one_side_listed_with_basket_weight(self):
        built = self._built(("A", "T", "I", D, 1.0, "F", "S", "C"), ("NEW", "T", "I", D, 1.0, "F", "S", "C"))
        table = self._table(("A", "T", "I", D, 1.0, "F", "S", "C", "2026-10-01"), ("OLD", "T", "I", D, 1.0, "F", "S", "C", "2026-10-01"))
        r = compare_with_table(built, table, self.LINES)
        assert r["direct_only"] == [("NEW", pytest.approx(0.25))]
        assert r["table_only"] == ["OLD"]
        assert r["direct_only_weight"] == pytest.approx(0.25)

    def test_attribute_mismatches_counted_per_security(self):
        built = self._built(("A", "T2", "I", D, 1.0, "F", "S2", "C"), ("A", "T2", "I", D, 1.0, "G", "S2", "C"))
        table = self._table(("A", "T1", "I", D, 1.0, "F", "S1", "C", "2026-10-01"), ("A", "T1", "I", D, 1.0, "G", "S1", "C", "2026-10-01"))
        r = compare_with_table(built, table, self.LINES)
        assert r["attr_mismatch"] == {"ticker": 1, "isin": 0, "sec": 1, "country": 0}

    def test_empty_table_reports_nothing_to_compare(self):
        # ClariFI 가 아직 안 내보낸 달도 빌드 결과는 볼 수 있어야 한다
        built = self._built(("A", "T", "I", D, 1.0, "F", "S", "C"))
        r = compare_with_table(built, self._table(), self.LINES)
        assert r["table_rows"] == 0
        assert r["both"] == 0


class TestGuards:
    def test_month_end_accepted_and_returned_as_timestamp(self):
        assert validate_month_end("2026-10-31") == pd.Timestamp("2026-10-31")

    def test_mid_month_rejected(self):
        # 15 일로 돌리면 원천에 행이 없어 조용히 빈 달이 쌓인다
        with pytest.raises(ValueError):
            validate_month_end("2026-10-15")

    def test_latest_basket_takes_last_date_on_or_before_month_end(self):
        b = pd.DataFrame({"ddt": ["2026-10-29", "2026-10-30", "2026-10-30"], "isin": ["X", "A", "B"], "wgt": [1.0, 0.5, 0.5]})
        out = latest_basket(b, pd.Timestamp("2026-10-31"))
        assert sorted(out["isin"]) == ["A", "B"]

    @pytest.mark.parametrize("basket_date", ["2026-10-23", "2026-10-01"])
    def test_stale_basket_rejected(self, basket_date):
        # 바스켓이 멈췄는데 옛 구성으로 적재되면 안 된다
        b = pd.DataFrame({"ddt": [basket_date], "isin": ["A"], "wgt": [1.0]})
        with pytest.raises(ValueError):
            latest_basket(b, pd.Timestamp("2026-10-31"))

    def test_empty_basket_rejected(self):
        with pytest.raises(ValueError):
            latest_basket(pd.DataFrame({"ddt": [], "isin": [], "wgt": []}), pd.Timestamp("2026-10-31"))

    def test_coverage_passes_when_source_has_the_month(self):
        lines = pd.DataFrame({"gvkeyiid": list("ABCDEFGHIJ")})
        values = pd.DataFrame({"gvkeyiid": list("ABCDEFGHI"), "factorId": 1, "factorValue": 1.0})
        px = pd.DataFrame({"gvkeyiid": list("ABCDEFGHIJ"), "datadate": pd.Timestamp("2026-10-30")})
        check_coverage(lines, values, px, pd.Timestamp("2026-10-31"))

    def test_source_month_not_loaded_yet_rejected(self):
        # 월초에 일찍 돌리면 월간 테이블에 새 달이 없다 - 전 종목 NULL 로 쌓이면 안 된다
        lines = pd.DataFrame({"gvkeyiid": list("AB")})
        values = pd.DataFrame({"gvkeyiid": [], "factorId": [], "factorValue": []})
        px = pd.DataFrame({"gvkeyiid": list("AB"), "datadate": pd.Timestamp("2026-10-30")})
        with pytest.raises(RuntimeError):
            check_coverage(lines, values, px, pd.Timestamp("2026-10-31"))

    def test_null_only_source_rows_count_as_missing(self):
        lines = pd.DataFrame({"gvkeyiid": list("AB")})
        values = pd.DataFrame({"gvkeyiid": list("AB"), "factorId": 1, "factorValue": [np.nan, np.nan]})
        px = pd.DataFrame({"gvkeyiid": list("AB"), "datadate": pd.Timestamp("2026-10-30")})
        with pytest.raises(RuntimeError):
            check_coverage(lines, values, px, pd.Timestamp("2026-10-31"))

    def test_prices_not_updated_rejected(self):
        # sec_dprc 가 밀려 있으면 '마지막 가격' 규칙이 M_RETURN 을 조용히 0 으로 만든다
        lines = pd.DataFrame({"gvkeyiid": list("AB")})
        values = pd.DataFrame({"gvkeyiid": list("AB"), "factorId": 1, "factorValue": 1.0})
        px = pd.DataFrame({"gvkeyiid": list("AB"), "datadate": pd.Timestamp("2026-09-30")})
        with pytest.raises(RuntimeError):
            check_coverage(lines, values, px, pd.Timestamp("2026-10-31"))

    def test_suspended_minority_tolerated(self):
        lines = pd.DataFrame({"gvkeyiid": list("ABCDEFGHIJ")})
        values = pd.DataFrame({"gvkeyiid": list("ABCDEFGHIJ"), "factorId": 1, "factorValue": 1.0})
        px = pd.DataFrame({"gvkeyiid": list("ABCDEFGHIJ"), "datadate": [pd.Timestamp("2026-10-30")] * 9 + [pd.Timestamp("2025-01-31")]})
        check_coverage(lines, values, px, pd.Timestamp("2026-10-31"))


class TestWritePath:
    def test_identifier_guard(self):
        assert safe_table("clarifi_mxwo_afl") == "clarifi_mxwo_afl"
        assert safe_table("#direct_test") == "#direct_test"
        with pytest.raises(ValueError):
            safe_table("t; drop table x")

    def test_missing_values_become_sql_null(self):
        df = pd.DataFrame(
            {
                "gvkeyiid": ["A"],
                "ticker": [None],
                "isin": [np.nan],
                "ddt": [pd.Timestamp("2026-09-30")],
                "val": [np.nan],
                "fld": ["F"],
                "sec": ["Undefined"],
                "country": ["USA"],
            }
        )
        assert insert_tuples(df) == [("A", None, None, pd.Timestamp("2026-09-30").date(), None, "F", "Undefined", "USA")]


def test_isin_check_digit():
    # 실제 ISIN: Apple, JPMorgan, Shopify(CA), 문자 포함 본체
    assert isin_check_digit("US037833100") == "5"
    assert isin_check_digit("US46625H100") == "5"
    assert isin_check_digit("CA82509L107") == "6"  # Shopify CA82509L1076
    assert isin_check_digit("US16161A108") == "8"


def _cusip(*rows):
    """(gvkey, iid, cusip, efffrom, effthru)."""
    df = pd.DataFrame(rows, columns=["gvkey", "iid", "cusip", "efffrom", "effthru"])
    df["efffrom"] = pd.to_datetime(df["efffrom"])
    df["effthru"] = pd.to_datetime(df["effthru"])
    return df


def test_backfill_isin_history_rules():
    isin = _hist(
        ("001690", "01", "US0378331005", "2013-01-01", None),  # 1. US: CUSIP 에서 ISIN 생성
        ("002968", "01", "US46625H1005", "2013-01-01", None),  # 1. CUSIP 이 바뀐 과거도
        ("010000", "01", "IE00B4BNMY34", "2013-01-01", None),  # 2. 외국 ISIN: 이어진 CUSIP 구간만
        ("020000", "01W", "FR0000000001", "2008-10-16", None),  # 4. 다른 라인이 안 쓴 ISIN -> 열린 시작
        ("030000", "01", "US22052L1044", "2026-10-01", None),  # 4. 제외: 다른 라인(030001) 이 쓴 ISIN
        ("030001", "01", "US22052L1044", "2019-05-24", "2026-09-30"),
    )
    cusip = _cusip(
        ("001690", "01", "037833100", "1979-12-31", None),
        ("002968", "01", "16161A108", "1996-04-01", "2001-01-01"),
        ("002968", "01", "46625H100", "2001-01-02", None),
        ("010000", "01", "G1151C101", "2009-09-01", None),
        ("010000", "01", "G1150G111", "2001-07-19", "2009-08-31"),  # 3. 국적 이전 전 BMG ISIN (바스켓에 있을 때)
        ("030001", "01", "22052L104", "2019-05-24", None),
    )
    out = backfill_isin_history(isin, cusip, ["BMG1150G1116"])
    got = {(r.gvkey, r.isin, None if pd.isna(r.efffrom) else str(r.efffrom.date()), str(r.effthru.date())) for r in out.itertuples()}
    assert got == {
        ("001690", "US0378331005", "1979-12-31", "2012-12-31"),
        ("002968", "US16161A1088", "1996-04-01", "2001-01-01"),
        ("002968", "US46625H1005", "2001-01-02", "2012-12-31"),
        ("010000", "IE00B4BNMY34", "2009-09-01", "2012-12-31"),
        ("010000", "BMG1150G1116", "2001-07-19", "2009-08-31"),
        ("020000", "FR0000000001", None, "2008-10-15"),
    }


def test_backfill_cins_on_us_line():
    # Tim Hortons: 첫 ISIN 이 CA 라 규칙 1 은 CA 를 만들지만, 바스켓의 캐나다 이전 전 US ISIN 도 붙어야 한다
    isin = _hist(("166366", "01", "CA88706M1036", "2013-01-01", None))
    cusip = _cusip(("166366", "01", "88706M103", "2001-12-31", None))
    out = backfill_isin_history(isin, cusip, ["US88706M1036"])
    assert set(out["isin"]) == {"CA88706M103" + isin_check_digit("CA88706M103"), "US88706M1036"}


def test_msci_fallback_lines_by_date():
    msci = _hist(
        ("101264", "01W", "FR0000127771", "2005-06-01", None),
        ("017726", "402", "IE00B3QN1M21", "2009-06-01", "2012-12-31"),
        ("999999", "01", "IE00B3QN1M21", "2013-01-01", None),
    )
    out = msci_fallback_lines(pd.Series(["FR0000127771", "IE00B3QN1M21", "XX0000000000"]), pd.Timestamp("2010-12-31"), msci)
    assert out.values.tolist() == [["FR0000127771", "101264" + "01W"], ["IE00B3QN1M21", "017726402"]]


def test_msci_fallback_skips_isin_owned_by_other_company():
    msci = _hist(("004094", "01", "US6153691059", "2005-06-30", None))
    known = _hist(("012345", "01", "US6153691059", "2001-01-01", "2004-12-31"))
    assert msci_fallback_lines(pd.Series(["US6153691059"]), pd.Timestamp("2010-12-31"), msci, known).empty
    assert len(msci_fallback_lines(pd.Series(["US6153691059"]), pd.Timestamp("2010-12-31"), msci)) == 1
