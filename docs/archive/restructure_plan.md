# BOK 파이프라인 재구조화 단일 실행계획

> **목적:** 유지보수성/모듈성 향상 (단계별 책임 분리, 의존성 단방향화, 테스트 용이성).
> **철학:** 4개 설계안 중 `minimal-risk-pragmatic`을 운영 골격으로 채택하고, `layered-ddd`의 도메인 승격(factor_selection), `contract-first`의 계약 중앙화(serialization 상수), `functional-core`의 결정성 상수 고정(determinism 체크리스트)을 **검증된 best_idea만** 접목한다.
> **불변 원칙(글자보존 이동):** 함수 본문은 1바이트도 바꾸지 않는다. 위치/import만 변경한다. 모든 "통합/공식 일원화/리네이밍 충동"은 배제한다.

## 0. 절대 불변 제약 (HARD)
1. Python 유지. SQL Server 소스 유지(쿼리 텍스트/ORDER BY/ROW_NUMBER 불변).
2. Bloomberg Optimizer 입력 CSV 계약(파일명/컬럼/포맷/encoding/index 정책) 100% 유지.
3. 산출 byte-identity: `total_aggregated_weights_*`, `total_aggregated_weights_style_*`, `pivoted_total_agg_wgt_*`, `meta_data.csv`, `walk_forward_results.csv`.
   - 주의: `aggregated_weights_*`(total 접두 없음)는 현재 `model_portfolio.py`가 **생성하지 않는 dead 계약**(output/의 잔재). 검증 대상에서 제외하되 본 문서에 명시.

## 1. 결정성 불변식 체크리스트 (이동 금지 / 변경 금지)
코드 교차확인으로 실재 확인된 7개 핫스팟. 이동 시 키/순서/반올림 자릿수/dtype 절대 불변.

| # | 위치 | 불변식 |
|---|------|--------|
| 1 | `smoothing.py:51` | `union = sorted(set(target)|set(prev))` — held/free/binding 분류 순회 및 float 합산 순서 |
| 2 | `factor_selection.py:208-229` | `apply_selection_hysteresis` exits `key=(-score,f)` / entries `key=(score,f)` / 반환 `sorted(out,key=(-score,f))` |
| 3 | `walk_forward_engine.py:542` | `available_factors = sorted(...)` — OOS 합산(557) 순서 |
| 4 | `model_portfolio.py:473/481` | `sort_values(['factor','ticker','gvkeyiid'])` / `(['style','factor','ticker','gvkeyiid'])` |
| 5 | `model_portfolio.py:499` | `factor_weight.round(12)` — pivot 헤더 결정 |
| 6 | `optimization.py:48,88` | `np.float32` (가중치 벡터/수익률 행렬) — dtype 정리 충동 차단 |
| 7 | `weight_history.py:154/176` | `_build_factor_style_df` sorted union + `groupby('style')...transform('sum')` + `sort_values(['style','new_weight'])`, `save_style_totals` `groupby(sort=False)` |

**부동소수/정렬/dict순서 보장 전략:** 위 7개는 "이동만 허용". 함수 본문을 복사할 때 라인 단위 diff(글자보존)로만 옮기고, 옮긴 직후 `git diff -w`가 위치 변화 외 0임을 확인한다.

## 2. 목표 모듈 트리 (현재 -> 목표)

