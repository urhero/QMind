# Hierarchical Clustering × Turnover Smoothing 백테스트 실험 설계

- **작성일:** 2026-04-24
- **작성자:** inkyu moon (+ Claude)
- **대상 브랜치:** `claude/funny-bell-1d41f7`
- **상위 목표:** 이미 구현되어 있으나 기본 off인 `use_cluster_dedup` 과 `turnover_smoothing_alpha` 의 조합 효과를 Walk-Forward 백테스트로 검증한다.

> **상태 (2026-11-25): 실행 완료된 과거 설계 — 그대로 재실행 불가.** 결과는 `docs/experiments/cluster_turnover_20260425.md`. 본문(§1~§8)은 2026-04-24 당시 기록으로 고치지 않고 둔다. 이후 `turnover_smoothing_alpha` 는 제거됐고 (`deploy_step` + `selection_hysteresis` 로 대체), 선정 코드는 `service/factor/selection.py` 로 옮겨졌다. 설계 결함과 결과 해석 시 주의점, 비슷한 실험을 다시 짤 때의 점검 항목은 **§9 사후 외부 리뷰 검토** 참조.

---

## 1. 배경과 목표

### 1.1 배경

- `service/backtest/factor_selection.py:cluster_and_dedup_top_n()` — Hierarchical Clustering 기반 Top-N 중복 제거 (Sprint 1-B). 현재 `PIPELINE_PARAMS["use_cluster_dedup"] = False` 로 off.
- `WalkForwardEngine.turnover_smoothing_alpha` — EMA 가중치 블렌딩. 현재 기본 `1.0` (스무딩 없음). CLI `--turnover-alpha` 로 조정 가능.
- 두 기능은 독립적으로 테스트된 바 없고, 조합 효과도 확인되지 않았다.

### 1.2 목표 (우선순위 순)

1. **성과 개선 확인 (주)** — `use_cluster_dedup=True` 가 OOS CAGR/Sharpe 를 개선하는지 검증.
2. **Turnover 감소 효과 (부)** — `turnover_smoothing_alpha < 1.0` 이 실제 거래비용을 줄이면서 성과 손실이 허용 범위인지 측정.
3. **과적합 경감 여부** — Clustering 이 `research.md §6.4` 의 과적합 판정(Funnel Value-Add, OOS Percentile) 에 유의미한 개선을 주는지 확인.
4. **운영 후보 조합 도출** — 8 케이스 중 성과·turnover·과적합 지표를 종합해 추천 1~2 케이스 제시.

### 1.3 비목표

- Cluster 파라미터 (`n_clusters`, `per_cluster_keep`) 의 fine-tuning 전수조사.
- 새로운 clustering 알고리즘 도입.
- 운영 배포 결정 (실험 리포트 이후 별도 논의).

---

## 2. 실험 그리드 (8 케이스)

`per_cluster_keep=3` 고정, 한 축씩 변화시켜 효과 해석을 명확하게 분리한다.

| # | 케이스 이름 | `use_cluster_dedup` | `n_clusters` | `per_cluster_keep` | `turnover_alpha` | 설계 의도 |
|---|---|---|---|---|---|---|
| 1 | `baseline` | False | – | – | 1.0 | 현재 프로덕션 기본 (비교 기준) |
| 2 | `cluster_18` | True | 18 | 3 | 1.0 | clustering 단독 (표준) |
| 3 | `cluster_12` | True | 12 | 3 | 1.0 | n_clusters 민감도 (대분류) |
| 4 | `cluster_24` | True | 24 | 3 | 1.0 | n_clusters 민감도 (세분) |
| 5 | `smooth_0.7` | False | – | – | 0.7 | smoothing 단독 (약) |
| 6 | `smooth_0.5` | False | – | – | 0.5 | smoothing 단독 (강) |
| 7 | `combo_18_0.7` | True | 18 | 3 | 0.7 | 둘 다 (표준) |
| 8 | `combo_18_0.5` | True | 18 | 3 | 0.5 | 둘 다 (강) |

### 비교 축

