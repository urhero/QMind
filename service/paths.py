# -*- coding: utf-8 -*-
"""프로젝트 경로 상수 단일 출처 (leaf 모듈).

과거 model_portfolio / dashboard_data / download_factors 가 각각 __file__
기준으로 PROJECT_ROOT/DATA_DIR/OUTPUT_DIR 를 재유도했다. 이를 단일 출처로 통합한다.

레이아웃 (2026-09-09 유니버스별 폴더 정리):
  data/factor_info.csv, factor_desc_kr.csv      — 공통
  data/{BENCHMARK}/factor_YYYY.parquet, mreturn.parquet, country_map.parquet,
                   mp_target_gross.csv, mp_multiplier.csv, bmwgt.parquet, bm_returns.csv
  output/{BENCHMARK}/{YYYY-MM-DD}/               — 기준일별 산출물 (dated / latest)
  output/{BENCHMARK}/mp_weight_history/          — 회차 간 이력 (EMA prev 입력)
  output/{BENCHMARK}/test/                       — mp test 모드 산출물 (byte-diff 기준선)

dated() 는 기준일 폴더를 만든다(mkdir) — 쓰는 쪽이 전부 to_csv 직행이라 여기서 책임진다.
"""
from __future__ import annotations

from pathlib import Path

from config import PARAM

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
_BENCHMARK = PARAM["benchmark"]


def universe_data_dir(benchmark: str) -> Path:
    """유니버스 종속 데이터 폴더 (data/{benchmark}/)."""
    return DATA_DIR / benchmark


UNIVERSE_DATA_DIR = universe_data_dir(_BENCHMARK)
MRETURN_FILE = "mreturn.parquet"  # M_RETURN parquet 파일명 — download/validation/mp 공용

OUTPUT_DIR = PROJECT_ROOT / "output" / _BENCHMARK
HISTORY_DIR = OUTPUT_DIR / "mp_weight_history"
TEST_OUTPUT_DIR = OUTPUT_DIR / "test"

DATE_GLOB = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]"


def dated_dir(base: Path, as_of) -> Path:
    """기준일 폴더 base/YYYY-MM-DD (생성 포함)."""
    d = base / str(as_of)[:10]
    d.mkdir(parents=True, exist_ok=True)
    return d


def dated(path: Path, as_of) -> Path:
    """산출물 경로에 기준일 폴더+접미사를 붙인다: foo.csv + 2026-06-30 -> 2026-06-30/foo_2026-06-30.csv.

    as_of 가 None 이면 원본 경로를 그대로 반환 (기준일을 못 구한 경우 안전 폴백).
    Timestamp/date/str 모두 허용 — 앞 10자만 사용한다.
    """
    if as_of is None:
        return path
    d = str(as_of)[:10]
    return dated_dir(path.parent, d) / f"{path.stem}_{d}{path.suffix}"


def latest(path: Path, as_of=None) -> Path:
    """기준일 폴더의 산출물 중 최신본 경로. 없으면 원본(구 무날짜 파일)을 반환한다.

    as_of 를 주면 그 기준일 이하 중 최신본을 고른다 — 과거 시점 대시보드를 만들 때
    최신 산출물이 섞이는 것을 막는다. 해당 시점 이전 파일이 없으면 전체 최신본.
    글롭은 날짜 패턴으로 한정해 meta_data_test_*.csv 같은 동일 stem 파생 파일이
    섞이지 않도록 한다.
    """
    matches = sorted(path.parent.glob(f"{DATE_GLOB}/{path.stem}_{DATE_GLOB}{path.suffix}"))
    if not matches:
        return path
    if as_of:
        cutoff = f"{path.stem}_{str(as_of)[:10]}{path.suffix}"
        eligible = [m for m in matches if m.name <= cutoff]
        if eligible:
            return eligible[-1]
    return matches[-1]
