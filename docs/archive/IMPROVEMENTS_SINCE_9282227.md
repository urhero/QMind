# 9282227 대비 개선 사항 정리

**Base commit**: `92822273d5b4f88df87e2e4abb686f329f9e6014` ("Refactor module structure, rename files, add real data integration tests")
**Current HEAD**: PR #6 (`claude/funny-bell-1d41f7`, commit `075d7ad`)
**Range**: 116 commits, ~50,000 insertions / 13,000 deletions

---

## 한눈에 보는 변화 (수치)

| 지표 | 9282227 | 현재 | 변화 |
|---|---|---|---|
| `service/` Python 파일 | 8 | **21** | +13 (신규 모듈 + 신규 패키지) |
| `tests/test_unit/` 파일 | 4 | **14** | +10 |
| pytest 테스트 수 | ~25 | **223** | **+198** (~9배) |
| `PIPELINE_PARAMS` 키 | 0 (없음) | **16** | 비즈니스 파라미터 중앙 관리 신설 |
| CLI 명령 | 2 (`download`, `mp`) | **3** + 풍부한 옵션 | `backtest` 신규, 옵션 ~10개 추가 |
| `docs/experiments/` 산출물 | 0 | **12** | 실험 리포트 인프라 신설 |
| 커밋 수 | – | 116 commits | – |

---

## 1. 신규 패키지 — `service/backtest/` (Walk-Forward Engine 전체)

9282227 에는 **backtest 자체가 없었음**. 단순 일회성 mp 산출만 가능.

신규 모듈 5개:

| 파일 | 역할 |
|---|---|
| `walk_forward_engine.py` | Expanding Window 백테스트 오케스트레이터 (3-tier 리밸런싱) |
| `factor_selection.py` | Hierarchical Clustering dedup, shrunk t-stat, Newey-West |
| `data_slicer.py` | IS / OOS 날짜 기반 데이터 분할 |
| `result_stitcher.py` | OOS 결과 접합 + 성과 계산 (`WalkForwardResult`) |
| `overfit_diagnostics.py` | 5지표 진단 (Funnel Value-Add, OOS Percentile, Strict Jaccard, IS-OOS Rank Corr, Deflation Ratio) |

CLI 진입점: `python main.py backtest 2009-12-31 2026-03-31`

---

## 2. Production 파이프라인 강화 — `service/pipeline/`

### 신규 파일
- **`weight_history.py`** — Production 의 EMA history 관리 (load/save/blend). `output/mp_weight_history/` 디렉토리에 분기마다 누적
- **`benchmark_comparison.py`** — Constrained EW vs 1/N 동일가중 벤치마크 비교 (`mp --benchmark`)

### 변경된 `model_portfolio.py` 핵심 개선
- **Hierarchical Clustering 통합** — `use_cluster_dedup=True` 시 18 cluster × top 3 dedup
- **EMA Smoothing 통합** — `turnover_smoothing_alpha=0.1` 시 분기 가중치 EMA 블렌딩
- **`pipeline_params` dict 주입** — 비즈니스 파라미터 중앙화 (이전: 하드코딩 분산)
- **테스트 모드 출력 분리** — `_test{suffix}.csv` 명명 규칙

---

## 3. Configuration 시스템 — `PIPELINE_PARAMS` 신설

9282227 의 `config.py` 는 **DB 연결 정보만** 담음 (`PARAM`).
현재는 비즈니스 파라미터를 중앙 관리하는 `PIPELINE_PARAMS` 가 신설됨.