- **Clustering 효과:** #1 vs #2
- **n_clusters 민감도:** #2 vs #3 vs #4 (단조성 체크)
- **Smoothing 효과:** #1 vs #5 vs #6 (α 감소에 따른 trade-off)
- **조합 효과:** #2 vs #7 vs #8 (clustering 위에 smoothing 얹을 때 marginal)
- **최종 후보:** #7, #8 (성과 유지 + turnover 감소 동시 달성 여부)

### 공통 파라미터 (전 케이스 고정)

- Backtest 기간: `2009-12-31 ~ 2026-03-31`
- `min_is_months=36`, `factor_rebal_months=6`, `weight_rebal_months=3`
- `top_factors=50`, `factor_ranking_method="tstat"`

---

## 3. 러너 아키텍처

### 3.1 최소 침습 변경 (프로덕션 경로 무영향)

`service/backtest/walk_forward_engine.py`:

- `WalkForwardEngine.__init__` 시그니처에 `pipeline_params_override: dict | None = None` 인자 1개 추가.
- `run()` 초반 `pp = dict(PIPELINE_PARAMS)` 직후에 `pp.update(self.pipeline_params_override or {})` 한 줄 추가.

CLI 경로 (`main.py backtest ...`) 는 `pipeline_params_override=None` 으로 주입되므로 동작 완전 동일.

### 3.2 러너 스크립트

- 파일: `scripts/run_cluster_turnover_experiment.py`
- 예상 길이: 약 150~200 라인
- 책임:
  1. 그리드 선언 (CASES 리스트)
  2. `ProcessPoolExecutor` 로 케이스 병렬 실행
  3. 케이스별 결과 CSV/JSON 저장
  4. 집계 `summary.csv` + `REPORT.md` 자동 생성
- **실행하지 않는 것:** 데이터 다운로드, 프로덕션 배포, 설정 파일 영구 수정.

### 3.3 주요 동작 흐름 (의사코드)

```python
CASES = [
    {"name": "baseline", "override": {}, "alpha": 1.0},
    {"name": "cluster_18", "override": {
        "use_cluster_dedup": True, "n_clusters": 18, "per_cluster_keep": 3,
    }, "alpha": 1.0},
    # ... 총 8개
]

OUT_ROOT = Path(f"output/experiments/cluster_turnover_{timestamp}")

def run_single_case(case: dict, out_root: Path, common: dict) -> dict:
    # 워커 프로세스 내부
    case_dir = out_root / case["name"]
    case_dir.mkdir(parents=True, exist_ok=True)
    # stdout/stderr 를 run.log 로 리디렉트
    # logging.getLogger() 레벨 WARNING 이상 (rich progress 억제)

    engine = WalkForwardEngine(
        min_is_months=common["min_is_months"],
        factor_rebal_months=common["factor_rebal_months"],
        weight_rebal_months=common["weight_rebal_months"],
        top_factors=common["top_factors"],
        turnover_smoothing_alpha=case["alpha"],
        pipeline_params_override=case["override"],
    )
    result = engine.run(common["start"], common["end"])

    # 결과 파일 저장
    result.to_csv(case_dir / "walk_forward.csv")
    save_overfit_diagnostics(result, case_dir / "overfit_diagnostics.csv")
    save_performance_json(result, case_dir / "performance.json")

    return build_summary_row(case, result)

if __name__ == "__main__":
    args = parse_args()  # --workers (default 4), --sequential
    write_config_json(OUT_ROOT, CASES, args)

    if args.sequential or args.workers == 1:
        rows = [run_single_case(c, OUT_ROOT, COMMON) for c in CASES]
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(run_single_case, c, OUT_ROOT, COMMON): c for c in CASES}
            rows = []
            for f in as_completed(futures):
                try:
                    rows.append(f.result())
                except Exception as e:
                    rows.append(build_failed_row(futures[f], e))

    summary_df = pd.DataFrame(rows)
    summary_df.to_csv(OUT_ROOT / "summary.csv", index=False)
    render_markdown_report(summary_df, OUT_ROOT / "REPORT.md")
```

### 3.4 병렬화 세부사항

