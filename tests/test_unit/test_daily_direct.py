# -*- coding: utf-8 -*-
"""일별 직접 적재 (db/daily_direct.py) 와 읽기 (db/daily_read.py), 2026-11-06."""
import numpy as np
import pandas as pd

from db.daily_direct import dates_to_load, guard_ok, price_lines, weekdays_back
from db.daily_read import add_returns

D = pd.Timestamp("2026-10-30")


def _hist(*rows):
    """(gvkey, iid, isin, efffrom, effthru) - None = 열린 기간."""
    df = pd.DataFrame(rows, columns=["gvkey", "iid", "isin", "efffrom", "effthru"])
    df["efffrom"] = pd.to_datetime(df["efffrom"])
    df["effthru"] = pd.to_datetime(df["effthru"])
    return df


def _lines(*rows):
    return pd.DataFrame(rows, columns=["gvkeyiid", "isin"])


class TestWeekdaysBack:
    def test_skips_weekends_and_ends_on_given_day(self):
        out = weekdays_back(pd.Timestamp("2026-11-02"), 3)  # 월요일
        assert out == [pd.Timestamp("2026-10-29"), pd.Timestamp("2026-10-30"), pd.Timestamp("2026-11-02")]


class TestDatesToLoad:
    def test_new_dates_and_recent_revisit(self):
        source = pd.to_datetime(["2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30"])
        stored = pd.Series({pd.Timestamp("2026-10-27"): 100, pd.Timestamp("2026-10-28"): 100})
        out = dates_to_load(source, stored, revisit=1)
        assert out == [pd.Timestamp("2026-10-29"), pd.Timestamp("2026-10-30")]

    def test_revisit_reloads_stored_recent_dates(self):
        source = pd.to_datetime(["2026-10-28", "2026-10-29", "2026-10-30"])
        stored = pd.Series({d: 100 for d in source})
        assert dates_to_load(source, stored, revisit=2) == [pd.Timestamp("2026-10-29"), pd.Timestamp("2026-10-30")]

    def test_dates_gone_from_source_are_not_touched(self):
        source = pd.to_datetime(["2026-10-30"])
        stored = pd.Series({pd.Timestamp("2026-09-30"): 100})
        assert dates_to_load(source, stored, revisit=5) == [pd.Timestamp("2026-10-30")]


class TestGuard:
    def test_first_load_passes(self):
        assert guard_ok(10, 0)

    def test_shrunk_source_is_refused(self):
        assert not guard_ok(89, 100)
        assert guard_ok(90, 100)


class TestPriceLines:
    def test_illiquid_secondary_switches_to_busiest_same_isin_line(self):
        hist = _hist(("143357", "02W", "IE00B4BNMY34", None, None), ("143357", "01", "IE00B4BNMY34", None, None))
        traded = pd.Series({"14335702W": 1.0, "14335701": 100.0})
        out = price_lines(_lines(("14335702W", "IE00B4BNMY34")), hist, D, traded)
        assert out.to_dict() == {"14335702W": "14335701"}

    def test_comparable_lines_keep_selected_line(self):
        hist = _hist(("000001", "01W", "XX0000000001", None, None), ("000001", "02W", "XX0000000001", None, None))
        traded = pd.Series({"00000101W": 10.0, "00000102W": 100.0})  # 10% = 기준 5% 이상
        out = price_lines(_lines(("00000101W", "XX0000000001")), hist, D, traded)
        assert out.to_dict() == {"00000101W": "00000101W"}

    def test_other_isin_or_ended_line_is_not_a_candidate(self):
        hist = _hist(
            ("000001", "01W", "XX0000000001", None, None),
            ("000001", "02W", "XX0000000002", None, None),  # 다른 ISIN (예: 우선주)
            ("000001", "03W", "XX0000000001", None, "2026-01-31"),  # 끝난 라인
        )
        traded = pd.Series({"00000101W": 1.0, "00000102W": 100.0, "00000103W": 100.0})
        out = price_lines(_lines(("00000101W", "XX0000000001")), hist, D, traded)
        assert out.to_dict() == {"00000101W": "00000101W"}

    def test_no_traded_value_keeps_selected_line(self):
        hist = _hist(("000001", "01W", "XX0000000001", None, None), ("000001", "02W", "XX0000000001", None, None))
        out = price_lines(_lines(("00000101W", "XX0000000001")), hist, D, pd.Series(dtype=float))
        assert out.to_dict() == {"00000101W": "00000101W"}


class TestAddReturns:
    def test_returns_per_line_in_date_order(self):
        px = pd.DataFrame(
            {
                "gvkeyiid": ["B", "A", "A", "B", "A"],
                "ddt": pd.to_datetime(["2026-10-02", "2026-10-02", "2026-10-01", "2026-10-01", "2026-10-05"]),
                "tr_idx": [11.0, 105.0, 100.0, 10.0, 105.0],
                "usd_per_local": [1.0, 0.5, 0.5, 1.0, 0.6],
            }
        )
        out = add_returns(px).set_index(["gvkeyiid", "ddt"])
        assert np.isnan(out.loc[("A", pd.Timestamp("2026-10-01")), "ret_loc"])
        assert out.loc[("A", pd.Timestamp("2026-10-02")), "ret_loc"] == 105 / 100 - 1
        assert out.loc[("A", pd.Timestamp("2026-10-05")), "ret_loc"] == 0.0
        assert out.loc[("A", pd.Timestamp("2026-10-05")), "ret_usd"] == (105 * 0.6) / (105 * 0.5) - 1
        assert out.loc[("B", pd.Timestamp("2026-10-02")), "ret_loc"] == 11 / 10 - 1
