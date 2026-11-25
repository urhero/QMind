# Cluster Dedup x Turnover Smoothing 실험 리포트

- 실행일: 2026-04-26 13:29:43
- Git SHA: `f0d2021`
- 백테스트 기간: `2009-12-31` ~ `2026-03-31`
- 공통 파라미터: min_is=36, factor_rebal=6, weight_rebal=3, top=50, ranking=tstat

> **읽기 전 주의 (2026-11-25 사후 검토):** (1) n ≤ 16 케이스는 `per_cluster_keep` 이 자동으로 올라갔다 (`cluster_12` = 5 등) — n_clusters 효과와 섞여 있고, `cluster_18_keep1`·`keep2` 는 keep3 과 같은 실행이다. (2) `OPTIMIZATION_OVERFIT` 은 현행 기준 제약 비용(`CONSTRAINT_DRAG`)이며 과적합 증거가 아니다 — §4 추천은 이 판정으로 걸러낸 결과다. (3) 스무딩 케이스의 회전율은 메모리 비중 기준이라 실거래 회전율이 아니다. 상세: `docs/superpowers/specs/2026-04-24-cluster-turnover-experiment-design.md` §9.

## 1. 성과 요약 (OOS, Net-of-cost)

| 케이스 | CAGR | Net CAGR | Sharpe | MDD | Calmar | Avg Turnover | dCAGR vs base | dTurnover vs base |
|---|---|---|---|---|---|---|---|---|
| `baseline` | 2.31% | 2.25% | 0.632 | -7.91% | 0.292 | 0.045 | +0.00% | +0.000 |
| `cluster_18` | 1.39% | 1.29% | 0.693 | -2.93% | 0.475 | 0.084 | -0.92% | +0.039 |
| `cluster_12` | 2.08% | 1.96% | 0.866 | -3.41% | 0.610 | 0.098 | -0.22% | +0.053 |
| `cluster_24` | 1.23% | 1.14% | 0.611 | -3.97% | 0.310 | 0.073 | -1.08% | +0.028 |
| `smooth_0.7` | 2.31% | 2.27% | 0.633 | -7.96% | 0.291 | 0.040 | +0.01% | -0.005 |
| `smooth_0.5` | 2.32% | 2.28% | 0.633 | -8.00% | 0.290 | 0.037 | +0.01% | -0.009 |
| `combo_18_0.7` | 1.41% | 1.33% | 0.706 | -2.90% | 0.488 | 0.074 | -0.89% | +0.029 |
| `combo_18_0.5` | 1.43% | 1.35% | 0.714 | -2.88% | 0.497 | 0.068 | -0.88% | +0.023 |
| `baseline_nocap` | 2.55% | 2.51% | 0.675 | -8.64% | 0.295 | 0.038 | +0.25% | -0.007 |
| `cluster_nocap` | 1.38% | 1.28% | 0.691 | -2.90% | 0.474 | 0.082 | -0.93% | +0.037 |
| `combo_nocap_0.5` | 1.41% | 1.33% | 0.708 | -2.84% | 0.496 | 0.067 | -0.90% | +0.022 |
| `cluster_8` | 1.48% | 1.36% | 0.549 | -6.81% | 0.217 | 0.094 | -0.83% | +0.049 |
| `cluster_15` | 1.68% | 1.57% | 0.760 | -3.14% | 0.534 | 0.090 | -0.63% | +0.045 |
| `cluster_20` | 1.21% | 1.12% | 0.597 | -3.04% | 0.397 | 0.077 | -1.10% | +0.031 |
| `cluster_30` | 1.43% | 1.35% | 0.644 | -3.26% | 0.438 | 0.065 | -0.88% | +0.020 |
| `cluster_40` | 1.12% | 1.04% | 0.456 | -4.88% | 0.230 | 0.068 | -1.18% | +0.023 |
| `cluster_18_keep1` | 1.39% | 1.29% | 0.693 | -2.93% | 0.475 | 0.084 | -0.92% | +0.039 |
| `cluster_18_keep2` | 1.39% | 1.29% | 0.693 | -2.93% | 0.475 | 0.084 | -0.92% | +0.039 |
| `cluster_18_keep5` | 1.78% | 1.69% | 0.814 | -3.45% | 0.517 | 0.078 | -0.52% | +0.033 |
| `combo_18_cap0.5` | 1.41% | 1.33% | 0.708 | -2.84% | 0.496 | 0.067 | -0.90% | +0.022 |
| `combo_18_0.3` | 1.45% | 1.37% | 0.722 | -2.87% | 0.506 | 0.062 | -0.86% | +0.017 |
| `combo_18_0.2` | 1.46% | 1.39% | 0.725 | -2.86% | 0.510 | 0.060 | -0.85% | +0.014 |
| `combo_18_0.1` | 1.47% | 1.40% | 0.728 | -2.86% | 0.514 | 0.057 | -0.84% | +0.011 |
| `baseline_nocap_0.5` | 2.57% | 2.53% | 0.678 | -8.75% | 0.294 | 0.032 | +0.27% | -0.014 |
| `baseline_nocap_0.3` | 2.58% | 2.55% | 0.679 | -8.80% | 0.294 | 0.029 | +0.28% | -0.016 |
| `combo_nocap_0.3` | 1.43% | 1.35% | 0.713 | -2.82% | 0.505 | 0.061 | -0.88% | +0.016 |
| `combo_nocap_0.1` | 1.44% | 1.38% | 0.717 | -2.80% | 0.515 | 0.056 | -0.86% | +0.010 |
| `cluster_10` | 2.00% | 1.88% | 0.805 | -5.37% | 0.373 | 0.102 | -0.30% | +0.056 |
| `cluster_n50_keep1` | 0.33% | 0.25% | 0.180 | -4.44% | 0.074 | 0.071 | -1.98% | +0.025 |
| `cluster_n30_keep1_top30` | 0.78% | 0.67% | 0.381 | -3.51% | 0.223 | 0.096 | -1.52% | +0.050 |
| `cluster_n18_keep1_top18` | 0.81% | 0.67% | 0.350 | -3.38% | 0.241 | 0.122 | -1.49% | +0.076 |
| `baseline_shrunk` | 2.02% | 1.96% | 0.561 | -8.17% | 0.248 | 0.053 | -0.28% | +0.008 |
| `baseline_cagr` | 2.35% | 2.28% | 0.606 | -6.91% | 0.340 | 0.061 | +0.04% | +0.016 |
| `combo_18_0.1_shrunk` | 1.25% | 1.19% | 0.619 | -3.17% | 0.396 | 0.054 | -1.05% | +0.008 |
| `combo_18_0.1_cagr` | 1.24% | 1.17% | 0.602 | -2.31% | 0.536 | 0.057 | -1.07% | +0.012 |
| `baseline_rebal3` | 2.16% | 2.11% | 0.596 | -7.72% | 0.280 | 0.047 | -0.14% | +0.001 |
| `baseline_rebal12` | 2.45% | 2.39% | 0.674 | -7.75% | 0.316 | 0.045 | +0.14% | -0.000 |
| `combo_18_0.1_rebal3` | 0.94% | 0.83% | 0.455 | -4.47% | 0.211 | 0.094 | -1.37% | +0.049 |
| `combo_18_0.1_rebal12` | 1.37% | 1.33% | 0.681 | -2.59% | 0.529 | 0.035 | -0.94% | -0.010 |
| `baseline_is24` | 2.53% | 2.48% | 0.707 | -7.91% | 0.320 | 0.047 | +0.23% | +0.002 |
| `baseline_is60` | 2.89% | 2.85% | 0.782 | -7.91% | 0.366 | 0.040 | +0.59% | -0.006 |
| `combo_18_0.1_is24` | 1.60% | 1.53% | 0.801 | -2.86% | 0.558 | 0.058 | -0.71% | +0.013 |
| `combo_18_0.1_is60` | 1.85% | 1.78% | 0.946 | -2.86% | 0.646 | 0.052 | -0.46% | +0.006 |

