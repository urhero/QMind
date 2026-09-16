import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── 유니버스 스위치 ────────────────────────────────────────────────────────────
# 우선순위: .env 의 BENCHMARK > 아래 상수. .env 에서 MXCN1A/MXWO 블록 중 하나만
# 활성(나머지 주석)으로 두면 되고, .env 에 BENCHMARK 가 없으면 아래 값이 쓰인다
# (.env 없는 CI 가 이 경로를 탄다). 기본값은 2026-09-09 MXCN1A -> MXWO 로 전환.
# 이 값 하나로 DB/universe 식별자, PIPELINE_PARAMS 유니버스별 값, output/{BENCHMARK}/,
# data/{BENCHMARK}/ 데이터 파일이 전부 결정된다 (2026-09-02 통합).
BENCHMARK = os.getenv("BENCHMARK") or "MXWO"

UNIVERSES = {
    "MXCN1A": {"universe": "clarifi_mxcn1a_afl", "server_name": "10.206.1.19,9433", "db_name": "GLOBAL"},
    "MXWO":   {"universe": "clarifi_mxwo_afl",   "server_name": "10.206.101.14",    "db_name": "kb_global"},
}
_U = UNIVERSES[BENCHMARK]  # 미등록 유니버스(오타)는 여기서 KeyError 로 즉시 실패

# ── DB 연결 설정 (.env 필수) ──────────────────────────────────────────────────
PARAM = {
    "benchmark": BENCHMARK,
    "universe": os.getenv("UNIVERSE") or _U["universe"],
    "server_name": os.getenv("SERVER_NAME") or _U["server_name"],
    "db_name": os.getenv("DB_NAME") or _U["db_name"],
    "user_name": os.getenv("USER_NAME", ""),
    "user_pwd": os.getenv("USER_PWD", ""),
    "odbc_name": os.getenv("ODBC_NAME", "ODBC Driver 17 for SQL Server"),
}

if not PARAM["user_pwd"]:
    logger.warning("USER_PWD not set in .env - DB connections will fail")
if not PARAM["server_name"]:
    logger.warning("SERVER_NAME not set in .env - DB connections will fail")
if not PARAM["user_name"]:
    logger.warning("USER_NAME not set in .env - DB connections will fail")