```python
# 신규 16 키 (현재)
PIPELINE_PARAMS = {
    "style_cap": 0.25,                   # 스타일별 최대 비중
    "transaction_cost_bps": 30.0,         # 거래비용
    "top_factor_count": 50,               # Top-N 선정 수
    "spread_threshold_pct": 0.10,         # L/N/S 라벨링 임계값
    "min_sector_stocks": 10,              # 섹터-날짜 최소 종목
    "max_zero_return_months": 10,
    "backtest_start": "2009-12-31",
    "backtest_end": "2026-03-31",
    "min_downside_obs": 20,
    "optimization_mode": "equal_weight",  # equal_weight / hardcoded
    "factor_ranking_method": "tstat",     # tstat / shrunk_tstat / cagr
    "use_cluster_dedup": True,            # 신규: clustering on/off
    "n_clusters": 18,
    "per_cluster_keep": 3,
    "newey_west_lag": 3,                  # 신규: NW 보정 lag (진단용)
    "turnover_smoothing_alpha": 1.0,      # 신규: EMA blending (1.0=off, 0.1 권장)
}
```

운영 함의: Bloomberg Optimizer 입력 산출물의 동작이 **config.py 한 파일** 으로 완전 통제 가능.

---

## 4. CLI 명령 + 옵션 확장

### `download` 명령
| 9282227 | 현재 |
|---|---|
| `download <start> <end>` | + `--incremental` (4월말 only 추가 다운) |
|  | + `--no-validate` |
|  | + Year-split parquet 자동 무결성 검증 |

### `mp` 명령
| 9282227 | 현재 |
|---|---|
| `mp <start> <end>` | + `mp test test_data.csv` (소량 검증) |
|  | + `--benchmark` (Constrained EW vs EW 비교) |
|  | + `--report` (리포트만 생성 후 종료) |

### `backtest` 명령 (**완전 신규**)
```bash
python main.py backtest 2009-12-31 2026-03-31 \
  --min-is-months 36 \
  --factor-rebal-months 6 \
  --weight-rebal-months 3 \
  --top-factors 50 \
  --turnover-alpha 1.0
```

5개 전용 옵션 + test 모드 (`backtest test test_data.csv --min-is-months 4`).

---

## 5. Walk-Forward 백테스트 인프라 — 핵심 신규 기능

### 3-Tier 리밸런싱 시스템
| Tier | 작업 | 빈도 (default) |
|---|---|---|
| **Tier 1** | 팩터 선정 규칙 학습 (dropped sectors, L/N/S 라벨) | 6개월 (가능: 3 / 12) |
| **Tier 2** | Top-N 선정 + 가중치 최적화 + (선택) EMA 블렌딩 | 3개월 |
| **Tier 3** | OOS 월간 수익률 기록 | 매월 |

### 5-지표 과적합 진단
1. **Funnel Value-Add Test** — A(전체)/B(Top-50)/C(Constrained EW) CAGR 비교 → `NORMAL` / `OPTIMIZATION_OVERFIT` / `FILTER_OVERFIT` 판정
2. **OOS Percentile Tracking** — 선정 팩터 OOS 백분위 평균 (≥0.60 시 WARN)
3. **Strict Jaccard Index** — weight>0 팩터의 분기간 안정성
4. **IS-OOS Rank Correlation** — 팩터 순위 유지력 (Spearman)
5. **Deflation Ratio** — OOS CAGR / IS CAGR (보조 지표)

### Hierarchical Clustering Dedup
- IS 팩터 L-S 수익률 상관행렬 → `1 - |corr|` distance
- Average linkage hierarchical → 18 cluster
- Cluster 별 top 3 → top-50 cap → 자동 다양성 보장

### Sprint 1-A: Shrunk t-stat (James-Stein)
- 스타일 그룹 내 t-stat 평균 쪽으로 shrinkage
- `factor_ranking_method="shrunk_tstat"` 옵션

### Sprint 1-C: Newey-West t-stat
- Bartlett kernel 자기상관 보정 (lag=3)
- `meta_data.csv` 진단 컬럼 (랭킹 교체 X)

---

## 6. 테스트 인프라 확장

### 9282227 시점
- `test_calculate_factor_stats.py`
- `test_downside_correlation.py`
- `test_prepend_start_zero.py`
- `test_simulate_constrained_weights.py`
- `test_pipeline_real_data.py` (integration)

