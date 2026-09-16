# -*- coding: utf-8 -*-
"""팩터 데이터 다운로드 및 Pipeline-Ready Parquet 생성 모듈.

SQL Server에서 팩터 데이터를 다운로드하여 pipeline이 바로 사용할 수 있는
최적화된 parquet 파일로 저장한다.

파일 구조 (연도별 분할, 유니버스 폴더):
  data/{BENCHMARK}/
  ├── factor_2018.parquet   — 연도별 팩터 데이터
  ├── factor_2019.parquet
  ├── ...
  ├── factor_2026.parquet
  ├── mreturn.parquet       — M_RETURN (단일 파일)
  └── country_map.parquet   — 국가 매핑 (거래세용)
  이전 vintage 백업은 없다 — data/ 는 git 추적이므로 history 가 백업이다 (2026-09-09).

다운로드 모드:
  - full: 전체 기간 다운로드 (최초 또는 재구축)
  - incremental: 신규 월만 다운로드하여 해당 연도 파일에 append

검증 (validate_parquet_coverage):
  - 빈 월(gap) 감지
  - 팩터별 월간 커버리지 이상 감지 (급감/급증)
  - M_RETURN 종목 수 이상 감지
  - Rich 테이블로 터미널 시각화
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import pandas as pd

from db.factor_query import GenerateQueryStructure
from service.download.download_validation import validate_parquet_coverage
from service.report.reporting import print_coverage_report
from service.download.parquet_io import (
    list_yearly_parquets,
    save_factor_parquet_by_year,
)
from service.paths import DATA_DIR, MRETURN_FILE, UNIVERSE_DATA_DIR

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# Pipeline-Ready 변환
# ═══════════════════════════════════════════════════════════════════════════════

def _build_pipeline_ready(
    raw_df: pd.DataFrame,
    factor_info_path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """SQL에서 가져온 raw DataFrame을 pipeline-ready 2개 DataFrame으로 변환한다.

    Returns:
        (factor_df, mreturn_df) 튜플
        - factor_df: factor_info merge 완료, Undefined 섹터 제외, categorical
        - mreturn_df: M_RETURN만 분리, categorical (gvkeyiid + ddt 키)
    """
    # M_RETURN 분리 (67K행 — 별도 저장하여 19M행 중복 방지)
    # merge 키: (gvkeyiid, ddt) — sec 불일치 0건 확인완료
    m_mask = raw_df["factorAbbreviation"] == "M_RETURN"
    mreturn_df = (
        raw_df.loc[m_mask, ["gvkeyiid", "ddt", "val"]]
        .rename(columns={"val": "M_RETURN"})
        .reset_index(drop=True)
    )
    factor_raw = raw_df.loc[~m_mask].reset_index(drop=True)

    # factor_info merge (factorOrder만 — 나머지 메타는 pipeline에서 lazy join)
    factor_info = pd.read_csv(factor_info_path)
    factor_df = factor_raw.merge(
        factor_info[["factorAbbreviation", "factorOrder"]],
        on="factorAbbreviation",
        how="inner",
    )
    factor_df = factor_df[factor_df["sec"] != "Undefined"]

    # categorical 변환 (parquet에 보존 → 로딩 시 즉시 사용 가능)
    for col in ["gvkeyiid", "ticker", "isin", "factorAbbreviation", "sec"]:
        if col in factor_df.columns:
            factor_df[col] = factor_df[col].astype("category")
    mreturn_df["gvkeyiid"] = mreturn_df["gvkeyiid"].astype("category")

    # 필요 컬럼만 유지
    keep = ["gvkeyiid", "ticker", "isin", "ddt", "sec", "val", "factorAbbreviation", "factorOrder"]
    factor_df = factor_df[[c for c in keep if c in factor_df.columns]].reset_index(drop=True)

    return factor_df, mreturn_df


# ═══════════════════════════════════════════════════════════════════════════════
# 다운로드 파이프라인
# ═══════════════════════════════════════════════════════════════════════════════

def run_download_pipeline(
    start_date: str,
    end_date: str,
    *,
    out_dir: Path | str | None = None,
    incremental: bool = False,
    validate: bool = True,
) -> None:
    """SQL Server에서 팩터 데이터를 다운로드하여 pipeline-ready parquet으로 저장한다.

    Args:
        start_date: 분석 시작 날짜 (예: "2017-12-31")
        end_date: 분석 종료 날짜 (예: "2026-02-28")
        out_dir: 출력 폴더 (None이면 data/{BENCHMARK}/)
        incremental: True이면 end_date 월만 다운로드하여 기존 파일에 append
        validate: True이면 저장 후 커버리지 검증 실행
    """
    out_dir = Path(out_dir) if out_dir else UNIVERSE_DATA_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    mreturn_path = out_dir / MRETURN_FILE
    factor_info_path = DATA_DIR / "factor_info.csv"

    has_existing = bool(list_yearly_parquets(out_dir))

    if incremental and has_existing:
        # ─── 증분 모드: end_date 월만 다운로드하여 해당 연도 파일에 append ───
        logger.info("Incremental download for %s", end_date)
        t0 = time.time()

        new_raw = GenerateQueryStructure(end_date, end_date).fetch_snp()
        if new_raw.empty:
            logger.warning("No new data for %s", end_date)
            return

        logger.info("Fetched %s new rows in %.2fs", f"{len(new_raw):,}", time.time() - t0)

        new_factor, new_mret = _build_pipeline_ready(new_raw, factor_info_path)

        # 영향받는 연도 파일만 업데이트 (전체 재로드 대신)
        t0 = time.time()
        end_dt = pd.Timestamp(end_date)
        affected_year = end_dt.year
        year_file = out_dir / f"factor_{affected_year}.parquet"

        if year_file.exists():
            existing_year = pd.read_parquet(year_file)
            existing_year = existing_year[existing_year["ddt"] != end_dt]
        else:
            # 연도 파일이 없으면 해당 연도 전체를 DB에서 다운로드
            logger.info("Year file missing for %d — downloading full year", affected_year)
            year_start = f"{affected_year}-01-01"
            year_end = f"{affected_year}-12-31"
            year_raw = GenerateQueryStructure(year_start, year_end).fetch_snp()
            if not year_raw.empty:
                year_factor, year_mret = _build_pipeline_ready(year_raw, factor_info_path)
                # end_date 월은 이미 new_factor에 있으므로 제외
                existing_year = year_factor[year_factor["ddt"] != end_dt]
                # 연도 전체 M_RETURN도 병합
                existing_mret = pd.read_parquet(mreturn_path) if mreturn_path.exists() else pd.DataFrame()
                year_mret_no_dup = year_mret[year_mret["ddt"] != end_dt]
                if not existing_mret.empty:
                    existing_mret = pd.concat([existing_mret, year_mret_no_dup], ignore_index=True)
                    existing_mret = existing_mret.drop_duplicates(subset=["gvkeyiid", "ddt"], keep="last")
                else:
                    existing_mret = year_mret_no_dup
                existing_mret.to_parquet(mreturn_path, index=False, compression="zstd")
            else:
                existing_year = pd.DataFrame()

        updated_year = pd.concat([existing_year, new_factor], ignore_index=True)
        # backfill(기존 최신 월보다 과거 월 재다운로드) 시 append 가 (gvkeyiid, factor)
        # 그룹 내 시간순서를 깨서 calculate_factor_stats_batch 의 shift(1) lag 이
        # 조용히 오염됨 -> 시간순 복원. 최신 월 append(통상 경로)는 이미 정렬돼
        # 있으므로 그대로 둔다 (기존 파일 바이트 보존).
        if not existing_year.empty and end_dt < existing_year["ddt"].max():
            updated_year = updated_year.sort_values(
                ["factorAbbreviation", "ddt"], kind="stable", ignore_index=True
            )
        save_factor_parquet_by_year(updated_year, out_dir, years={affected_year})

        # M_RETURN은 단일 파일이므로 전체 업데이트
        existing_mret = pd.read_parquet(mreturn_path) if mreturn_path.exists() else pd.DataFrame()
        if not existing_mret.empty:
            existing_mret = existing_mret[existing_mret["ddt"] != end_dt]
        updated_mret = pd.concat([existing_mret, new_mret], ignore_index=True)
        updated_mret.to_parquet(mreturn_path, index=False, compression="zstd")

        logger.info("Incremental update saved in %.2fs (year: %d)",
                     time.time() - t0, affected_year)

    else:
        # ─── 전체 모드: 연 단위 청크 다운로드 ───
        # 전체 기간 일괄 fetch는 대형 유니버스(MXWO ~67M행)에서 MemoryError 발생
        # -> 연도별로 fetch/변환/저장하여 피크 메모리를 1개 연도 수준으로 제한.
        logger.info("Full download %s -> %s (yearly chunks)", start_date, end_date)
        t0 = time.time()

        start_ts, end_ts = pd.Timestamp(start_date), pd.Timestamp(end_date)
        saved_paths: list[Path] = []
        mret_frames: list[pd.DataFrame] = []
        total_rows = 0
        for year in range(start_ts.year, end_ts.year + 1):
            ys = max(start_ts, pd.Timestamp(f"{year}-01-01")).strftime("%Y-%m-%d")
            ye = min(end_ts, pd.Timestamp(f"{year}-12-31")).strftime("%Y-%m-%d")
            raw_df = GenerateQueryStructure(ys, ye).fetch_snp()
            if raw_df.empty:
                logger.warning("No rows for %d", year)
                continue
            total_rows += len(raw_df)
            factor_df, mret_df = _build_pipeline_ready(raw_df, factor_info_path)
            del raw_df
            saved_paths += save_factor_parquet_by_year(factor_df, out_dir, years={year})
            mret_frames.append(mret_df)
            del factor_df

        if not saved_paths:
            logger.warning("No rows returned")
            return

        mreturn_df = pd.concat(mret_frames, ignore_index=True)
        mreturn_df["gvkeyiid"] = mreturn_df["gvkeyiid"].astype("category")
        mreturn_df.to_parquet(mreturn_path, index=False, compression="zstd")

        total_mb = sum(p.stat().st_size for p in saved_paths) / 1024**2
        logger.info("Fetched %s rows, saved in %.2fs - factor: %.1f MB (%d files), mreturn: %.1f MB",
                     f"{total_rows:,}", time.time() - t0, total_mb, len(saved_paths),
                     mreturn_path.stat().st_size / 1024**2)

    # ─── 국가 매핑 재생성 (지역 중립 랭킹용 — 증분 신규 종목 자동 반영) ───
    from db.factor_query import fetch_country_map
    cmap = fetch_country_map()
    cmap.to_parquet(out_dir / "country_map.parquet", index=False, compression="zstd")
    logger.info("Country map refreshed (%d stocks)", len(cmap))

    # ─── 검증 ───
    if validate:
        warnings_list, factor_df, mret_df = validate_parquet_coverage(out_dir, mreturn_path)
        print_coverage_report(warnings_list, factor_df, mret_df)

        errors = [w for w in warnings_list if w["level"] == "ERROR"]
        if errors:
            logger.error("Validation failed with %d error(s) - review before running pipeline", len(errors))
            raise RuntimeError(
                f"Data validation failed: {len(errors)} error(s). "
                "Fix data issues or re-run with validate=False to skip."
            )
