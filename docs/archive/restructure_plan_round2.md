# BOK 파이프라인 재구조화 (2차) — 적대적 검증 + 단일 종합 실행계획

> **위치 메모:** 1차 재구조화는 이미 완료됨 (`factor_selection -> service/factor/selection.py`, `scripts -> research/`,
> `overfit 직렬화 추출 -> serialize_diagnostics_csv`, `_evaluate_universe -> service/pipeline/universe.py`).
> 본 문서는 그 위에서 4개 신규 설계안(A 헥사고날 / B 함수형코어 / C 최소위험점진 / D 계약우선)을
> **적대적으로 검증**하고, 검증 통과한 best idea만 접목한 **2차** 실행계획이다.
> 기존 `docs/restructure_plan.md`는 1차 기록(라인번호 일부 stale)으로 보존한다.

> **검증 방식:** 모든 주장은 `C:\Users\IKM\bok` 실제 코드를 Read/Grep으로 교차확인했다.
> 라인번호는 검증 시점(2026-06-25, branch `main-ikm`) 기준 절대값이다.

---

## 0. HARD 제약 (불변)

1. Python 유지 / SQL Server 소스 유지(쿼리·`ORDER BY factorAbbreviation,ddt` 불변).
2. Bloomberg CSV 계약(파일명/컬럼/포맷/encoding/index 정책) 100% 유지.
3. 산출물 byte-identity: `total_aggregated_weights_*`, `*_style_*`, `pivoted_*`, `meta_data.csv`,
   `walk_forward_results.csv`, `overfit_diagnostics.csv`, `walk_forward_weight_history.csv`.
4. "구조만 재조직" — 수식/도메인 로직/동작 불변. 모듈/인터페이스/의존성만.

**검증 인프라(실재 확인):**
- `python main.py mp test test_data.csv` -> `_test` suffix 출력 diff (빠른 게이트)
- `python -m pytest tests/test_unit/` (단위)
- 실데이터 `mp` / 백테스트 ~41분
- `tests/test_unit/test_determinism.py`: 5개 PYTHONHASHSEED(`0,1,2,3,7`) 하위프로세스 교차 byte 검증 — `step_smooth`·`apply_selection_hysteresis`의 set 반복 의존 회귀를 잡음 (확인됨).

---

## 1. 제안별 검증 verdict

각 verdict는 다음을 평가한다: constraint 준수, scope-creep(구조를 넘어 **동작을 바꾸는가**), byte-identity 위험, 종합점수, 살릴 best idea.

### [A] 헥사고날 (ports & adapters) — **종합 5/10**

| 항목 | 판정 | 근거 |
|------|------|------|
| Constraint 준수 | **risk** | 의도는 byte 보존(정렬/round/float32를 core에 유지)이나, 전면 디렉터리 재편(core/ports/adapters/app)이 import 표면을 광범위하게 흔든다 |
| Scope-creep | **부분 risk** | "writer는 쓰기만" 원칙 자체는 동작 무변경. 그러나 헥사고날 풀 채택은 *구조 변경량*이 과대 — 이득 대비 회귀 면적 큼 |
| Byte-identity 위험 | **높음** | A 스스로 인정: "정렬/round를 writer로 잘못 넘기면 즉시 회귀". 검증 결과 정렬/round/float32가 **여러 모듈에 분산**(model_portfolio.py:356/364/382, walk_forward_engine.py:542, smoothing.py:51, result_stitcher.py:98, optimization.py:48/88) — Protocol/adapter 경계로 끌어내는 과정에서 누락 위험 |
| 종합 | 5/10 | 철학은 옳지만 ROI 낮음. walk_forward 상태머신 "포트 주입만" 권고는 현명(추출 금지) |