- **모델:** `ProcessPoolExecutor` (GIL 회피, 케이스 독립).
- **워커 기본값:** 4. CLI `--workers N` 로 변경, `--sequential` 로 순차 fallback.
- **메모리:** 각 워커가 parquet 전체 독립 로드 → RAM 사용량 ≈ `N × 단일 케이스 RAM`. 첫 실행 후 단일 케이스 RAM 측정하여 `--workers` 조정.
- **Windows 호환:** `if __name__ == "__main__":` 가드 필수. spawn 방식.
- **로그 격리:** 워커의 stdout/stderr 는 `case_dir/run.log` 로 리디렉트. rich `track()` 진행바는 워커에서 logger level WARNING↑로 억제. 메인 프로세스는 "case X/8 완료" 한 줄씩만 출력.
- **예외 처리:** `future.exception()` 포집. 실패 케이스는 `summary.csv` 에 `status=FAILED` + 에러 메시지 기록, 나머지 케이스는 계속 진행.

### 3.5 트레이드오프

- 케이스 2/3/4/7/8 은 `cluster_and_dedup_top_n()` 호출로 baseline 대비 각 Tier 2 리밸런싱마다 소폭 느림 (예상 +5~15%).
- 병렬화로 wall-clock 은 단축되지만 총 CPU 시간은 동일하거나 조금 증가.

---

## 4. 산출물 구조

```
output/experiments/cluster_turnover_<YYYYMMDD_HHMMSS>/
├── REPORT.md                    # 사람이 읽는 요약 리포트 (한글)
├── summary.csv                  # 케이스별 성과 요약 한 줄씩 (머신 친화)
├── config.json                  # 그리드 + 공통 파라미터 + git SHA + 실행 시각
├── baseline/
│   ├── walk_forward.csv         # OOS 월별 수익률 (CEW/EW/EW_All/EW_Top50 + 누적)
│   ├── overfit_diagnostics.csv  # 과적합 진단 5지표 + verdict
│   ├── performance.json         # CAGR, Sharpe, MDD, Calmar, Avg Turnover, Net-CAGR
│   └── run.log                  # 워커 stdout/stderr
├── cluster_18/                  # ... (동일 구조)
├── cluster_12/
├── cluster_24/
├── smooth_0.7/
├── smooth_0.5/
├── combo_18_0.7/
└── combo_18_0.5/
```

### 4.1 `summary.csv` 컬럼

| 컬럼 | 의미 |
|---|---|
| `case` | 케이스 이름 |
| `use_cluster_dedup`, `n_clusters`, `per_cluster_keep`, `turnover_alpha` | 그리드 값 |
| `status` | `OK` / `FAILED` |
| `cagr_cew`, `sharpe_cew`, `mdd_cew`, `calmar_cew` | OOS CEW 성과 (net-of-cost) |
| `cagr_ew`, `sharpe_ew` | OOS 1/N 벤치마크 |
| `avg_turnover`, `net_cagr_cew` | 거래비용 차감 전후 |
| `funnel_verdict` | `OK (C>B>A)` / `OPT_OVERFIT (B>C>A)` / `FILTER_OVERFIT (A>B)` |
| `funnel_a_cagr`, `funnel_b_cagr`, `funnel_c_cagr` | Funnel Value-Add 서브 값 |
| `oos_pctile_value`, `oos_pctile_flag` | 수치 + `OK`/`WARN` (≥60% → WARN) |
| `strict_jaccard`, `is_oos_rank_corr`, `deflation_ratio` | 나머지 3지표 raw |
| `verdict` | 종합 판정: `OK` / `OPTIMIZATION_OVERFIT` / `FILTER_OVERFIT` / `PERCENTILE_WARN` |
| `runtime_sec` | 실행 시간 (병렬 튜닝용) |

### 4.2 `performance.json` (케이스별)

`summary.csv` 한 행과 동일한 필드. 단일 케이스만 보고 싶을 때 참조용.

### 4.3 `config.json`