### 현재 — 14 파일, 223 테스트

| 신규 단위 테스트 | 검증 대상 |
|---|---|
| `test_walk_forward_engine_override.py` | Engine override 회귀 안전 |
| `test_factor_selection.py` | Cluster dedup, shrunk t-stat, NW |
| `test_data_slicer.py` | IS/OOS 슬라이싱 off-by-one |
| `test_overfit_diagnostics.py` | 5지표 진단 |
| `test_benchmark_comparison.py` | EW 벤치마크 비교 |
| `test_filter_and_label_factors.py` | 섹터 필터 + L/N/S |
| `test_optimize_constrained_weights.py` | style_cap 재분배 |
| `test_parquet_io.py` | Year-split parquet I/O |
| `test_weight_construction.py` | Long/Short DF, vectorized return |
| `test_weight_history.py` | EMA blending (load/save/blend) |
| `test_cluster_turnover_experiment.py` | 실험 러너 헬퍼 33 tests |

CI: `.github/workflows/test.yml` 신규 (PR 마다 pytest 자동 실행)

---

## 7. 보안 + 인프라

| 9282227 | 현재 |
|---|---|
| `.env` 미사용 (DB 비번 코드에 노출 위험) | `.env` + `.env.example` (python-dotenv) |
| 없음 | `.pre-commit-config.yaml` (`detect-secrets`) |
| 없음 | `.secrets.baseline` (allowlist) |
| 없음 | `db/factor_query.py` SQL allowlist 검증 |
| 없음 | `service/download/download_validation.py` post-download 무결성 검증 |

---

## 8. 실험 인프라 + 분석 — `docs/experiments/` (완전 신규)

`scripts/run_cluster_turnover_experiment.py` (신규, 700+ 라인) — **43 케이스 광역 sweep** 자동화

### 산출 분석 (12 파일)
- `cluster_turnover_20260425.md` — 43 케이스 통합 REPORT (Sharpe, Verdict, Funnel, Drawdown 표)
- `cluster_turnover_20260425_summary.csv` — 머신 친화 요약
- `period_sharpe_analysis.md/csv` — 기간별 Sharpe (p1/p2/p3 변동)
- `drawdown_analysis.md/csv` — Drawdown episode 추적 (peak / trough / recovery)
- `cluster_size_diagnostics.md/csv/png` — 54 Tier 2 fire 시계열 cluster size 분포
- `cluster_membership_2026-03-31.csv` — 18 cluster 별 factor 멤버십
- `executive_summary.md` — CEO/PM 1장 요약

### 발견 (43 케이스)
- **n_clusters sweet spot**: 18~30 (8/10/12/15 → OPT_OVERFIT, 40 → FILTER_OVERFIT)
- **smoothing α saturation**: 0.1 부근 (Sharpe 0.728)
- **clustering 후 style_cap 효과 거의 없음** (cap 0.5/1.0 결과 동일)
- **min_is_months 는 모델에 영향 없음** (expanding window — Sharpe 차이는 OOS 시작점 sample bias)
- **t-stat 이 ranking 베스트** (shrunk_tstat / cagr 모두 악화)
- **현재 baseline 은 historical artifact**: 2014-2017 강세에 의존, 2023~ Sharpe 0.27 vs combo 0.99

### 최종 추천
- **Sharpe 우선**: `combo_18_0.1` (Sharpe 0.728, MDD -2.86%, verdict OK)
- **CAGR 우선**: `baseline_nocap_0.3` (CAGR 2.58%, MDD -8.80%, 규제 허용 시)

---

## 9. 데이터 인프라 — `data/` parquet

| 9282227 | 현재 |
|---|---|
| 없음 | `MXCN1A_factor_2009.parquet` ~ `2026.parquet` (year-split) |
| 없음 | `MXCN1A_mreturn.parquet` |
| 없음 | `factor_info.csv` (588 factor 메타) |
| 없음 | `hardcoded_weights.csv` (production 고정 가중치) |