**Top3 검증:**
1. `aggregate_factor_returns -> core/returns.py` — **유효.** 함수는 `service/pipeline/model_portfolio.py:70-100`에 정의. lazy import 순환 주장은 **과장**: 실제 deferred import은 3건이나, 이 함수를 import하는 것은 `universe.py:33-37`(`evaluate_universe` 내부) **1건뿐**. 나머지 2건은 `report_generator`(model_portfolio.py:337), `DATA_DIR`(walk_forward_engine.py:337)로 별개. "순환 3건 제거" 표현은 부정확하나, 이동 자체는 진짜 순환(`model_portfolio <-> universe`)을 푼다.
2. factor_query 엔진 DI — **유효, but C/D와 중복.** `db/factor_query.py:GenerateQueryStructure.__init__`이 `self._param = PARAM` 하드코딩(line 28), `fetch_snp()`이 인라인 엔진 생성. default-arg DI는 non-breaking.
3. Rich `print_*`/serialize -> presentation adapter — **유효, but C와 중복.** `print_benchmark_report`/`print_overfit_report`/`print_coverage_report` 모두 `-> None`, 호출부가 반환값 미사용. lazy `rich` import. 이동 안전.

**살릴 best idea:** "writer는 쓰기만, 정렬/round/float32/index는 도메인이 소유" 원칙(byte-identity 가드레일로 채택). walk_forward 상태머신 **추출 금지** 권고.

---

### [B] 함수형코어 / 명령형셸 — **종합 4/10 (scope-creep 1건이 치명)**

| 항목 | 판정 | 근거 |
|------|------|------|
| Constraint 준수 | **risk** | Top3 중 1·3은 안전하나 2번이 HARD 제약 4 위반 소지 |
| Scope-creep | **FAIL on #2** | 아래 상술 |
| Byte-identity 위험 | **높음 (CSV write 이동)** | B 스스로 "CSV write 이동이 최대 위험" 인정. categorical<->object 변환 시점 누락 시 groupby 행순서 변동 경고는 **정확** |
| 종합 | 4/10 | 정확한 위험 인식에도 불구, 핵심 제안이 동작을 바꿈 |

**Top3 검증:**
1. `aggregate_factor_returns -> core/returns` — **유효** (A1과 동일 합의).
2. **label_rules를 `filter_and_label_factors`의 명시 출력으로 승격 — 검증 결과 "구조적이지만 공개 시그니처를 바꾸는 회색지대", 채택 보류.**
   - 현 사실: `filter_and_label_factors`(`service/pipeline/factor_analysis.py:34-123`)는 **6-tuple** 반환, `label_rules` 미포함. label_map은 함수 내부(line 111 `label_map = q_mean["label"].to_dict()`)에서 계산 후 `label` 컬럼에만 남기고 폐기.
   - walk_forward는 호출 후 **역산**으로 label_rules 재구성: `walk_forward_engine.py:88-94` `q_labels = fd.groupby("quantile")["label"].first().to_dict()`.
   - "승격"은 *값 보존*(이미 내부에 동일 dict 존재)이므로 **순수 구조 변경에 가깝다**. BUT: (a) 공개 함수 시그니처 6-tuple -> 7-tuple 변경, 호출부 2곳(`model_portfolio.py:154`, `walk_forward_engine.py:78`) + research 스크립트 수정 필요, (b) B가 이를 "OOS look-ahead 방지를 **구조로 강제**"한다고 묶는 순간 *동작 보증의 변경*으로 해석될 위험. **HARD 제약 4("동작 불변, 인터페이스만")의 경계.** 시그니처 변경은 byte-diff로 검증 가능하나, 이득(역산 1회 제거)이 작고 위험(공개 계약 변경)이 있어 **본 종합에서는 배제**(아래 6절).
3. 정렬/round(12)/float32를 core 함수 계약으로 흡수 — **유효 원칙**, A의 가드레일과 합치.