```json
{
  "run_id": "cluster_turnover_20260424_141523",
  "git_sha": "ac80cfe",
  "backtest_start": "2009-12-31",
  "backtest_end": "2026-03-31",
  "min_is_months": 36,
  "factor_rebal_months": 6,
  "weight_rebal_months": 3,
  "top_factors": 50,
  "factor_ranking_method": "tstat",
  "workers": 4,
  "cases": [{"name": "baseline", "override": {}, "alpha": 1.0}, ...]
}
```

**재현성 포인트:** `config.json` + 원본 parquet + 러너 스크립트 → 실험 완전 재현 가능.

---

## 5. `REPORT.md` 포맷

```markdown
# Cluster Dedup × Turnover Smoothing 실험 리포트
- 실행일: YYYY-MM-DD
- Git SHA: <sha>
- 백테스트 기간: 2009-12-31 ~ 2026-03-31
- 공통 파라미터: min_is=36, factor_rebal=6, weight_rebal=3, top=50, ranking=tstat

## 1. 성과 요약 (OOS, Net-of-cost)
| 케이스 | CAGR | Sharpe | MDD | Calmar | Avg Turnover | vs Baseline ΔCAGR | ΔTurnover |
|---|---|---|---|---|---|---|---|
| (8행) |

## 2. 과적합 진단
| 케이스 | Verdict | Funnel (A/B/C CAGR) | OOS Pctile ↓ | Jaccard ↑ | Rank Corr ↑ | Deflation |
|---|---|---|---|---|---|---|
| (8행) |

> *Deflation Ratio = OOS CAGR / IS CAGR. OOS 기간이 짧으면 단독 판단 금지.*

## 3. 해석
### 3.1 Clustering 효과 (cases 1 vs 2)
- (자동) CAGR/Sharpe 변화 방향, 과적합 지표 verdict 개수 변화
### 3.2 n_clusters 민감도 (2 vs 3 vs 4)
- (자동) 12/18/24 수치 비교, 단조성 체크
### 3.3 Turnover Smoothing 단독 효과 (1 vs 5 vs 6)
- (자동) α 감소에 따른 CAGR/Turnover trade-off
### 3.4 조합 효과 (2 vs 7 vs 8)
- (자동) marginal ΔCAGR / ΔTurnover

## 4. 추천 조합
- 규칙: `verdict=="OK"` 인 케이스 중 `sharpe_cew` 상위 3개, 그 중 `avg_turnover` 가장 낮은 것
- 최종 추천: `<case_name>` — 근거 수치 2~3개

## 5. 실행 메타
- 워커 수 / 총 소요 / 케이스별 런타임 / 실패 케이스
```

### 5.1 자동 해석의 한계

- §3.1~3.4 의 **방향성·수치**는 자동 채우되, **도메인 해석 문장**(예: "clustering이 diversification을 개선") 은 사람이 덧붙여야 함.
- §4 추천 규칙은 단순 규칙 기반. Pareto front 분석은 이번 스펙 범위 밖.

### 5.2 과적합 진단 verdict 분류 로직

- Funnel Value-Add `A/B/C CAGR` 값에서:
  - `C > B > A` → `funnel_verdict = "OK (C>B>A)"`
  - `B > C > A` → `funnel_verdict = "OPT_OVERFIT (B>C>A)"`
  - `A > B` → `funnel_verdict = "FILTER_OVERFIT (A>B)"`
  - 그 외 패턴 → `"UNCATEGORIZED"` (드물게 발생)
- OOS Percentile: `>= 60%` → `oos_pctile_flag = "WARN"`, else `"OK"`.
- 종합 `verdict`:
  - `funnel_verdict` 가 `OPT_OVERFIT` → `OPTIMIZATION_OVERFIT`
  - `funnel_verdict` 가 `FILTER_OVERFIT` → `FILTER_OVERFIT`
  - `funnel_verdict=OK` 이고 `oos_pctile_flag=OK` → `OK`
  - `funnel_verdict=OK` 이고 `oos_pctile_flag=WARN` → `PERCENTILE_WARN`

---

## 6. 검증 & 마무리

### 6.1 코드 변경 검증 (CLAUDE.md 프로세스)