## 2. 과적합 진단

| 케이스 | Verdict | Funnel (A/B/C CAGR) | OOS Pctile (lower=better) | Jaccard (higher=better) | Rank Corr (higher=better) | Deflation |
|---|---|---|---|---|---|---|
| `baseline` | **OPTIMIZATION_OVERFIT** | OPT_OVERFIT (B>C>A) (1.11%/2.55%/2.31%) | 47.21% [OK] | 0.801 | 0.136 | 0.386 |
| `cluster_18` | **OK** | OK (C>B>A) (1.11%/1.38%/1.39%) | 50.04% [OK] | 0.646 | 0.121 | 0.363 |
| `cluster_12` | **OPTIMIZATION_OVERFIT** | OPT_OVERFIT (B>C>A) (1.11%/2.12%/2.08%) | 49.04% [OK] | 0.614 | 0.163 | 0.491 |
| `cluster_24` | **OK** | OK (C>B>A) (1.11%/1.22%/1.23%) | 50.14% [OK] | 0.681 | 0.109 | 0.313 |
| `smooth_0.7` | **OPTIMIZATION_OVERFIT** | OPT_OVERFIT (B>C>A) (1.11%/2.55%/2.31%) | 47.36% [OK] | 0.838 | 0.136 | 0.388 |
| `smooth_0.5` | **OPTIMIZATION_OVERFIT** | OPT_OVERFIT (B>C>A) (1.11%/2.55%/2.32%) | 47.36% [OK] | 0.838 | 0.136 | 0.389 |
| `combo_18_0.7` | **OK** | OK (C>B>A) (1.11%/1.38%/1.41%) | 50.04% [OK] | 0.721 | 0.121 | 0.369 |
| `combo_18_0.5` | **OK** | OK (C>B>A) (1.11%/1.38%/1.43%) | 50.04% [OK] | 0.721 | 0.121 | 0.374 |
| `baseline_nocap` | **OK** | OK (C>B>A) (1.11%/2.55%/2.55%) | 47.21% [OK] | 0.801 | 0.136 | 0.414 |
| `cluster_nocap` | **OK** | OK (C>B>A) (1.11%/1.38%/1.38%) | 50.04% [OK] | 0.646 | 0.121 | 0.363 |
| `combo_nocap_0.5` | **OK** | OK (C>B>A) (1.11%/1.38%/1.41%) | 50.04% [OK] | 0.721 | 0.121 | 0.372 |
| `cluster_8` | **OPTIMIZATION_OVERFIT** | OPT_OVERFIT (B>C>A) (1.11%/2.00%/1.48%) | 48.95% [OK] | 0.640 | 0.192 | 0.300 |
| `cluster_15` | **OPTIMIZATION_OVERFIT** | OPT_OVERFIT (B>C>A) (1.11%/1.68%/1.68%) | 49.49% [OK] | 0.627 | 0.144 | 0.392 |
| `cluster_20` | **OK** | OK (C>B>A) (1.11%/1.18%/1.21%) | 50.30% [OK] | 0.671 | 0.116 | 0.314 |
| `cluster_30` | **OK** | OK (C>B>A) (1.11%/1.41%/1.43%) | 49.92% [OK] | 0.700 | 0.102 | 0.351 |
| `cluster_40` | **FILTER_OVERFIT** | FILTER_OVERFIT (A>B) (1.11%/1.11%/1.12%) | 50.14% [OK] | 0.691 | 0.111 | 0.253 |
| `cluster_18_keep1` | **OK** | OK (C>B>A) (1.11%/1.38%/1.39%) | 50.04% [OK] | 0.646 | 0.121 | 0.363 |
| `cluster_18_keep2` | **OK** | OK (C>B>A) (1.11%/1.38%/1.39%) | 50.04% [OK] | 0.646 | 0.121 | 0.363 |
| `cluster_18_keep5` | **OPTIMIZATION_OVERFIT** | OPT_OVERFIT (B>C>A) (1.11%/1.80%/1.78%) | 49.16% [OK] | 0.664 | 0.124 | 0.409 |
| `combo_18_cap0.5` | **OK** | OK (C>B>A) (1.11%/1.38%/1.41%) | 50.04% [OK] | 0.721 | 0.121 | 0.372 |
| `combo_18_0.3` | **OK** | OK (C>B>A) (1.11%/1.38%/1.45%) | 50.04% [OK] | 0.721 | 0.121 | 0.379 |
| `combo_18_0.2` | **OK** | OK (C>B>A) (1.11%/1.38%/1.46%) | 50.04% [OK] | 0.721 | 0.121 | 0.381 |
| `combo_18_0.1` | **OK** | OK (C>B>A) (1.11%/1.38%/1.47%) | 50.04% [OK] | 0.721 | 0.121 | 0.384 |
| `baseline_nocap_0.5` | **OK** | OK (C>B>A) (1.11%/2.55%/2.57%) | 47.36% [OK] | 0.838 | 0.136 | 0.418 |
| `baseline_nocap_0.3` | **OK** | OK (C>B>A) (1.11%/2.55%/2.58%) | 47.36% [OK] | 0.838 | 0.136 | 0.419 |
| `combo_nocap_0.3` | **OK** | OK (C>B>A) (1.11%/1.38%/1.43%) | 50.04% [OK] | 0.721 | 0.121 | 0.376 |
| `combo_nocap_0.1` | **OK** | OK (C>B>A) (1.11%/1.38%/1.44%) | 50.04% [OK] | 0.721 | 0.121 | 0.381 |
| `cluster_10` | **OPTIMIZATION_OVERFIT** | OPT_OVERFIT (B>C>A) (1.11%/2.18%/2.00%) | 48.73% [OK] | 0.602 | 0.166 | 0.470 |
| `cluster_n50_keep1` | **FILTER_OVERFIT** | FILTER_OVERFIT (A>B) (1.11%/0.32%/0.33%) | 51.70% [OK] | 0.694 | 0.059 | 0.101 |
| `cluster_n30_keep1_top30` | **FILTER_OVERFIT** | FILTER_OVERFIT (A>B) (1.11%/0.84%/0.78%) | 51.08% [OK] | 0.599 | 0.040 | 0.235 |
| `cluster_n18_keep1_top18` | **FILTER_OVERFIT** | FILTER_OVERFIT (A>B) (1.11%/0.78%/0.81%) | 50.78% [OK] | 0.514 | 0.057 | 0.210 |
| `baseline_shrunk` | **OPTIMIZATION_OVERFIT** | OPT_OVERFIT (B>C>A) (1.11%/2.48%/2.02%) | 47.32% [OK] | 0.819 | 0.152 | 0.349 |
| `baseline_cagr` | **OPTIMIZATION_OVERFIT** | OPT_OVERFIT (B>C>A) (1.11%/2.45%/2.35%) | 47.39% [OK] | 0.755 | 0.091 | 0.357 |
| `combo_18_0.1_shrunk` | **OPTIMIZATION_OVERFIT** | OPT_OVERFIT (B>C>A) (1.11%/1.27%/1.25%) | 50.13% [OK] | 0.735 | 0.135 | 0.332 |
| `combo_18_0.1_cagr` | **FILTER_OVERFIT** | FILTER_OVERFIT (A>B) (1.11%/1.03%/1.24%) | 50.55% [OK] | 0.717 | 0.117 | 0.307 |
| `baseline_rebal3` | **OPTIMIZATION_OVERFIT** | OPT_OVERFIT (B>C>A) (0.99%/2.40%/2.16%) | 47.21% [OK] | 0.792 | 0.130 | 0.359 |
| `baseline_rebal12` | **OPTIMIZATION_OVERFIT** | OPT_OVERFIT (B>C>A) (1.06%/2.70%/2.45%) | 46.84% [OK] | 0.804 | 0.133 | 0.410 |
| `combo_18_0.1_rebal3` | **FILTER_OVERFIT** | FILTER_OVERFIT (A>B) (0.99%/0.94%/0.94%) | 50.30% [OK] | 0.603 | 0.121 | 0.226 |
| `combo_18_0.1_rebal12` | **OK** | OK (C>B>A) (1.06%/1.23%/1.37%) | 50.29% [OK] | 0.816 | 0.138 | 0.358 |
| `baseline_is24` | **OPTIMIZATION_OVERFIT** | OPT_OVERFIT (B>C>A) (1.27%/2.76%/2.53%) | 47.09% [OK] | 0.793 | 0.134 | 0.424 |
| `baseline_is60` | **OPTIMIZATION_OVERFIT** | OPT_OVERFIT (B>C>A) (1.77%/3.21%/2.89%) | 47.24% [OK] | 0.824 | 0.140 | 0.485 |
| `combo_18_0.1_is24` | **OK** | OK (C>B>A) (1.27%/1.52%/1.60%) | 50.04% [OK] | 0.712 | 0.134 | 0.417 |
| `combo_18_0.1_is60` | **FILTER_OVERFIT** | FILTER_OVERFIT (A>B) (1.77%/1.74%/1.85%) | 50.47% [OK] | 0.742 | 0.139 | 0.482 |