**살릴 best idea:** **categorical->object 변환 타이밍 불변식**을 결정성 체크리스트에 명문화. 검증 확인: `model_portfolio.py:263-267`이 **load 시점, 모든 groupby/sort 이전에** category->object 변환 -> 이 순서가 깨지면 weight CSV 행순서 무성 변동. 이건 B의 가장 값진 기여.

---

### [C] 최소위험 점진 — **종합 9/10 (운영 골격으로 채택)**

| 항목 | 판정 | 근거 |
|------|------|------|
| Constraint 준수 | **pass** | 트리 유지 + 신규 파일만, 동작 무변경 |
| Scope-creep | **없음** | 검증·params·download 통합을 **명시적으로 제외** — 동작변경 위험 회피가 정확 |
| Byte-identity 위험 | **낮음** | re-export 패턴으로 호출부 무수정, 글자보존 이동 |
| 종합 | 9/10 | 위험/이득 비율 최적. 단계별 검증 매트릭스 제시 |

**채택 항목 검증:**
- `print_coverage/overfit/benchmark_report -> service/report/reporting.py` (**low**) — 3 함수 모두 `-> None`, 순수 표현. 안전. (단 `serialize_diagnostics_csv`는 CSV 키 문자열이 load-bearing이므로 함수는 옮겨도 **문자열 불변**.)
- `mreturn` 경로헬퍼 4곳 -> `paths.py` (**low**) — 검증된 4 사이트: `model_portfolio.py:256`, `download_factors.py:185`, `download_validation.py:53`, (+테스트 `test_pipeline_real_data.py:45`). 전부 `f"{benchmark}_mreturn.parquet"` 동일 템플릿 -> 헬퍼화 시 byte-identical 문자열 보장.
- factor_query PARAM DI 기본인자 (**low**) — `param=None` -> `self._param = param or PARAM`. 단일 호출부 위치인자 무영향.
- `aggregate_factor_returns -> factor_returns.py` + model_portfolio에서 re-export (**low~med**) — 4안 공통 합의. re-export로 기존 호출부(`report_generator.py:17/152`, `universe.py`, `walk_forward_engine.py:36/209`) 무수정.

**명시 제외(C의 판단 = 정확):** walk_forward Tier 추출(cached_* 상태 6개·41분 회귀·이득<위험), download `_build_pipeline_ready`/incremental 분기, 검증3중복 통합(임계값/반환형 상이 -> 동작변경).

**살릴 best idea:** **전부.** 운영 골격 채택.

---

### [D] 계약우선 & 확장성 — **종합 6/10 (혼합: 1건 채택, 1건 배제, 1건 보류)**

| 항목 | 판정 | 근거 |
|------|------|------|
| Constraint 준수 | **혼합** | byte-snapshot 회귀 선행 권고는 **탁월**. params frozen은 깨짐 |
| Scope-creep | **부분 risk** | writer 단일 게이트·registry는 동작 보존 가능하나 fallback 계약 보존 필수 |
| Byte-identity 위험 | **중간** | "writer 인자 1개 누락 시 전파일 깨짐"은 D 스스로 인정한 진짜 위험 |
| 종합 | 6/10 | 회귀 테스트 선행 아이디어가 점수를 끌어올림 |