연도별 분할 + 자동 무결성 검증 (`download_validation.py`).

---

## 10. 문서 체계 정립

| 문서 | 9282227 | 현재 |
|---|---|---|
| `README.md` | 116 lines (간단) | **543 lines** (Funnel 구조 + 단계별 + CLI + 백테스트) |
| `CLAUDE.MD` | 없음 | **429 lines** (검증 프로세스 + 컨벤션 + 환경) |
| `research.md` | 없음 | Deep Dive 신규 |
| `docs/VARIABLE_FLOW.md` | 102 lines | 166 lines (mp 변수 흐름 시각화) |
| `docs/backtest_results_2009_2026.md` | 없음 | 백테스트 진단 결과 |
| `docs/superpowers/specs/` | 없음 | 디자인 스펙 (브레인스토밍 결과) |
| `docs/superpowers/plans/` | 없음 | 구현 플랜 (TDD 기반) |

---

## 11. Production 적용 가이드 (PR #6 기준)

### 1단계: config 변경
```python
# config.py PIPELINE_PARAMS
"use_cluster_dedup": True,         # ← 이미 디폴트 (commit 7f1b5d5)
"turnover_smoothing_alpha": 0.1,   # ← 운영팀 결정 후 0.1 로 (현재 1.0)
```

### 2단계: 실행
```bash
# 4월말 데이터 다운로드 (5월 4일 이후 권장)
python main.py download 2009-12-31 2026-04-30 --incremental

# MP 산출 (cluster + EMA 자동 적용)
python main.py mp 2009-12-31 2026-04-30
```

### 3단계: Bloomberg Optimizer 입력
- `output/pivoted_total_agg_wgt_2026-04-30.csv` → Optimizer
- TE / 제약 위반 사전 확인 (dry-run)
- 첫 적용 시 종목 turnover ~80% 가능 → phase-in 검토

### 자동 효과 (PR 적용 후)
- 매 분기 mp 실행 시 cluster dedup 자동 적용 (51개 → ~40개로 dedup)
- EMA on 시 분기 가중치 자동 누적 블렌딩 (`output/mp_weight_history/` 자동 관리)
- Backtest 명령으로 과적합 진단 5지표 자동 산출

---

## 12. Backward Compatibility

PR #6 의 모든 production 변경은 **회귀 안전** 검증됨:
- `use_cluster_dedup=False` (이전 디폴트) 시 byte-identical
- `turnover_smoothing_alpha=1.0` (디폴트) 시 EMA 비활성, 기존 동작
- `WalkForwardEngine(pipeline_params_override=None)` 시 CLI 경로 무영향

CLAUDE.md 검증 프로세스 (A: pytest, B: 실데이터 mp diff) 모두 통과.

---

## 핵심 비교

| | 9282227 | PR #6 |
|---|---|---|
| **목적** | "200+ 팩터 → top 50 → 1/N + style_cap → MP CSV" | "200+ 팩터 → cluster dedup → top 50 → EMA blend → MP CSV + 백테스트 진단" |
| **Production 출력 신뢰도** | 단발성 mp, 검증 어려움 | Walk-Forward 백테스트로 13년 OOS 검증 + 과적합 5지표 |
| **운영 안정성** | 매 mp 실행 시 큰 변화 가능 (turnover 격변) | EMA 누적으로 점진 변화 (turnover 0.084 → 0.057, 32%↓) |
| **다양성** | 단순 cagr top-50 (모멘텀 변형 8개 등 중복 가능) | Cluster dedup 으로 자동 다양화 (18 그룹) |
| **검증 도구** | 4 unit tests (~25개) | 223 unit tests + 43 case backtest sweep |
| **문서** | README 116 lines | README 543 + 12 experiment 분석 + executive summary |

**한 줄 요약**: 9282227 의 "MP 산출 파이프라인" 에서 → **"백테스트 검증된 + 과적합 진단된 + 다양성 보장된 + 운영 안정적인 production 시스템"** 으로 진화.