```
bok/
├── main.py                       # CLI 라우팅 전용으로 축소 (직렬화 로직 제거)
├── config.py                     # 유지 (PARAM/PIPELINE_PARAMS)
│
├── service/
│   ├── factor/                   # [신규] 공유 도메인 — 레이어 역전 해소
│   │   └── selection.py          # ← service/backtest/factor_selection.py (본문 불변 이동)
│   │
│   ├── download/                 # 골격 유지 (이번 범위에서 SQL/검증 로직 무변경)
│   │   ├── download_factors.py
│   │   ├── parquet_io.py
│   │   └── download_validation.py
│   │
│   ├── pipeline/
│   │   ├── model_portfolio.py    # 오케스트레이터 (축소: _evaluate_universe → universe.py 호출)
│   │   ├── universe.py           # [신규] _evaluate_universe 본문을 함수로 추출(글자보존)
│   │   ├── factor_analysis.py    # 유지
│   │   ├── optimization.py       # 유지
│   │   ├── weight_construction.py# 유지
│   │   ├── weight_history.py     # 유지
│   │   └── smoothing.py          # 유지 (factor/로 옮기지 않음 — 아래 rejected 참조)
│   │
│   ├── backtest/
│   │   ├── walk_forward_engine.py# 유지 (Tier 메서드 추출은 후속, 이번엔 import만 갱신)
│   │   ├── result_stitcher.py    # 유지 (walk_forward_results.csv 계약 단독 소유)
│   │   ├── overfit_diagnostics.py# + serialize_diagnostics_csv() 흡수
│   │   ├── factor_selection.py   # → factor/selection.py 로 이동 후 제거(shim 경유)
│   │   └── data_slicer.py        # 유지
│   │
│   └── report/                   # 유지 (시각화 분리는 후속)
│       ├── dashboard*.py
│       └── report_generator.py
│
├── research/                     # ← scripts/ git mv (배포/연구 분리)
└── tests/                        # import 경로 동시 갱신
```

**이번 범위에서 의도적으로 미루는 것(후속 작업):** download 검증 통합, GenerateQueryStructure DI, walk_forward Tier 메서드 추출, benchmark_comparison 위치 이동, Rich print 분리, dashboard 템플릿 외부화. (scope_creep/위험 대비 이득 낮음)

## 3. 단계별 이행 (위험 낮은 순)

각 단계 공통 게이트: `python main.py mp test test_data.csv` 결과 diff 0 + `python -m pytest tests/test_unit/ -v` 통과. 백테스트 41분 회귀는 **계산경로를 건드리는 단계(4)에만** 1회 지불.

### Phase 0 — 베이스라인 캡처
- `mp test` 출력, `pytest`, (가능하면) 실데이터 `mp` 1회를 캡처해 회귀 기준 고정.

### Phase 1 — factor_selection 도메인 승격 (레이어 역전 해소)
- `service/backtest/factor_selection.py` → `service/factor/selection.py` (본문 1바이트 불변).
- 구경로에 `from service.factor.selection import *` shim 임시 배치.
- **import 6사이트 전수 치환** (grep `backtest.factor_selection`로 확인):
  `model_portfolio.py:380/420/439`(lazy 3곳), `walk_forward_engine.py:24`, `tests/test_unit/test_factor_selection.py:11`, `tests/test_unit/test_determinism.py:33`(_PROBE 문자열 — 일반 import 갱신에서 누락되기 쉬움, 별도 확인).
- 게이트: `mp test` diff 0 + pytest(특히 test_determinism, test_factor_selection).

### Phase 2 — overfit_diagnostics 직렬화 추출 (CLI 표현 책임 제거)
- `main.py:236-276`의 `_pct/_dec` + rows 리스트 + `to_csv(encoding='utf-8-sig', index=False)`를 `overfit_diagnostics.serialize_diagnostics_csv(report, path)`로 **글자보존 이동**.
- `main._run_backtest`는 `serialize_diagnostics_csv(...)` 한 줄 호출로 대체.
- **암묵 결합 동기 검증:** `dashboard_data.build_kpis`가 파싱하는 한국어 Category/Metric 키 목록을 회귀 assertion으로 고정(예: 새 test가 직렬화 출력 → build_kpis 파싱 성공을 함께 검증). 단일 글자 변경도 viz 파손이므로 동일 커밋에서 검증.
- 게이트: `mp test`(영향 없음) + pytest + 새 test_overfit_serializer.