**권고/항목 검증:**
- **byte-snapshot 회귀테스트 선행(test CSV 4종 해시 고정)** — **최우선 채택.** 4안 중 유일하게 "리팩터 전에 안전망부터"를 제시. 현 `mp test` diff 게이트를 **해시 고정 단위테스트로 승격**하면 모든 후속 단계가 보호됨.
- `diagnostics_keys.py` (한국어 Category/Metric 공유상수) — **채택(순수 구조).** 검증: 동일 한국어 리터럴이 **3중 중복** — producer `overfit_diagnostics.py:516/533-541`, consumer `dashboard_data.py:138-151`, +`research/run_cluster_turnover_experiment.py:469-472`. `tests/test_unit/test_overfit_serializer.py`가 이미 "한 글자 바뀌면 viz 파손"을 가드. 상수 추출은 emit 문자열 불변 시 byte-safe.
- `bloomberg.py` (zfill/CH Equity/MXCN1A 중앙화) — **부분 채택.** ticker 빌드는 `weight_construction.py:64` `str.zfill(6).add(" CH Equity")` **단 1곳**. `MXCN1A_`는 `weight_construction.py:61`·config·파일명에 분산. 단일 지점이라 중앙화 이득 작음; **상수만 추출**(동작 무변경)하되 변환식은 그대로.
- `io/writers.py` 단일 to_csv 게이트 — **배제.** 검증: 출력별 index/encoding이 **제각각**(MP 3종 `index=True` 디폴트 utf-8, meta/walk/overfit `index=False`, overfit만 `utf-8-sig` BOM). 단일 게이트는 인자 누락 시 전파일 회귀(D 자인) + 다양성 흡수가 오히려 복잡. **배제.**
- `params: PIPELINE_PARAMS frozen dataclass + dict 어댑터` — **배제(동작변경 위험).** 검증: 라이브 패턴 3종 — (a) `dict(PIPELINE_PARAMS)` + `__setitem__` (`main.py:157-158`, `walk_forward_engine.py:324`), (b) `.update()` (`walk_forward_engine.py:327`), (c) **글로벌 in-place 변이** (`research/experiment_base.py:61-65/125-130` `PIPELINE_PARAMS[key]=val` / `.pop()`). frozen은 (c)를 깨고, `test_walk_forward_engine_override.py:25-34`가 `PIPELINE_PARAMS == before` 불변식을 핀. D 자인대로 `with_overrides` 교체 필요 = 호출부 동작/시그니처 변경. **배제.**
- registry (ranking/weighting 플러그인) — **보류(구조적이나 fallback 계약 리스크).** 검증: `compute_rank_score`(`selection.py:135`)는 unknown method시 **warn+cagr fallback**(test `test_factor_selection.py:156-157`가 핀), `optimize_constrained_weights`(`optimization.py:105`)는 unknown mode시 **ValueError**(test `test_optimize_constrained_weights.py:258`가 핀). registry가 이 두 계약(silent fallback vs raise)을 정확 보존하면 구조적이나, 현재 if/elif 디스패치가 이미 깔끔한 단일 진입점 -> 이득 낮음. **이번 범위 보류.**

**살릴 best idea:** byte-snapshot 회귀 선행 + `diagnostics_keys.py` 상수 추출.

---

## 2. 수렴 분석

### 4안 공통 합의 (만장일치 = 가장 안전·가치 높음)
- **`aggregate_factor_returns` 이주** (A1·B1·C·D 암묵): 4안 전부가 이 함수를 `model_portfolio`에서 빼내자고 함. 검증으로 진짜 순환(`model_portfolio <-> universe`)을 푸는 것 확인. **최우선 합의 이동.** C의 re-export 방식이 호출부 무수정으로 가장 안전.

### 다수 합의
- **Rich `print_*` 표현 분리** (A3·C): 안전. 채택.
- **정렬/round/float32/index를 도메인이 소유, writer는 표현만** (A·B 원칙): 가드레일로 채택(코드 이동 아님, 불변식 명문화).

### 한 안에만 있으나 가치 있는 것
- **D: byte-snapshot 회귀테스트 선행** — 유일. 안전망을 먼저 깔자는 발상이 전체 계획의 ROI를 바꿈. **최우선 채택.**
- **B: categorical->object 변환 타이밍 불변식** — 유일. `model_portfolio.py:263-267` load-시점 변환이 groupby 행순서를 좌우. 결정성 체크리스트에 추가. **채택.**
- **D: diagnostics_keys 공유상수** — 3중 중복 리터럴 + 기존 테스트 핀. 순수 구조 개선. **채택.**
- **C: mreturn paths 헬퍼, factor_query DI** — 저위험 정리. **채택.**