**A. pytest**

- `tests/test_unit/test_walk_forward_engine_override.py` 신규 추가:
  - `pipeline_params_override=None` 일 때 기존 동작 유지 확인 (baseline 소량 실행)
  - `override={"use_cluster_dedup": True, "n_clusters": 10, "per_cluster_keep": 2}` 주입 시 `pp` 에 반영되는지 (engine 내부 `pp` 를 노출하거나 cluster_and_dedup 호출이 일어나는지 검증)
- `python -m pytest tests/test_unit/ -v` 전체 통과

**B. 회귀 검증 (CLAUDE.md 필수 프로세스)**

1. 변경 전: `python main.py mp test test_data.csv` 출력 저장
2. 변경 후: 동일 명령 재실행
3. `total_aggregated_weights_*_test.csv`, `meta_data.csv` diff 0 확인 (override=None 경로이므로 완전 동일 필요)
4. 변경 전/후 `python main.py backtest test test_data.csv --min-is-months 4` 도 diff 0 확인

### 6.2 실험 실행 체크리스트

1. `python scripts/run_cluster_turnover_experiment.py --workers 4` 실행
2. 첫 케이스 완료 후 단일 케이스 RAM 로그 확인 → 필요 시 `--workers 2` 재실행
3. `output/experiments/cluster_turnover_<ts>/` 전체 생성 확인
4. `REPORT.md` §3 해석 문단에 도메인 해석 보강 (사람 작업)

### 6.3 문서 업데이트

- `docs/experiments/cluster_turnover_YYYYMMDD.md` — 재현성 있는 최종본 커밋 (`output/` 은 gitignore 대상일 수 있음)
- `README.md` — `use_cluster_dedup` / `turnover_smoothing_alpha` 파라미터 표 옆에 "실험 결과는 `docs/experiments/...` 참조" 한 줄 추가
- `research.md` — 실험 결과가 기존 해석을 바꾸는 경우에만 §6 업데이트

---

## 7. 범위 밖

- 병렬화 실패 시 공유 메모리 (mmap parquet) 최적화
- Cluster 파라미터 전수조사 (n_clusters × keep grid)
- Production 적용 의사결정 (CEO/PM 영역)
- 다른 clustering 알고리즘 (DBSCAN, graph-based 등)
- 여러 `factor_ranking_method` 와의 교차 그리드

---

## 8. 리스크 & 완화

| 리스크 | 영향 | 완화 |
|---|---|---|
| 8 케이스 전 실행이 너무 오래 걸림 | 실험 피드백 루프 지연 | 병렬화 (--workers 4) + 첫 실행 RAM 측정 |
| 워커 RAM 폭증 | OOM 실패 | `--workers` 조정, 최악의 경우 `--sequential` |
| `pipeline_params_override` 도입이 기존 경로에 영향 | 프로덕션 회귀 | override=None 경로 회귀 테스트 + diff-0 검증 의무화 |
| 결과가 noisy하여 비교 어려움 | 의사결정 불가 | 기간 분할 (pre/post 2017) 서브 분석을 필요 시 수행 — 다음 반복에서 고려 |
| `cluster_and_dedup_top_n` 이 특정 IS 슬라이스에서 실패 | 일부 케이스 FAILED | 이미 있는 fallback (rank_score sort) 활용 + 로그 확인 |

---

## 9. 사후 외부 리뷰 검토 (2026-11-25)

외부 리뷰(Gemini) 지적을 당시 코드(`d2799c5`), 현행 코드, 실험 결과 파일로 하나씩 판정했다. 수용 = 지적이 맞고 아래 §9.1~§9.3 에 반영, 일부 = 방향은 맞으나 수치·근거를 고쳐 반영, 제외 = 근거가 틀렸거나 당시 설계의 결함이 아님.

