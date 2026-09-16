# -*- coding: utf-8 -*-
"""Parquet 연도별 분할 저장/로드 유틸리티.

GitHub 100MB 파일 크기 제한을 우회하기 위해 대용량 factor parquet를
연도별로 분할 저장하고, 로드 시 투명하게 병합한다.

파일 명명 규칙 (data_dir = data/{BENCHMARK}/):
    data/MXCN1A/factor_2018.parquet
    ...
    data/MXCN1A/factor_2026.parquet
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# 저장
# ═══════════════════════════════════════════════════════════════════════════════

def save_factor_parquet_by_year(
    df: pd.DataFrame,
    data_dir: str | Path,
    compression: str = "zstd",
    *,
    years: set[int] | None = None,
) -> list[Path]:
    """DataFrame을 연도별 parquet으로 분할 저장.

    Args:
        df: factor DataFrame (ddt 컬럼 필수)
        data_dir: 저장 디렉토리 (유니버스 폴더)
        compression: 압축 방식
        years: 지정하면 해당 연도만 저장 (증분 모드용). None이면 전체.

    Returns:
        생성된 파일 경로 리스트
    """
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    # 전체 프레임 copy + 임시 _year 컬럼 없이, 외부 연도 Series 로 그룹화한다
    # (대용량(19M행) 다운로드 시 불필요한 full-frame copy 회피, 입력 df 불변).
    year_ser = pd.to_datetime(df["ddt"]).dt.year

    if years is not None:
        mask = year_ser.isin(years)
        df = df[mask]
        year_ser = year_ser[mask]

    saved: list[Path] = []
    for year, group in df.groupby(year_ser):
        out = data_dir / f"factor_{year}.parquet"
        group.to_parquet(
            out, index=False, compression=compression,
        )
        size_mb = out.stat().st_size / (1024 * 1024)
        logger.info("Saved %s (%d rows, %.1f MB)", out.name, len(group), size_mb)
        saved.append(out)

    logger.info(
        "Factor parquet: %d yearly file(s) saved in %s",
        len(saved), data_dir,
    )
    return saved


# ═══════════════════════════════════════════════════════════════════════════════
# 로드
# ═══════════════════════════════════════════════════════════════════════════════

def load_factor_parquet(
    data_dir: str | Path,
    start_year: int | None = None,
    end_year: int | None = None,
    *,
    validate: bool = False,
    columns: list[str] | None = None,
) -> pd.DataFrame:
    """연도별 분할 parquet을 로드하여 하나의 DataFrame으로 반환.

    분할 파일(factor_YYYY.parquet)만 지원 — start/end_year 로 연도 선택 로드.
    파일이 없으면 FileNotFoundError.

    Args:
        data_dir: parquet 디렉토리 (유니버스 폴더 data/{BENCHMARK}/)
        start_year: 시작 연도. 주어지면 해당 연도부터만 로드
        end_year: 종료 연도. 주어지면 해당 연도까지만 로드
        validate: True이면 로드 후 데이터 무결성 검증. ERROR 발견 시 RuntimeError.
        columns: 읽을 컬럼 목록 (pyarrow column pushdown). None이면 전체 컬럼.
            validate=True와 함께 쓸 땐 검증에 필요한 컬럼을 포함해야 한다.

    Returns:
        병합된 factor DataFrame

    Raises:
        FileNotFoundError: parquet 파일이 없는 경우
        RuntimeError: validate=True이고 ERROR 수준 문제 발견 시
    """
    split_files = list_yearly_parquets(data_dir)
    if not split_files:
        raise FileNotFoundError(f"No factor parquet found at {data_dir}")
    if start_year or end_year:
        sy, ey = start_year or 0, end_year or 9999
        split_files = [f for f in split_files if sy <= int(f.stem.rsplit("_", 1)[-1]) <= ey]
        if not split_files:
            raise FileNotFoundError(
                f"No yearly parquets in year range {start_year}~{end_year} at {data_dir}"
            )

    frames = [pd.read_parquet(f, columns=columns) for f in split_files]
    result = pd.concat(frames, ignore_index=True)
    logger.info("Loaded %d yearly parquets (%s rows)", len(split_files), f"{len(result):,}")

    if validate:
        issues = validate_loaded_factor_data(result)
        errors = [i for i in issues if i["level"] == "ERROR"]
        for issue in issues:
            if issue["level"] == "ERROR":
                logger.error("[%s] %s", issue["type"], issue["message"])
            else:
                logger.warning("[%s] %s", issue["type"], issue["message"])
        if errors:
            raise RuntimeError(
                f"Loaded factor data has {len(errors)} error(s). "
                f"First: {errors[0]['message']}"
            )

    return result


def list_yearly_parquets(data_dir: str | Path) -> list[Path]:
    """디렉토리에 있는 연도별 분할 parquet 경로 리스트를 반환."""
    return sorted(Path(data_dir).glob("factor_[0-9][0-9][0-9][0-9].parquet"))


# ═══════════════════════════════════════════════════════════════════════════════
# 검증
# ═══════════════════════════════════════════════════════════════════════════════

_MONTH_GAP_THRESHOLD_DAYS = 35  # 연속 월 간격이 이보다 크면 데이터 누락(gap)으로 간주


def month_gap_issues(sorted_months, threshold_days: int = _MONTH_GAP_THRESHOLD_DAYS) -> list[dict]:
    """오름차순 월 시퀀스에서 threshold_days 초과 간격을 MONTH_GAP ERROR 이슈로 만든다.

    validate_loaded_factor_data 와 download_validation 이 공유한다 (동일 로직 중복 제거).
    (cp949 호환: 메시지 화살표는 ASCII '->' 사용.)
    """
    issues: list[dict] = []
    for i in range(1, len(sorted_months)):
        gap = (sorted_months[i] - sorted_months[i - 1]).days
        if gap > threshold_days:
            issues.append({
                "level": "ERROR", "type": "MONTH_GAP",
                "message": (
                    f"Gap: {pd.Timestamp(sorted_months[i-1]).strftime('%Y-%m')} -> "
                    f"{pd.Timestamp(sorted_months[i]).strftime('%Y-%m')} ({gap}d)"
                ),
            })
    return issues


def validate_loaded_factor_data(
    df: pd.DataFrame,
    *,
    min_months: int = 3,
    min_factors_per_month: int = 50,
    min_stocks_per_month: int = 30,
    # 정상 베이스라인: MXCN1A ~5%, MXWO ~13% (글로벌 유니버스는 은행 전용
    # 팩터·커버리지 차이로 구조적 희소). 로딩 실패형 회귀(전량 NaN)만 잡으면 된다.
    max_null_pct: float = 0.20,
) -> list[dict]:
    """병합된 factor DataFrame의 무결성을 검증한다.

    검증 항목:
      1. 필수 컬럼 존재 (ddt, gvkeyiid, factorAbbreviation, val, sec)
      2. 월 수 최소 기준 충족
      3. 월별 팩터 수 최소 기준 충족
      4. 월별 종목 수 최소 기준 충족
      5. val 컬럼 NaN 비율 초과 여부
      6. val 컬럼 inf 값 존재 여부
      7. 연도 경계 연속성 (월 gap 감지)
      8. 중복 행 검사 (gvkeyiid + ddt + factorAbbreviation)

    Args:
        df: 병합된 factor DataFrame
        min_months: 최소 월 수 (기본 3)
        min_factors_per_month: 월별 최소 팩터 수 (기본 50)
        min_stocks_per_month: 월별 최소 종목 수 (기본 30)
        max_null_pct: val 컬럼 최대 NaN 비율 (기본 20%)

    Returns:
        경고/에러 리스트 [{"level": "WARN"|"ERROR", "type": str, "message": str}]
    """
    issues: list[dict] = []

    # [1] 필수 컬럼 검사
    required = {"ddt", "gvkeyiid", "factorAbbreviation", "val", "sec"}
    missing = required - set(df.columns)
    if missing:
        issues.append({
            "level": "ERROR", "type": "MISSING_COLUMNS",
            "message": f"Missing: {missing}",
        })
        return issues  # 이후 검증 불가

    # [2] 월 수 검사
    months = sorted(df["ddt"].unique())
    if len(months) < min_months:
        issues.append({
            "level": "ERROR", "type": "INSUFFICIENT_MONTHS",
            "message": f"Only {len(months)} month(s), need >= {min_months}",
        })

    # [3] 월별 팩터 수
    monthly_factors = df.groupby("ddt")["factorAbbreviation"].nunique()
    low_factor_months = monthly_factors[monthly_factors < min_factors_per_month]
    for dt, cnt in low_factor_months.items():
        issues.append({
            "level": "WARN", "type": "LOW_FACTOR_COUNT",
            "message": f"{pd.Timestamp(dt).strftime('%Y-%m')}: {cnt} factors (< {min_factors_per_month})",
        })

    # [4] 월별 종목 수
    monthly_stocks = df.groupby("ddt")["gvkeyiid"].nunique()
    low_stock_months = monthly_stocks[monthly_stocks < min_stocks_per_month]
    for dt, cnt in low_stock_months.items():
        issues.append({
            "level": "WARN", "type": "LOW_STOCK_COUNT",
            "message": f"{pd.Timestamp(dt).strftime('%Y-%m')}: {cnt} stocks (< {min_stocks_per_month})",
        })

    # [5] val NaN 비율 (100% NaN 팩터 제외 후 검사)
    factor_nan = df.groupby("factorAbbreviation")["val"].apply(lambda x: x.isna().mean())
    full_nan_factors = factor_nan[factor_nan >= 1.0].index.tolist()
    if full_nan_factors:
        issues.append({
            "level": "WARN", "type": "FULL_NAN_FACTORS",
            "message": f"{len(full_nan_factors)} factor(s) with 100% NaN: {full_nan_factors[:5]}{'...' if len(full_nan_factors) > 5 else ''}",
        })
    df_valid = df[~df["factorAbbreviation"].isin(full_nan_factors)]
    null_pct = df_valid["val"].isna().mean() if len(df_valid) > 0 else 0.0
    if null_pct > max_null_pct:
        issues.append({
            "level": "ERROR", "type": "HIGH_NULL_PCT",
            "message": f"val NaN ratio {null_pct:.1%} exceeds {max_null_pct:.1%} (after excluding {len(full_nan_factors)} full-NaN factors)",
        })

    # [6] val inf 검사
    if np.any(np.isinf(df["val"].dropna().values)):
        issues.append({
            "level": "ERROR", "type": "INF_VALUES",
            "message": "Infinite values in val column",
        })

    # [7] 월 gap 검사 (35일 초과)
    issues.extend(month_gap_issues(months))

    # [8] 중복 행 검사
    dup_count = df.duplicated(subset=["gvkeyiid", "ddt", "factorAbbreviation"]).sum()
    if dup_count > 0:
        issues.append({
            "level": "ERROR", "type": "DUPLICATE_ROWS",
            "message": f"{dup_count:,} duplicate rows (gvkeyiid + ddt + factorAbbreviation)",
        })

    # [9] 팩터 내 시간순 정렬 검사 — calculate_factor_stats_batch 의 lag(groupby.shift(1))는
    # (gvkeyiid, factorAbbreviation) 그룹 내 행이 시간순이라는 전제에 의존한다.
    # 깨지면 전월값 대신 임의 월 값으로 투자하는 조용한 오염이므로 ERROR 로 승격.
    # 팩터 단위 단조성(저장 규약: ORDER BY factorAbbreviation, ddt)이 그룹 단위를 함의하므로
    # 싼 검사를 먼저 하고, 위반 팩터에 한해 정밀(그룹 단위) 재검사한다.
    factor_mono = df.groupby("factorAbbreviation", observed=True)["ddt"].apply(
        lambda s: s.is_monotonic_increasing
    )
    suspects = factor_mono[~factor_mono].index
    if len(suspects):
        sub = df[df["factorAbbreviation"].isin(suspects)]
        group_mono = sub.groupby(["gvkeyiid", "factorAbbreviation"], observed=True)["ddt"].apply(
            lambda s: s.is_monotonic_increasing
        )
        n_bad = int((~group_mono).sum())
        if n_bad:
            issues.append({
                "level": "ERROR", "type": "UNSORTED_LAG_GROUPS",
                "message": (
                    f"{n_bad:,} (gvkeyiid, factor) group(s) not in chronological order - "
                    f"factor lag (shift(1)) would be silently corrupted"
                ),
            })

    return issues