### 합의했으나 **배제**할 것
- **B2 label_rules 승격**: 공개 시그니처 변경 + "OOS 방지를 구조 강제"가 동작보증 변경 경계 -> 배제.
- **D writers 단일 게이트 / params frozen / registry**: 동작변경 위험(배제 2 / 보류 1).
- **A 헥사고날 풀 채택**: 구조 변경량 과대 -> 원칙만 차용.

---

## 3. 단일 종합 실행계획

> **철학:** C(최소위험 점진)를 운영 골격으로, D의 안전망 선행 + B의 결정성 불변식 + A의 writer 가드레일을 접목.
> **불변 원칙:** 함수 본문 1바이트도 안 바꾼다. 위치/import만. 통합·일원화·시그니처 변경·리네이밍 충동 전면 배제.

### 결정성 불변식 체크리스트 (이동 금지 / 변경 금지) — 코드 교차확인 완료

| # | 위치(현재 절대 라인) | 불변식 |
|---|------|--------|
| 1 | `service/pipeline/smoothing.py:51` | `union = sorted(set(target)|set(prev))` — 분류 순회·float 합산 순서 |
| 2 | `service/factor/selection.py:208-229` | `apply_selection_hysteresis` exits `(-score,f)` / entries `(score,f)` / 반환 `sorted(out,(-score,f))` |
| 3 | `service/backtest/walk_forward_engine.py:542` | `available_factors = sorted(...)` — OOS 합산(:557) 순서 |
| 4 | `service/pipeline/model_portfolio.py:356,364` | `sort_values(["factor","ticker","gvkeyiid"])` / `(["style","factor","ticker","gvkeyiid"])` + `reset_index(drop=True)` |
| 5 | `service/pipeline/model_portfolio.py:382` | `final_weights["factor_weight"].round(12)` — pivot 헤더 결정 |
| 6 | `service/pipeline/optimization.py:48,88` | `np.float32` (가중치 벡터/수익률 행렬) — dtype 정리 충동 차단 |
| 7 | `service/pipeline/weight_history.py:127,154,176,240,261` | sorted union + `groupby("style")` + `sort_values` (`save_style_totals`는 `groupby(sort=False)`) |
| 8 | `service/backtest/result_stitcher.py:98` | `reindex(sorted(wh.columns), axis=1)` — weight_history 컬럼 순서 |
| 9 | **`service/pipeline/model_portfolio.py:263-267`** | **category->object 변환은 load 시점, 모든 groupby/sort 이전 (B 기여)** |
| 10 | `service/pipeline/universe.py:50-51,93` | `ret_df.loc[ret_df.index[0]]=0.0; sort_index()`, `sort_values("rank_score", ascending=False)` |
| 11 | `service/pipeline/weight_construction.py:64` | `str.zfill(6).add(" CH Equity")` — Bloomberg ticker (절단 금지, pad만) |
| 12 | `service/backtest/overfit_diagnostics.py:546` | `to_csv(..., encoding="utf-8-sig")` — 유일 BOM 출력 |

이동 시 이 12개는 sorted/round/float32/dtype/encoding을 1:1 보존. 이동 직후 `git diff -w`가 위치 변화 외 0임을 확인.

---

### Phase 0 — 안전망 선행 (D 채택) + 베이스라인
- **byte-snapshot 회귀테스트 추가**: `mp test test_data.csv`가 생성하는 4종(`total_aggregated_weights_*`, `*_style_*`, `pivoted_*`, `meta_data_test_*`)의 **해시를 고정**하는 단위테스트. (overfit/walk_forward는 백테스트 산출이므로 별도 fixture 또는 mp test 범위 외 명시.)
- 베이스라인 캡처: `mp test` 출력 + `pytest` + (가능시) 실데이터 `mp` 1회.
- **검증:** 신규 테스트가 현 코드에서 green인지 확인 (회귀 기준 고정).
- **위험:** 없음 (테스트 추가만).