| # | 지적 | 판정 | 근거 (확인한 것) |
|---|---|---|---|
| 1 | `per_cluster_keep=3` 고정이 코드에서 조용히 깨짐 | 수용 | `cluster_and_dedup_top_n` 의 가드(`n_clusters × keep < top_n` 이면 keep 을 올림)는 설계 당시에도 있었다. top_n=50 이면 n ≤ 16 인 케이스 전부 해당. 결과 파일이 증거: `cluster_18_keep1`·`keep2` 가 `cluster_18`(keep3) 과 모든 수치가 같다 |
| 2 | 케이스마다 실제 선정 수가 다름 (baseline 50 vs cluster_18 평균 41.1) | 수용 | `cluster_size_diagnostics.md`: 평균 41.15, 33~49. 가장 큰 클러스터가 평균 53% (최대 79%) |
| 3 | 팩터 비중 회전율은 종목단 매매비용과 1:1 이 아님 (교차 상쇄) | 수용 | `mp_level_cost_20260703.md`: 실제 비용 / 팩터별 전액 계상 = 0.530~0.574 |
| 4 | EMA 가 기록상 회전율만 낮추고 실제 배포 비중 회전율은 못 줄임 | 수용 | `2026-06-05-absolute-step-smoothing-design.md` §1: 메모리 비중 2.63→2.66 인데 배포 비중 2.63→3.48. 회전율 지표가 메모리 기준이었다 |
| 5 | `cagr_cew` "(net-of-cost)" 와 `net_cagr_cew` "차감 전후" 표기 충돌 | 수용 | 엔진 수익률은 팩터 안 종목 교체 비용만 뺀 값이고, 팩터 간 비중 교체 비용은 러너가 따로 뺐다. 두 컬럼이 각각 무엇을 뺐는지 설계에 없다 |
| 6 | `B > C > A` 를 과적합(OPT_OVERFIT)으로 분류 | 수용 | 현행 `calc_funnel_value_add` 는 같은 패턴을 `CONSTRAINT_DRAG`(제약 비용, 과적합 아님)로 부른다. C 단계에 학습되는 가중이 없다. 이 분류 때문에 baseline·cluster_12 가 추천에서 빠졌다 |
| 7 | verdict 분기 불완전 (`UNCATEGORIZED` 가 종합 verdict 표에 없음, B==C 처리 없음) | 수용 | §5.2 그대로. 결과에서 소수 둘째 자리까지 같은 값이 서로 다른 판정을 받았다: `baseline_nocap` 2.55/2.55 → OK, `cluster_15` 1.68/1.68 → OPT_OVERFIT, `cluster_40` 1.11/1.11 → FILTER_OVERFIT |
| 8 | OOS 백분위 임계 `>= 60%` vs 코드 `> 0.60` | 일부 | 정확히 0.60 인 경우만 다르다. 실험 값은 전부 47~52% 라 결과에 영향 없음. 현행 코드도 docstring(`>= 0.60`)과 분기(`<= 0.60` 은 보통)가 서로 다르다 |
| 9 | alpha {0.7, 0.5} 가 너무 약함 | 일부 | 결론은 맞다 (baseline 회전율 0.045 → 0.040 → 0.037, 이후 0.3·0.2·0.1 을 추가로 돌림). 리뷰의 "반감기 0.61 분기" 는 틀림: 직전 비중이 남는 비율 = 1−α 이므로 α=0.7 은 0.58 회차(1.7개월), α=0.5 는 1 회차(3개월) |
| 10 | cluster_12·24 × alpha 교차 케이스 없음 | 일부 | 한 축씩 바꾸는 것은 의도한 설계다 (1 케이스 40~60분, §1.3 이 전수조사를 비목표로 둠). 다만 "교차 효과는 이 그리드로 알 수 없다" 는 한계가 적혀 있지 않았다 |
| 11 | 전 구간 수치 하나로 비교, 기간 분해를 뒤로 미룸 | 수용 | `period_sharpe_analysis.md`: cluster_18 은 ~2017 0.80 vs baseline 1.01, 2018~22 0.38 vs 0.61 로 열위, 2023~ 0.95 vs 0.27 로 전체가 뒤집힘 |
| 12 | 추천 규칙에 대체 경로·하한 없음 | 수용 | 실제 자동 추천이 "회전율 0.001 차이" 로 정해졌다 (결과 §4). OK 가 3개 미만일 때 처리 없음, CAGR -0.92%p 를 걸러내는 조건 없음 |
| 13 | 다중 비교 통제 없음 | 수용 | 8 케이스로 시작해 43 케이스까지 늘렸고 그중 최고 Sharpe 를 골랐다. 부트스트랩까지는 과하고, 기간 분해 + 이웃 파라미터 값이 고르게 좋은지(고원) 확인으로 충분 (이후 실험의 관행) |
| 14 | `config.json` 이 재현에 부족 | 수용 | `style_cap`, `optimization_mode`, 비용 등 override 하지 않은 `PIPELINE_PARAMS` 가 빠져 있다. `BENCHMARK`·`is_window_months`·`selection_hysteresis` 는 당시 없던 항목이라 현행화 사항 |
| 15 | 병렬 워커가 `OUTPUT_DIR` 의 같은 파일에 동시에 씀 | 제외 (당시 결함 아님) / 현행 문제로 수용 | `factor_returns_matrix`·`stock_level_series` 저장은 2026-07-29·08-20 에 추가됐다. 현행 코드에서는 사실이고 범위가 더 넓다 — §9.3 |
| 16 | 엔진에 `selection_hysteresis`·`is_window_months` 를 넘기지 않음 | 제외 (당시 결함 아님) / 현행화 | 당시 없던 인자. 현행은 사실: 엔진 기본값이 0.0·None 이고 config 를 따르는 것은 `main.py:_run_backtest` 뿐 |
| 17 | `save_overfit_diagnostics` 인자 누락 | 제외 | 의사코드의 가상 래퍼다. `result.is_full_period_cagr` 는 `result` 에서 꺼낼 수 있다 |
| 18 | logger 레벨로는 rich `track()` 이 안 꺼짐 | 수용 | `track()` 은 logging 을 거치지 않는다 (엔진 L413). stdout 을 파일로 돌리면 진행바가 `run.log` 에 쌓일 뿐이라 실행에는 지장 없었다 (43 케이스 전부 OK). cp949 충돌 주장은 확인된 바 없음 |
| 19 | 체크포인트·재개 없음 | 수용 | 총 153,967초 (약 43시간) 규모. 현행 `research/param_recheck_runner.py` 도 완료 케이스를 건너뛰지 않는다 |
| 20 | §6.1.B 의 `test_data.csv`·`total_aggregated_weights_*`·`meta_data.csv` 가 틀림 | 제외 (당시 결함 아님) / 현행화 | 당시에는 맞는 경로·파일명. 현행은 `tests/fixtures/test_data.csv`, `output/{BENCHMARK}/test/weights_*` |
| 21 | 실험 스크립트는 `research/` | 현행화 | 커밋 `4724c6c` 에서 `scripts/` → `research/`. 러너 스크립트는 이후 삭제됨 |
| 22 | 최종 후보는 `research/mp_level_cost_backtest.py` 로 검증 | 일부 | 종목단 실측이 필요하다는 것은 맞다. 다만 그 스크립트는 엔진과 어긋나 있다 (2026-11-11 점검: Top50 고정, horizon 없음). 실제 기준은 엔진이 내는 `stock_level_series` |
| 23 | EW_Top50(B 단계)이 2026-07-05 이후 dedup 이전 Top-N | 현행화 | `research.md` §6.4, `walk_forward_engine.py` `rank_topn`. 당시 결과의 B 값은 dedup 이후 선정분이라 현행 판정과 직접 비교할 수 없다 |