### Phase 3 — scripts → research 이동 (배포/연구 분리)
- `git mv scripts research`. `tests/test_unit/test_cluster_turnover_experiment.py`의 `from scripts.X` → `from research.X` 치환. `experiment_*`, `run_smoothing_*`, `analyze_smoothing_*` 내부 import는 service 경로라 무영향. `sys.path(ROOT)` 유지.
- 근거 정정: 본 저장소는 Pipfile만 존재(setup.py/pyproject 없음). "패키징 제외"가 아니라 "조직적 분리"로 표현.
- 게이트: pytest 전체.

### Phase 4 — _evaluate_universe 본문 추출 (god-method 분해) [계산경로]
- `model_portfolio._evaluate_universe`(L344-460) 본문을 `pipeline/universe.py:evaluate_universe(...)`로 **글자보존 이동**. `model_portfolio`는 이 함수를 호출만.
- **통합 금지:** walk_forward Tier2(L430-490)와 **합치지 않는다**. 두 경로는 (a) monthly_rets 컬럼 순서(production=`meta["factorAbbreviation"].tolist()` 재인덱스 vs backtest=`ret_df_is.iloc[1:]` raw 순서), (b) incumbency 소스(production=history `load_prev_selection` vs backtest=`set(cached_selected_factors)`), (c) cagr 산식 범위, (d) meta_data.csv 저장 유무가 다르다. clustering의 corr/linkage가 컬럼 순서 의존이므로 통합 시 선정 집합이 바뀌어 byte-diff. **각 호출부는 그대로 두고, 공유는 이미 `compute_*`/`cluster_*`/`hysteresis` 함수 레벨에서 충족됨.**
- 추출 시 보존 핀: `sort_values('rank_score', ascending=False)` 안정성, `meta.to_csv`는 **clustering 적용 전 전체 universe meta**(L408-412 시점), `meta_full.set_index().loc[selected]` 순서.
- 게이트: `mp test` diff 0 + **실데이터 `mp` diff 0(meta_data.csv 포함)** + **백테스트 1회(41분) walk_forward_results.csv byte-identical** + pytest.

### Phase 5 — shim 제거 + 최종 검증
- Phase 1의 `factor_selection.py` shim 제거. grep `backtest.factor_selection` **0건** 확인을 게이트로.
- 전체 회귀: `mp test` diff 0, pytest 전체, test_determinism(별도 PYTHONHASHSEED 재현), test_oos_purity.
- 문서 갱신: `research.md`(모듈 경로), `README.md`(import 예시 L207/239), `CLAUDE.md`(Entry Point/경로), `docs/VARIABLE_FLOW.md`.

## 4. 수치 byte-identity 보장 전략 (요약)
- **글자보존 이동만**: 추출/이동 직후 `git diff -w`로 본문 변화 0 확인.
- **결정성 체크리스트(2절) 핀**: 7개 핫스팟의 sorted/round/float32를 이동 시에도 1:1 보존.
- **2겹 회귀 게이트**: 모든 단계 = `mp test` 빠른 diff + pytest. 계산경로 단계(4)만 실데이터 mp + 41분 백테스트.
- **CSV 계약 보존**: to_csv index 정책이 파일마다 다름(MP 3종은 index 디폴트, walk_forward/overfit은 `index=False`). 직렬화 이동 시 파일별 인자 그대로 유지. `MXCN1A_` 는 literal로 보존(benchmark 도출식 변환 금지).
- **테스트 프로브 핀**: `_PROBE` 내부 하드코딩 import 문자열을 이동과 동시 갱신.

## 5. 검증 명령
```
# 빠른 게이트 (매 단계)
python main.py mp test test_data.csv      # → _test 출력 diff 0
python -m pytest tests/test_unit/ -v

# 계산경로 게이트 (Phase 4만)
python main.py mp 2009-12-31 2026-03-31   # meta_data.csv 등 diff 0
python main.py backtest 2009-12-31 2026-03-31  # walk_forward_results.csv byte-identical (~41분)
```