### Phase 1 — `aggregate_factor_returns` 이주 (4안 만장일치)
- `service/pipeline/model_portfolio.py:70-100` 함수를 신규 `service/factor/factor_returns.py`로 **글자보존 이동**.
- `model_portfolio.py`에서 `from service.factor.factor_returns import aggregate_factor_returns` **re-export** (호출부 무수정 보장 — C 방식).
- `universe.py:33-37`의 lazy import을 새 위치로 갱신 (진짜 순환 해소; 더 이상 model_portfolio 경유 불필요).
- 영향 import: `report_generator.py:17`, `walk_forward_engine.py:36`도 re-export 경유로 무수정 OR 직접 새 경로.
- **검증:** `mp test` diff 0 + Phase 0 해시테스트 green + `pytest`.
- **위험:** 낮음. 함수 내부에 sort/round/float32/index 조작 없음(확인) -> byte 영향 0.

### Phase 2 — Rich 표현 분리 (A3·C 합의)
- `print_benchmark_report`(`benchmark_comparison.py:136`), `print_overfit_report`(`overfit_diagnostics.py:549`), `print_coverage_report`(`download_validation.py:170`)를 신규 `service/report/reporting.py`로 글자보존 이동.
- 셋 다 `-> None`, lazy `rich` import -> 이동 안전. 호출부(`main.py:174/234`, `download_factors.py:282`)는 import만 갱신.
- **검증:** `mp test`(영향 없음) + `pytest`. (콘솔 출력은 byte 계약 아님.)
- **위험:** 낮음.

### Phase 3 — diagnostics_keys 공유상수 (D 채택, 순수 구조)
- producer(`overfit_diagnostics.py:516/533-541`)·consumer(`dashboard_data.py:138-151`)의 중복 한국어 (Category, Metric) 리터럴을 신규 `service/report/diagnostics_keys.py` 상수로 추출, 양쪽에서 import.
- **emit 문자열 1바이트도 불변** (utf-8-sig BOM 포함). `research/run_cluster_turnover_experiment.py:469-472`의 3번째 사본도 동일 상수 사용(선택).
- **검증:** `pytest` 특히 `test_overfit_serializer.py`(키 변경 가드) + `test_dashboard_data.py`. overfit_diagnostics.csv 해시 불변 확인.
- **위험:** 낮음 (테스트가 이미 핀).

### Phase 4 — 저위험 헬퍼 정리 (C 채택)
- `mreturn` 경로 4사이트 -> 신규 `service/download/paths.py` (또는 기존 모듈) `mreturn_path(data_dir, benchmark)` 헬퍼. `f"{benchmark}_mreturn.parquet"` 템플릿 1:1 보존. 4사이트(`model_portfolio.py:256`, `download_factors.py:185`, `download_validation.py:53`, test:45) 치환.
- `db/factor_query.py:GenerateQueryStructure.__init__`에 `param=None` 기본인자 추가 -> `self._param = param or PARAM`. 단일 호출부 위치인자 무영향.
- **검증:** `mp test` diff 0 + `pytest`. (DI는 테스트가 실DB 없이 param 주입 가능해짐.)
- **위험:** 낮음 (생성 문자열·기본동작 불변).

### Phase 5 — 최종 검증
- 전체 회귀: `mp test` diff 0 + Phase 0 해시테스트 green + `pytest` 전체 + `test_determinism`(별도 PYTHONHASHSEED 재현).
- **계산경로 미변경 확인** -> 41분 백테스트는 Phase 1~4가 계산경로를 건드리지 않으므로 **1회만**(또는 생략 가능, 단 보수적으로 1회 권장) `walk_forward_results.csv`·`overfit_diagnostics.csv`·`walk_forward_weight_history.csv` byte-identical 확인.
- 문서 갱신: `research.md`(모듈 경로), `README.md`(import 예시), `CLAUDE.md`(Entry Point/Data I/O 경로), `docs/VARIABLE_FLOW.md`.

