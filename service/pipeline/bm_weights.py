# -*- coding: utf-8 -*-
"""벤치마크 비중 로드 + 종목별 숏 상한 (2026-08-21).

제약: 종목별 숏 비중 <= 그 종목의 BM 비중. 즉 총 보유(BM + 액티브)가 음수가
되지 않게 한다 (실물 공매도 없이 언더웨이트로만 표현 가능한 범위).

BM 미편입 종목(비중 0)은 숏 한도 0 -> 숏 불가 (사용자 결정 2026-08-21).
"""
from __future__ import annotations

import logging
from functools import lru_cache

import pandas as pd

from service.paths import universe_data_dir

logger = logging.getLogger(__name__)


@lru_cache(maxsize=4)
def load_bm_weights(benchmark: str) -> pd.DataFrame | None:
    """(ddt, gvkeyiid) -> 정규화된 BM 비중. 파일 없으면 None (제약 미적용)."""
    path = universe_data_dir(benchmark) / "bmwgt.parquet"
    if not path.exists():
        logger.warning("%s 없음 - BM 숏 상한 미적용", path.name)
        return None
    bw = pd.read_parquet(path).drop_duplicates(["ddt", "gvkeyiid"])
    bw["ddt"] = pd.to_datetime(bw["ddt"])
    # 월별 합=1 정규화 (일부 월의 부분 적재/중복 보정)
    bw["wgt"] = bw["wgt"] / bw.groupby("ddt")["wgt"].transform("sum")
    return bw.set_index(["ddt", "gvkeyiid"])["wgt"]


def bm_weights_at(benchmark: str, t) -> pd.Series | None:
    """해당 월의 BM 비중 (없으면 None)."""
    bw = load_bm_weights(benchmark)
    if bw is None:
        return None
    try:
        return bw.loc[pd.Timestamp(t)]
    except KeyError:
        return None


def apply_bm_short_cap(w: pd.Series, bm: pd.Series) -> pd.Series:
    """종목별 숏을 BM 비중까지로 제한하고, 롱을 숏 총액에 맞춰 비례 조정한다.

    상한 초과분은 **그냥 잘라낸다**. 숏 총액이 줄어든 만큼 롱을 비례 축소해
    달러 중립을 유지한다 -> 목표 노출에는 미달하지만 종목별 상대 비중(팩터
    신호)이 보존된다. (잘린 물량을 여유 있는 숏에 재분배하는 water-filling
    변형은 2026-08-21 실측에서 소형주 숏이 대형주 숏으로 밀려 팩터 신호가
    사이즈 베팅으로 변질, Sharpe +0.715 -> -0.855 로 반전 -> 폐기.)
    """
    longs, shorts = w[w > 0], w[w < 0]
    if shorts.empty:
        return w
    cap = bm.reindex(shorts.index).fillna(0.0).astype(float)   # 미편입 -> 0 (숏 금지)
    alloc = pd.concat([(-shorts), cap], axis=1).min(axis=1)     # 초과분 절단만
    short_gross = float(alloc.sum())
    lg = float(longs.sum())
    new_longs = longs * (short_gross / lg) if lg > 1e-15 else longs
    return pd.concat([new_longs, -alloc]).reindex(w.index).fillna(0.0)