> *Deflation Ratio = OOS CAGR / IS CAGR. OOS 기간이 짧으면 단독 판단 금지.*

## 3. 해석

### 3.1 Clustering 효과 (baseline vs cluster_18)
- dCAGR: -0.92%, dSharpe: +0.060, dTurnover: +0.039
- Verdict: baseline=`OPTIMIZATION_OVERFIT`, cluster_18=`OK`

### 3.2 n_clusters 민감도 (cluster_12 / cluster_18 / cluster_24)
- `cluster_12`: CAGR 2.08%, Sharpe 0.866, Turnover 0.098, Verdict `OPTIMIZATION_OVERFIT`
- `cluster_18`: CAGR 1.39%, Sharpe 0.693, Turnover 0.084, Verdict `OK`
- `cluster_24`: CAGR 1.23%, Sharpe 0.611, Turnover 0.073, Verdict `OK`

### 3.3 Turnover Smoothing 단독 효과 (baseline / smooth_0.7 / smooth_0.5)
- `baseline` (alpha=1.0): CAGR 2.31%, Turnover 0.045
- `smooth_0.7` (alpha=0.7): CAGR 2.31%, Turnover 0.040
- `smooth_0.5` (alpha=0.5): CAGR 2.32%, Turnover 0.037

### 3.4 조합 효과 (cluster_18 / combo_18_0.7 / combo_18_0.5)
- `cluster_18` (alpha=1.0): CAGR 1.39%, Sharpe 0.693, Turnover 0.084
- `combo_18_0.7` (alpha=0.7): CAGR 1.41%, Sharpe 0.706, Turnover 0.074
- `combo_18_0.5` (alpha=0.5): CAGR 1.43%, Sharpe 0.714, Turnover 0.068