---

### 목표 모듈 트리 (현재 -> 목표)

```
bok/
├── main.py                          # 유지 (import 경로만 갱신)
├── config.py                        # 유지 (PIPELINE_PARAMS dict 그대로 — frozen 배제)
│
├── db/
│   └── factor_query.py              # + param=None DI 기본인자 (Phase 4)
│
├── service/
│   ├── factor/
│   │   ├── selection.py             # 유지 (1차에서 이미 승격)
│   │   └── factor_returns.py        # [신규] ← aggregate_factor_returns (Phase 1)
│   │
│   ├── download/
│   │   ├── download_factors.py      # import 갱신
│   │   ├── parquet_io.py            # 유지
│   │   ├── download_validation.py   # print_coverage_report 이동(Phase 2)
│   │   └── paths.py                 # [신규] mreturn_path 헬퍼 (Phase 4)
│   │
│   ├── pipeline/
│   │   ├── model_portfolio.py       # 오케스트레이터 (re-export aggregate_factor_returns)
│   │   ├── universe.py              # lazy import 갱신 (순환 해소)
│   │   ├── factor_analysis.py       # 유지 (label_rules 승격 배제)
│   │   ├── optimization.py          # 유지 (float32 불변)
│   │   ├── weight_construction.py   # 유지 (zfill ticker 불변)
│   │   ├── weight_history.py        # 유지
│   │   └── smoothing.py             # 유지
│   │
│   ├── backtest/
│   │   ├── walk_forward_engine.py   # 유지 (Tier 추출 배제)
│   │   ├── result_stitcher.py       # 유지
│   │   ├── overfit_diagnostics.py   # print_overfit_report 이동(Phase2) + 키 상수화(Phase3)
│   │   └── data_slicer.py           # 유지
│   │
│   └── report/
│       ├── reporting.py             # [신규] print_* 표현 함수 (Phase 2)
│       ├── diagnostics_keys.py      # [신규] 공유 한국어 키 상수 (Phase 3)
│       ├── dashboard*.py            # import 갱신 (키 상수)
│       └── report_generator.py      # import 갱신
│
├── research/                        # 유지 (1차에서 이미 이동)
└── tests/                           # + byte-snapshot 해시테스트(Phase 0), import 갱신
```

---

### byte-identity 보장 전략 (요약)

- **글자보존 이동만:** 추출/이동 직후 `git diff -w`로 본문 변화 0 확인.
- **결정성 체크리스트(12핀):** sorted/round(12)/float32/dtype-timing/encoding을 이동 시 1:1 보존.
- **encoding 다양성 보존:** MP 3종+meta+walk = 디폴트 utf-8(BOM 없음), **overfit_diagnostics만 utf-8-sig(BOM)**. 절대 섞지 말 것 (Bloomberg Optimizer 인제스트 깨짐).
- **index 정책 보존:** MP 3종 `index=True`(디폴트, MultiIndex 포함), meta/walk/overfit `index=False`. 파일별 인자 그대로.
- **dict 순서/float 말단:** set 반복 의존 재유입 금지 — `test_determinism`(5 seed 교차)이 회귀 가드.
- **category->object 타이밍:** load 시점 변환(`model_portfolio.py:263-267`)을 groupby/sort 이전으로 고정 — 이동/리팩터로 순서가 뒤바뀌지 않게.
- **Bloomberg ticker:** `zfill(6)+" CH Equity"` 변환식·`MXCN1A_` 리터럴 보존 (도출식 일반화 금지).
- **2겹 회귀 게이트:** 매 Phase = `mp test` diff + 해시테스트 + pytest. 백테스트 41분은 Phase 5에서 1회(계산경로 무변경 확인용).

---

### 명시적 배제 목록 (이유)