### 9.1 실험 결과를 읽을 때 주의할 것 (`cluster_turnover_20260425.md`)

1. **n_clusters 민감도는 keep 과 섞여 있다.** 실제 keep: `cluster_8` = 7, `cluster_10`·`cluster_12` = 5, `cluster_15` = 4, n ≥ 18 = 3. keep 만 바꾼 `cluster_18_keep5` 가 Sharpe 0.814 (keep3 은 0.693) 이므로, `cluster_12` 의 0.866 중 어디까지가 클러스터 수 효과인지 이 그리드로는 가를 수 없다.
2. **`cluster_18_keep1`·`keep2` 는 keep3 과 같은 실행이다.** keep 을 줄인 효과로 읽으면 안 된다.
3. **dedup on/off 비교에는 선정 수 차이(50 vs 평균 41)가 섞여 있다.**
4. **`OPTIMIZATION_OVERFIT` 판정은 과적합 증거가 아니다.** 현행 기준으로는 제약 비용(`CONSTRAINT_DRAG`)이다. 따라서 "verdict OK 만 남긴다" 는 추천 규칙으로 baseline·cluster_12 가 빠진 것은 근거가 없고, 최종 권장 `combo_18_0.1` 도 이 규칙의 산물이다.
5. **스무딩 케이스의 회전율·Net CAGR 은 메모리 비중 기준이다.** 실제 배포 비중 회전율은 줄지 않았다 (#4). `combo_*` 의 회전율 개선 수치는 실거래 비용 절감으로 읽으면 안 된다.
6. **cluster_18 계열의 우위는 2023 년 이후 구간에서만 나온다** (#11).
7. 이후 채택된 구성은 이 실험의 추천이 아니다: MXCN1A 는 `winner_median` (2026-06-30), MXWO 는 dedup off (2026-07-28), 스무딩은 폐기.

### 9.2 비슷한 그리드 실험을 다시 짤 때

- 선정 규칙을 건드리는 케이스는 **실제 선정 수(평균·범위)와 실제 적용된 keep** 을 요약에 같이 적는다. `n_clusters × per_cluster_keep < top_n` 인 조합은 넣지 않는다.
- 회전율·비용은 **배포 비중 기준**으로 재고, 후보는 엔진의 `stock_level_series`(종목단, 상쇄 반영)로 확인한다.
- 판정은 현행 `overfit_diagnostics` 라벨(NORMAL / CONSTRAINT_DRAG / FILTER_OVERFIT)을 그대로 쓰고, 라벨 하나로 케이스를 떨어뜨리지 않는다. 기간 분해(전반·후반·최근 3년)와 이웃 값의 고원 여부를 같이 본다.
- 엔진을 직접 만들 때 `selection_hysteresis`·`is_window_months` 를 `PIPELINE_PARAMS` 에서 꺼내 넘기고 (엔진 기본값은 config 를 따르지 않는다), `save_outputs=False` 를 준다 (§9.3).
- 실행 기록에 `BENCHMARK` 와 `PIPELINE_PARAMS` 전체, 엔진 생성 인자를 남긴다.
- 케이스가 끝날 때마다 결과를 쓰고, 다시 돌릴 때 끝난 케이스는 건너뛴다.
- 병렬 수는 메모리 여유로 정한다 (이 실험도 설계 4 → 실제 2 워커).

### 9.3 현행 코드에서 찾은 문제 (2026-11-25 수정)

`WalkForwardEngine.run()` 은 `test_file` 이 없으면 항상 `output/{BENCHMARK}/<기준일>/factor_returns_matrix_<기준일>.csv` 와 `stock_level_series_<기준일>.csv` 를 쓴다. 따라서 엔진을 직접 부르는 실험 러너(`research/param_recheck_runner.py` 등)는

- 병렬 실행 시 같은 파일에 동시에 쓰고 (실패는 `try/except` 로 경고만 남김),
- 순차 실행이어도 **실험 변형의 결과로 정식 백테스트 산출물을 덮어쓴다.** 대시보드가 읽는 파일이므로, 실험 뒤에 정식 백테스트를 다시 돌리지 않으면 변형 케이스의 값이 남는다.

수정: `WalkForwardEngine(save_outputs=False)` 를 넘기면 두 파일을 쓰지 않는다 (기본 True 라 CLI `backtest` 는 그대로). `research/param_recheck_runner.py` 에 적용했다. 엔진의 `run()` 을 직접 부르는 러너를 새로 만들 때는 이 인자를 넘길 것. 이미 덮어쓰인 파일은 정식 `backtest` 를 다시 돌려야 복구된다.