> *§3 자동 해석은 방향성/수치만 제시. 도메인 해석은 사람이 보강.*

## 3-2. Phase 5 — Ranking / factor_rebal / min_is sweep

| Group | 케이스 | CAGR | Sharpe | Verdict | vs ref |
|---|---|---|---|---|---|
| ranking | `baseline_shrunk` | 2.02% | 0.561 | OPT_OVERFIT | -0.07 vs `baseline` |
| ranking | `baseline_cagr` | 2.35% | 0.606 | OPT_OVERFIT | -0.03 |
| ranking | `combo_18_0.1_shrunk` | 1.25% | 0.619 | OPT_OVERFIT | -0.11 vs `combo_18_0.1` |
| ranking | `combo_18_0.1_cagr` | 1.24% | 0.602 | FILTER_OVERFIT | -0.13 |
| factor_rebal | `baseline_rebal3` (3개월) | 2.16% | 0.596 | OPT_OVERFIT | -0.04 |
| factor_rebal | `baseline_rebal12` (12개월) | 2.45% | 0.674 | OPT_OVERFIT | +0.04 |
| factor_rebal | `combo_18_0.1_rebal3` | 0.94% | 0.455 | FILTER_OVERFIT | -0.27 |
| factor_rebal | `combo_18_0.1_rebal12` | 1.37% | 0.681 | OK | -0.05 |
| min_is | `baseline_is24` (24개월) | 2.53% | 0.707 | OPT_OVERFIT | +0.08 |
| min_is | `baseline_is60` (60개월) | 2.89% | 0.782 | OPT_OVERFIT | +0.15 |
| min_is | **`combo_18_0.1_is24`** | **1.60%** | **0.801** | **OK** | **+0.07** |
| min_is | `combo_18_0.1_is60` | 1.85% | 0.946 | FILTER_OVERFIT | +0.22 (verdict 위배) |