# ── 파이프라인 비즈니스 파라미터 ──────────────────────────────────────────────
#
# [모드 가이드]
#   optimization_mode: "erc"(기본, 상관 인지 ERC) / "equal_risk_weight"(1/sigma)
#                      / "equal_weight"(구 기본 1/N) / "hardcoded"(구 프로덕션 고정 가중치)
#   factor_ranking_method: "tstat"(기본) / "shrunk_tstat"(스타일 평균 수축) / "cagr"
#   use_cluster_dedup: 상관 기반 Hierarchical Clustering 중복 제거 on/off (cluster_method 참조)
#
# 공통 파라미터 + 유니버스별 오버라이드 -> PIPELINE_PARAMS (2026-09-02 통합; 유니버스별 값은
# 통합 전 각 전용 브랜치에서 채택돼 있던 값). 개발 브랜치 운영은 README "유니버스 전환" 표.
_COMMON_PARAMS = {
    "style_cap": 0.25,                # 스타일별 최대 비중 (프로덕션 규제 요건)
    # ── factor-level 백테스트 전용 비용 배수 ──────────────────────────────────
    # 팩터별 전액 계상은 교차 팩터 netting(실거래는 MP 합산 후 월 1회 매매)을 무시해
    # 비용을 과대평가한다. MP-level(종목단) 비용 백테스트 실측 결과
    # 실제 종목매매비용 / 팩터별 전액계상 비용 = 0.574 (2026-07-03,
    # docs/experiments/mp_level_cost_20260703.md) -> 0.6 으로 근사 적용
    # (MXWO 10bp x 0.6 = 6bp; MXCN1A 20bp x 0.6 = 12bp).
    # mp(운영) 파이프라인에는 적용되지 않음 (백테스트 전용).
    "backtest_cost_multiplier": 0.6,   # 선정 입력용 비용 (비용 인지 선정이 A/B 최적 — 0/22bp 모두 열위). 주의: factor-level 성과 회계는 고회전 구성에서 실비용 과소계상 -> 정본 성과 판단은 mp_level_cost_backtest 실측 기준
    "top_factor_count": 50,            # rank_score 상위 절단 수 (dedup off 또는 cluster_method=topn 에서 적용; winner_median 은 미사용). backtest CLI --top-factors 미지정 시 이 값
    "spread_threshold_pct": 0.05,      # L/N/S 라벨링 임계값. MXWO 0.05 (2026-07-29, 0.025~0.05 고원) / MXCN1A 0.05 (2026-08-05: MDD -10.1->-6.1%, Calmar 0.305); 구 0.10
    "min_sector_stocks": 10,           # 섹터-날짜 최소 종목 수 (프로덕션)
    "max_zero_return_months": 10,      # 0 수익률 허용 최대 월 수
    "backtest_start": "2009-12-31",    # 팩터 L/S 수익률 계산 하한(ddt >= 이 값). 엔진은 parquet 전체 기간을 돌고 CLI 날짜는 무시 — MXWO 데이터는 2015-06 부터라 사실상 no-op
    "optimization_mode": "erc",        # "erc"(상관 인지 ERC; MXWO 2026-07-29, MXCN1A 2026-08-05 채택) / "equal_risk_weight"(1/sigma) / "equal_weight"(1/N) / "hardcoded". 근거: docs/experiments/mxwo_sharpe_ladder_20260729.md, mxcn1a_component_ablation_20260805.md
    "deploy_step": 1.0,                # 부분 조정 배포 (1.0=전량 조정). MXWO: 20bp 시절 0.5 채택했으나 10bp 전환 후 역전 — 실측 step1.0 0.672 > 0.5 0.604 (2026-07-30)
    "ts_mom_window": 3,                # 팩터 TS 모멘텀 틸트: trailing N개월 자기수익 음수 팩터 비중 감쇠. MXWO 3 (2026-08-07 재검증: 독립 재실행 2회 연속 3M 피크 + 실측 net 0.721->0.761) / MXCN1A 3 (창 3~6 고원, 2026-08-05). None/0 = off
    "bm_short_cap": False,             # 종목별 숏 비중 <= 그 종목의 BM 비중 (총 보유가 음수가 되지 않게). 배수 적용 후 최종 비중에 적용, 초과분은 잘라내고(재분배 없음) 롱은 숏 총액에 맞춰 비례 조정(중립 유지). BM 미편입 종목은 숏 불가. data/{benchmark}/bmwgt.parquet 필요. **2026-08-21 실측 기각 -> False**: 상한이 소형주 숏을 잘라내 숏 북이 대형주로 편중되는데, 2018-2026 대형-소형 스프레드가 +8.63%p/yr 라 대형주 숏은 구조적 역풍 -> Sharpe +0.715 -> -0.447 반전. 구현은 보존 (mxwo_sharpe_ladder_20260729.md 13차)
    "factor_ranking_method": "tstat",  # "shrunk_tstat" / "tstat"(현 기본) / "cagr" — mp+backtest 공통 선정 기준
    "n_clusters": 18,                  # 클러스터 수 (use_cluster_dedup=True일 때)
    "per_cluster_keep": 3,             # 클러스터당 유지 팩터 수
    "cluster_method": "winner_median", # "winner_median"(기본): 클러스터 1등 보호 + 전역 중위값 바닥(top_n 고정 없음, ~18~54 가변) / "topn": 클러스터당 상위3 -> 전역 rank_score Top-N
    "newey_west_lag": 3,               # Newey-West 보정 lag (meta_data.csv 진단용)
}

