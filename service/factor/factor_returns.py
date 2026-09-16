# -*- coding: utf-8 -*-
"""팩터 롱-숏 수익률 행렬 (universe / walk_forward / report_generator 공유 도메인).

팩터별 라벨 프레임 -> 롱/숏 동일가중 포트폴리오 순수익률(비용 차감) -> (월 x 팩터) 행렬.
"""
from __future__ import annotations

import logging

import pandas as pd
from joblib import Parallel, delayed

from service.pipeline.weight_construction import (
    calculate_vectorized_return,
    construct_long_short_df,
)

logger = logging.getLogger(__name__)

# ponytail: 소량 입력(테스트/소규모 유니버스)은 loky spawn+pickle 오버헤드가 이득보다
# 크므로 이 개수 이하이면 직렬 실행한다. 임계값은 튜닝 노브일 뿐 정확성과 무관하다
# (serial/parallel 은 동일 함수를 동일 순서로 호출 -> 출력 byte-identical).
_PARALLEL_MIN_FACTORS = 8


def _compute_factor_net_return(
    data: pd.DataFrame, abbr: str, backtest_start: str, cost_bps: float
) -> pd.Series:
    """단일 팩터의 롱+숏 순수익률 시리즈를 계산한다 (병렬 워커용 module-level 함수).

    joblib loky(Windows spawn+pickle)가 pickle 할 수 있도록 클로저가 아닌 최상위
    함수로 둔다. 본문은 기존 루프 바디 글자보존: construct -> L/S net -> 합산.
    각 팩터 계산은 자기 데이터만 사용(무상태, 교차 팩터 float 리덕션 없음)하므로
    직렬/병렬 결과가 bit 단위로 동일하다.
    """
    long_df, short_df = construct_long_short_df(data, backtest_start=backtest_start)
    _, net_long, _ = calculate_vectorized_return(long_df, abbr, cost_bps=cost_bps)
    _, net_short, _ = calculate_vectorized_return(short_df, abbr, cost_bps=cost_bps)
    # 한쪽이 비면(롱-only/숏-only 팩터, 예: 라벨에 숏 분위 없음) 있는 쪽만 사용.
    # 둘 다 있으면 기존 동작(net_long + net_short) 그대로 보존(byte-identical).
    sides = [s for s in (net_long, net_short) if not s.empty]
    if not sides:
        return pd.DataFrame(columns=[abbr], dtype=float)
    if len(sides) == 1:
        return sides[0]
    return net_long + net_short


def aggregate_factor_returns(
    factor_data_list: list,
    factor_abbr_list: list[str],
    backtest_start: str = "2017-12-31",
    cost_bps: float = 20.0,
    n_jobs: int = -1,
) -> pd.DataFrame:
    """모든 팩터의 롱+숏 수익률을 하나의 행렬로 결합한다 (오케스트레이션 함수).

    각 팩터에 대해 롱/숏 포트폴리오를 구성하고 수익률을 계산한 후,
    팩터별 순수익률을 (날짜 x 팩터) 행렬로 합친다.

    팩터별 계산은 서로 독립이므로 joblib 으로 코어에 분산한다. joblib 은 결과를
    제출(입력) 순서대로 반환하므로 concat 컬럼 순서가 보존되어 병렬/직렬 출력이
    byte-identical 하다 (컬럼 순서는 이후 corr/클러스터링 선정에 영향 -> 보존 필수).

    Args:
        n_jobs: joblib 워커 수. -1=전체 코어(기본), 1=직렬. 팩터가
            _PARALLEL_MIN_FACTORS 이하이면 오버헤드 회피 위해 강제 직렬.
    """
    if len(factor_data_list) != len(factor_abbr_list):
        raise ValueError(
            f"factor_data_list ({len(factor_data_list)}) and "
            f"factor_abbr_list ({len(factor_abbr_list)}) length mismatch"
        )

    if len(factor_abbr_list) <= _PARALLEL_MIN_FACTORS or n_jobs == 1:
        net_return_series = [
            _compute_factor_net_return(data, abbr, backtest_start, cost_bps)
            for data, abbr in zip(factor_data_list, factor_abbr_list)
        ]
    else:
        net_return_series = Parallel(n_jobs=n_jobs)(
            delayed(_compute_factor_net_return)(data, abbr, backtest_start, cost_bps)
            for data, abbr in zip(factor_data_list, factor_abbr_list)
        )

    combined = pd.concat(net_return_series, axis=1)
    net_return_df = combined.dropna(axis=1)
    dropped = set(combined.columns) - set(net_return_df.columns)
    if dropped:
        logger.warning("Dropped %d factors with NaN: %s", len(dropped), sorted(dropped))

    return net_return_df