**해석:**

### A. Ranking method
- **t-stat 이 베스트**. shrunk_tstat 와 cagr 모두 baseline/combo 둘 다에서 Sharpe 악화.
- combo_18_0.1_cagr 는 FILTER_OVERFIT — CAGR ranking 은 noise 많아 dedup 시 신호 무력화.

### B. factor_rebal_months
- 3개월 (자주 학습): combo 에선 FILTER_OVERFIT (noise 과적합).
- 12개월 (드물게 학습): baseline +0.04 Sharpe (rules 더 stable). combo 는 OK 유지하지만 Sharpe 약간 하락.
- **6개월 (현재 기본) 이 무난.** 12개월도 운영적으로 검토 가치 있음.

### C. min_is_months — **착시였음 (정정)**

원본 raw Sharpe 비교 (각자 다른 OOS 시작점):
- is=24: Sharpe 0.801 (n=172, 2011-12~2026-03)
- is=36: Sharpe 0.728 (n=160, 2012-12~2026-03)
- is=60: Sharpe 0.946 (n=136, 2014-12~2026-03) — but FILTER_OVERFIT

**공정 정렬 OOS (2014-12 ~ 2026-03, n=136 공통)** 에서 재계산:
| 케이스 | CAGR | Sharpe | MDD |
|---|---|---|---|
| is=24 | 1.85% | **0.946** | -2.86% |
| is=36 | 1.85% | **0.946** | -2.86% |
| is=60 | 1.85% | **0.946** | -2.86% |