_UNIVERSE_PARAMS = {
    # 중국 A주. 2026-08-05 컴포넌트 ablation 채택 스택 (docs/experiments/mxcn1a_component_ablation_20260805.md).
    # 실측 net Sharpe 0.730 / MDD -0.76% (롱숏 ±7% 배포 기준, 2026-09-16 look-ahead 제거 후; 구 미스케일 0.703 / -4.9%).
    "MXCN1A": {
        "transaction_cost_bps": 20.0,  # 종목 단위 거래비용 (basis points)
        "apply_country_tax": False,    # 국가별 거래세(COUNTRY_TAX_BPS) 미적용. A주는 등록지(HKG 등)와 무관하게 본토 인지세 대상 — 등록지 기준 세율표가 맞지 않음 (2026-09-02)
        "erc_shrinkage": 0.5,          # ERC cov 대각 수축 비율. 0.2~0.5 고원, 실측 검증값 0.5 채택 (2026-08-05)
        "ts_mom_scale": 0.5,           # 감쇠 배율 (0.7은 열위, 0.5 채택)
        "use_cluster_dedup": True,     # 상관 클러스터 dedup (cluster_method=winner_median) 적용 — 2026-06-30 채택
        "is_window_months": None,      # expanding IS
        "selection_hysteresis": 0.5,   # 선정 히스테리시스 margin (rank_score 단위). 0=off. 실험 근거: smoothing_cost_experiment_20260612.md
        "weight_rebal_months": 3,      # Tier 2 가중 리밸 주기 (구 backtest CLI 기본값)
        "min_coverage_pct": 0.0,       # 커버리지 필터 미적용
        "sector_short_cap": None,      # 섹터 숏캡 미적용
        "mp_target_gross": 0.14,       # MP 배포 목표 총 gross (롱 +7% / 숏 -7%). 2026-09-02 사용자 지정 (그 전엔 None=배수 1.0, 북 gross ~0.92 그대로). 시점별 이력은 data/MXCN1A/mp_target_gross.csv
    },
    # MSCI World. 2026-07~08 Sharpe 사다리 채택 스택 (docs/experiments/mxwo_sharpe_ladder_20260729.md).
    # 실측 net Sharpe 0.661 / MDD -1.99% (롱숏 ±20% 배포 기준, 거래세 반영, 2026-09-16 Tier 1 look-ahead 제거 후; 구 0.734).
    "MXWO": {
        "transaction_cost_bps": 10.0,  # 선진국 대형주 실집행 기준 10bp (2026-07-30 사용자 지정). 국가별 거래세는 COUNTRY_TAX_BPS 로 별도 계상
        "apply_country_tax": True,     # 등록지 기준 국가별 거래세 적용 (실측 회계 전용, 2026-08-12)
        "erc_shrinkage": 0.2,          # 0.2 채택 (2026-08-07 전구간 0~1 스윕: 단조 하강 곡선, 실측 net 0.761->0.782/MDD -3.71%. 0은 특이 cov(n<p)+집중 12%라 회피, 0.1~0.3 안전지대 내부점)
        "ts_mom_scale": 0.2,           # 0.2 채택 (2026-08-10 전구간 0~1 스윕: 0 방향 단조 우위, 실측 net 0.782->0.840/MDD -3.50%. 0은 완전 제외 벼랑 규칙+회전 4.4x+최근구간 최약이라 회피, 0.2 = 내부 안전점)
        "use_cluster_dedup": False,    # 롤링 IS와 winner_median 궁합 문제로 off (2026-07-28 A/B: on -0.12 / off +0.41 Sharpe)
        "is_window_months": 48,        # 롤링 IS 윈도우 (개월). w36~72 스윕 중 내부 고원점 w48 채택 (2026-07-28, full Sharpe 0.16->0.41)
        "selection_hysteresis": 0.25,  # 0.25 채택 (2026-07-29; 구 0.5)
        "weight_rebal_months": 1,      # 월간 가중 리밸 채택 (2026-07-29)
        "min_coverage_pct": 0.10,      # 팩터 최소 단면 커버리지 (유니버스 대비 유효 관측 비율, IS 기준). 은행 전용 등 초저커버리지 팩터 제외 (2026-07-27 A/B 채택)
        "sector_short_cap": 0.15,      # 섹터별 숏 gross 상한 (전체 숏 gross 대비, 2026-07-30 채택 — 2020-11형 숏 crowding 완화. 실측 스택 net 0.692/MDD -4.95/Calmar 0.352)
        "mp_target_gross": 0.40,       # MP 배포 목표 총 gross (롱+|숏|). 설정 시 매 시점 배수를 자동 산출해 노출을 고정 (netting 변동 흡수). None 이면 data/{BENCHMARK}/mp_multiplier.csv 의 시점별 수동 배수 사용. 0.40 = 롱 +20% / 숏 -20% (2026-08-19 사용자 지정)
    },
}

PIPELINE_PARAMS = {**_COMMON_PARAMS, **_UNIVERSE_PARAMS[BENCHMARK]}

# ---------------------------------------------------------------------------
# 국가별 증권거래세 (2026-08-12 도입). 단위: (매수bp, 매도bp). 미등재국 = 0.
#
# 수수료(transaction_cost_bps=10bp)와 별도로 부과되는 법정 거래세.
# 기준: 종목의 법인등록국 (SDRT 등 대부분 세목이 상장지가 아닌 등록지를 따름).
# 채택 가정 (사용자 승인 2026-08-12):
#   - 스위스(0.15% 양방)·벨기에 TOB(0.35% 양방)는 제외 — 스위스 인지세는 스위스
#     증권딜러 경유 시에만, 벨기에 TOB는 벨기에 거주자에게만 부과.
#   - 프랑스·스페인의 시총 €1bn 초과 조건과 아일랜드의 €1bn 미만 면제 조건은
#     전 종목이 임계를 넘는 것으로 가정 (MSCI World 구성종목은 대부분 대형주).
# 한계: 버뮤다/케이맨/마카오 등록 기업이 홍콩 상장인 경우 실제로는 홍콩 인지세
#   대상이나 등록지 기준이라 미반영 (해당 비중 ~0.9% -> 편도 오차 ~0.09bp).
COUNTRY_TAX_BPS = {
    "GBR": (50.0, 0.0),      # SDRT 0.5% 매수 (영국 법인등록 기업). DR 은 1.5% 별도
    "IRL": (100.0, 0.0),     # 인지세 1.0% 매수
    "FRA": (40.0, 0.0),      # FTT 0.4% 매수 (2025-04 0.3% -> 0.4% 인상)
    "ESP": (20.0, 0.0),      # FTT 0.2% 매수
    "ITA": (20.0, 0.0),      # FTT 0.2% 매수 (2026-01 0.1% -> 0.2% 인상, 규제시장)
    "HKG": (10.0, 10.0),     # 인지세 0.1% 양방 (2023-11-17 이후)
    "ZAF": (25.0, 0.0),      # STT 0.25% 매수
    "USA": (0.0, 0.206),     # SEC Section 31 $20.60/백만 매도 (2026-04-04 부터)
}