| 배제 항목 | 출처 | 이유 |
|-----------|------|------|
| label_rules를 `filter_and_label_factors` 명시 출력으로 승격 | B2 | 공개 6->7-tuple 시그니처 변경 + "OOS 방지 구조강제"가 동작보증 변경 경계. 이득(역산 1회 제거) < 위험 |
| `io/writers.py` 단일 to_csv 게이트 | D | 출력별 index/encoding 상이(특히 utf-8-sig 1건), 인자 누락 시 전파일 회귀(D 자인). 다양성 흡수가 복잡도↑ |
| PIPELINE_PARAMS frozen dataclass | D | in-place 글로벌 변이(`experiment_base.py:61-65`) + `dict()+__setitem__` + `.update()` 3패턴 깨짐. `test_walk_forward_engine_override`가 불변식 핀. with_overrides 교체 = 동작/시그니처 변경 |
| ranking/weighting registry | D | unknown method=cagr-fallback / unknown mode=ValueError 두 계약 보존 필수, 현 디스패치 이미 깔끔. 이득 낮음 (보류) |
| walk_forward Tier 메서드 추출 | C 제외 합의 | cached_* 상태 6개·41분 회귀·이득<위험 |
| download `_build_pipeline_ready`/incremental 통합 | C 제외 | 분기 복잡, 동작변경 위험 |
| 검증 3중복 통합 | C 제외 | 임계값/반환형 상이 -> 동작변경 |
| 헥사고날 풀 디렉터리 재편 | A | 구조 변경량 과대, ROI 낮음 (원칙만 차용) |
| walk_forward 상태머신 추출 | A 권고 | A도 "추출 말고 포트주입만" — 추출 자체 배제 |

---

## 4. 사용자 결정이 필요한 열린 질문

1. **백테스트 41분 게이트 횟수.** Phase 1~4는 계산경로 미변경(글자보존 이동·import만)이므로 이론상 백테스트 산출(walk_forward/overfit) byte 영향 0. 보수적으로 Phase 5에서 **1회** 검증을 권장하나, "계산경로 무변경"을 신뢰해 백테스트를 **생략**할지 결정 필요. (메모리상 41분/회.)
2. **label_rules 승격을 정말 배제할지.** 검증상 *값 보존*이라 동작은 안 바뀌지만 공개 시그니처가 바뀐다. "역산 제거 + mp/backtest 단일 진실원천"의 유지보수 가치를 위해 **별도 후속 작업**(HARD 제약 완화 합의 하)으로 둘지, 영구 배제할지.
3. **registry 보류 vs 영구 배제.** ranking/weighting 플러그인은 향후 새 method 추가 시 가치. 지금은 보류했는데, 후속 로드맵에 둘지.
4. **byte-snapshot 테스트 범위.** Phase 0 해시테스트를 `mp test` 4종으로 한정할지, 백테스트 산출 3종(고정 소량 fixture)까지 넣을지. 후자는 fixture 구축 비용 있음.
5. **research 스크립트의 import 갱신 범위.** `research/`의 실험 스크립트들이 이동 대상 함수/상수를 참조하는 경우(예: `experiment_base.py`, `run_cluster_turnover_experiment.py`) 동시 갱신할지, 의도적으로 제외(연구코드 자유도)할지.

---

## 5. 검증 명령

```bash
# 빠른 게이트 (매 Phase)
python main.py mp test test_data.csv          # _test 출력 diff 0
python -m pytest tests/test_unit/ -v          # 해시테스트·determinism·overfit_serializer 포함

# 계산경로 게이트 (Phase 5, 1회)
python main.py mp <backtest_start> <backtest_end>      # meta_data.csv 등 diff 0
python main.py backtest <backtest_start> <backtest_end> # walk_forward_*·overfit_* byte-identical (~41분)
# 날짜는 config.py PIPELINE_PARAMS의 backtest_start/backtest_end 사용
```