**셋 다 비트 단위로 동일!**

원리: IS 는 expanding window 라서 2014-12 시점에는 모두 IS=2009-12~2014-11 (60개월) 로 동일 → 모델 결정도 동일 → OOS 결과도 동일. min_is 는 "OOS 시작점" 만 결정할 뿐 모델에 영향 없음.

Sharpe 차이의 출처는 **추가 OOS 구간의 raw 성과**:
- is=24 의 추가 12개월 (2011-12 ~ 2012-11): Sharpe 2.151, 누적 수익 +3.30% (강세)
- 이 강세 구간 포함 여부가 raw Sharpe 차이를 만듦
- is=60 raw Sharpe 가 0.946 인 것은 우연히 그 시점 이후만 평가했기 때문

**핵심 통찰 정정:** min_is_months 는 모델 자체에 영향 없음. 단지 "백테스트 평가 시작점" 만 결정. is=36 (현재 default) 유지가 합리적 — 너무 짧으면 첫 OOS 의 IS 가 24개월 미만이 되어 모델 신뢰도 낮은 구간 포함, 너무 길면 평가 sample 부족.

## 4. 추천 조합

- 선정 규칙: `verdict==OK` 중 Sharpe 상위 3개, 그 중 `avg_turnover` 최저
- **자동 추천: `combo_18_0.1`** (CAGR 1.47%, Sharpe 0.728, Turnover 0.057)
  - turnover 0.001 차이로 combo_18_0.1_is24 보다 우선 선택됨

### 4-1. 수동 추천 (Sharpe 우선) — min_is 착시 정정 후

`combo_18_0.1_is24` 의 +10% Sharpe 는 IS 길이 효과가 아닌 **OOS 시작점 차이로 인한 평가 sample bias** (§3-2.C 참조). 공정 정렬 후 동일 결과 → 모델 우위 없음.

| 우선순위 | 추천 | CAGR | Sharpe | MDD | Turnover | 근거 |
|---|---|---|---|---|---|---|
| **Sharpe 최대 (verdict OK)** | **`combo_18_0.1`** | 1.47% | **0.728** | -2.86% | 0.057 | cluster + 강한 smoothing (default IS=36) |
| 차순위 | `combo_18_0.2` | 1.46% | 0.725 | -2.86% | 0.060 | α 조금 약 |
| CAGR 최대 (verdict OK) | `baseline_nocap_0.3` | 2.58% | 0.679 | -8.80% | 0.029 | cap 해제 단독, 규제 허용 시 |

**최종 권장: `combo_18_0.1`** — IS 36 default 유지가 표준 비교 가능 + 운영 안정성.

## 5. 실행 메타

- 워커 수: 2
- 총 소요 시간 (순차 합): 153967.1s

| 케이스 | 상태 | Runtime (s) |
|---|---|---|
| `baseline` | OK | 2232.9 |
| `cluster_18` | OK | 2236.1 |
| `cluster_12` | OK | 2247.1 |
| `cluster_24` | OK | 2257.3 |
| `smooth_0.7` | OK | 2848.4 |
| `smooth_0.5` | OK | 2877.2 |
| `combo_18_0.7` | OK | 3680.2 |
| `combo_18_0.5` | OK | 3710.6 |
| `baseline_nocap` | OK | 3801.4 |
| `cluster_nocap` | OK | 3830.4 |
| `combo_nocap_0.5` | OK | 3622.1 |
| `cluster_8` | OK | 3588.3 |
| `cluster_15` | OK | 3604.1 |
| `cluster_20` | OK | 3638.1 |
| `cluster_30` | OK | 3642.9 |
| `cluster_40` | OK | 3685.0 |
| `cluster_18_keep1` | OK | 3686.2 |
| `cluster_18_keep2` | OK | 3705.1 |
| `cluster_18_keep5` | OK | 3699.4 |
| `combo_18_cap0.5` | OK | 3721.0 |
| `combo_18_0.3` | OK | 3713.8 |
| `combo_18_0.2` | OK | 3625.1 |
| `combo_18_0.1` | OK | 3603.7 |
| `baseline_nocap_0.5` | OK | 3639.7 |
| `baseline_nocap_0.3` | OK | 3655.1 |
| `combo_nocap_0.3` | OK | 3655.6 |
| `combo_nocap_0.1` | OK | 3663.6 |
| `cluster_10` | OK | 3569.0 |
| `cluster_n50_keep1` | OK | 3589.1 |
| `cluster_n30_keep1_top30` | OK | 3599.1 |
| `cluster_n18_keep1_top18` | OK | 3529.3 |
| `baseline_shrunk` | OK | 3617.6 |
| `baseline_cagr` | OK | 3610.4 |
| `combo_18_0.1_shrunk` | OK | 3660.5 |
| `combo_18_0.1_cagr` | OK | 3668.0 |
| `baseline_rebal3` | OK | 7391.9 |
| `baseline_rebal12` | OK | 1897.7 |
| `combo_18_0.1_rebal3` | OK | 7423.5 |
| `combo_18_0.1_rebal12` | OK | 1934.1 |
| `baseline_is24` | OK | 3947.0 |
| `baseline_is60` | OK | 3297.6 |
| `combo_18_0.1_is24` | OK | 4017.2 |
| `combo_18_0.1_is60` | OK | 3344.7 |
