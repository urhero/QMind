# BOK 리팩토링 계획 (Synthesis — 4 reviewer / 36 findings)

> 작성: synthesis agent. 4명의 독립 리뷰어가 제출한 36개 findings를 **현재 코드에 대해 1건씩 검증**하고,
> 중복을 통합하여 단계별 실행 계획으로 정리한다.
> **거버넌스 규칙(신규):** 함수 본문 변경 허용. 단 byte-identical 불필요 — float 말단 자릿수 차이는 허용.
> 모든 작업은 **변경 전/후 산출물 검증**을 통과해야 한다. 각 작업에 FLOAT/OOS 민감도와 그에 맞는 검증을 명시한다.

본 문서는 **분석/계획 전용**이다. 검증 과정에서 소스 파일은 일절 수정하지 않았다.

---

## 0. 핵심 발견 (검증의 큰 그림)

이 코드베이스는 이미 **"restructure 2차" / "Sprint 1"** 리팩토링을 상당 부분 거쳤다. 그 결과 리뷰어들이
제기한 일부 finding은 **이미 해결됨(stale)** 이다. 특히:

- `service/factor/selection.py` 가 이미 존재하고 `compute_rank_score`, `cluster_and_dedup_top_n`,
  `apply_selection_hysteresis` 를 **production(universe.py)과 walk-forward 엔진이 공유**한다.
  → **B-F2(가장 위험하다던 Tier2 중복)는 이미 해소됨 = REJECTED.**
- `service/factor/factor_returns.py` 가 이미 존재하고 `aggregate_factor_returns` 의 실제 home 이다.
  `model_portfolio` 는 하위호환 re-export 만 한다. → A1-F9/D-F5 의 "model_portfolio 소유" 전제는 부분 stale.
- `service/download/paths.py` 가 이미 존재하나 `mreturn_filename()` 하나뿐 — **경로 상수(OUTPUT_DIR 등)는 아직 미이주**.
- `service/report/reporting.py` 로 Rich 출력 분리 완료.

따라서 본 계획은 **현재 코드 기준**으로 살아있는 항목만 담는다.

---

## 1. 검증 원장 (Verification Ledger)

범례: CONFIRMED = 주장+라인 정확 / ADJUSTED = 사실이나 라인/세부 수정 / REJECTED = 거짓·stale·이미수정

### Reviewer A — Pipeline numerical core

| ID | 판정 | 검증 노트 (현재 코드 기준) |
|----|------|----------------------------|
| A1-F1 | **ADJUSTED** | CAGR/MDD 3중복 확인. `benchmark_comparison.py:37-40` & `:72-75` (두 복사본), `optimization.py:88-94`(`np.float32`+`@`matmul — finding이 권고한 대로 **별도 유지**), `universe.py:65`. 라인은 finding이 적은 33-46/68-81/90-94 보다 약간 어긋나나 실체 정확. 추가: `result_stitcher.py:186`, `dashboard_data.py:81-86` 도 동일 공식(→ D-F6과 통합). |
| A1-F2 | **CONFIRMED** | `_construct_and_export` god-method `model_portfolio.py:312-366`. pivot/MP-backfill 블록 `:340-363`. 결정성 가드 `:321-323`(sort) 및 `:346-349`(round(12)) load-bearing 확인. |
| A1-F3 | **CONFIRMED** | `deployed_weights` optional 파라미터가 `weight_history.py:139,187,222` 에 thread, 분기 `:167-168,:258-259`. production 호출부(`model_portfolio.py:168-171`)는 **이 kwarg 미전달** → 휴면. 테스트만 사용(`test_weight_history.py:252,323`). `research.md:670` 이 미사용 명시. `dashboard_data.py` 는 `style_totals` CSV 의 `new_weight` 만 읽고 `deployed_weight` 컬럼은 안 읽음(:233) → 삭제 안전. **단, 테스트 3개가 이 API를 검증 중**이라 제거 시 동반 삭제 필요. |
| A1-F4 | **CONFIRMED** | `ranking_method=="cagr"` 시 중복 cagr 연산. `universe.py:65`(meta["cagr"], `cumprod().iloc[-1]`) vs `:84-89`(rank_score) 와 `selection.py:161-166`(`prod()`). 기본은 tstat(config:52)이라 휴면. **Float HIGH**: `cumprod().iloc[-1]` vs `prod()` 말단 자릿수 상이 — 단순 통합 위험. 저우선. |
| A1-F5 | **CONFIRMED** | `prepend_start_zero` 가 입력을 in-place 변형 `factor_analysis.py:30`(`series.loc[...] = 0`). `test_prepend_start_zero.py` 가 동작 고정. |
| A1-F6 | **CONFIRMED** | `calculate_vectorized_return` 내 `w0` 별칭 혼동 `weight_construction.py:213`(`w0 = turnover_weight_df`). 순수 리네임 가능. reindex/shift/groupby-cumprod 체인(`:211-224`)은 손대지 말 것. |
| A1-F7 | **CONFIRMED** | `optimize_constrained_weights` 기본 `mode="hardcoded"` (`optimization.py:108`) 가 config 기본 `equal_weight`(`config.py:51`)와 모순. 파이프라인은 항상 명시 전달(`model_portfolio.py:140`). 무해하나 함정. |
| A1-F8 | **CONFIRMED** | 손수 짠 `_months_between` `model_portfolio.py:63-67`. step_smooth 입력 — int 동일성 필요. 최저 가치. |
| A1-F9 | **ADJUSTED** | `universe.py:33-37` 이 `model_portfolio` 에서 `OUTPUT_DIR/HISTORY_DIR/aggregate_factor_returns` 를 lazy-import. **그러나** `aggregate_factor_returns` 실제 home 은 이미 `service/factor/factor_returns.py` (model_portfolio 는 re-export). → `aggregate_factor_returns` 는 factor_returns 에서 직접 import 가능(즉시 가능). 경로 상수는 leaf paths 모듈로 이주 후 해소. finding의 "aggregate_factor_returns lives in factor_returns" 는 **이미 사실**. |
| A1-F10 | **CONFIRMED** | `create_equal_weight_benchmark`(`:20-47`) vs `create_mp_portfolio_return`(`:50-82`) 준-클론. return_series 구성만 상이(`:30` mean vs `:64-65` weighted sum). A1-F1 헬퍼로 흡수 가능. |

### Reviewer B — Backtest

| ID | 판정 | 검증 노트 |
|----|------|-----------|
| B-F1 | **CONFIRMED** | `WalkForwardEngine.run()` `:302-578`(≈277줄). Tier2 블록 `:405-530` 6단 중첩. IS-슬라이스 경계 `:409`, 결정성 `sorted(...)` `:542` 모두 load-bearing 확인. |
| B-F2 | **REJECTED** | **이미 해소됨.** `walk_forward_engine.py:430-491` 은 더 이상 inline 복사본이 아니라 `compute_rank_score`(:447), `cluster_and_dedup_top_n`(:467-473), `apply_selection_hysteresis`(:483-486) 를 호출 — `service/factor/selection.py` 공유. `universe.py:87-133` 도 동일 함수 호출. "subtly diverged :455-456" 발산 없음. **Sprint 1 리팩토링이 이미 처리.** |
| B-F3 | **CONFIRMED(ADJUSTED 라인)** | 팩터별 기하복리 루프 중복: `overfit_diagnostics.py:186-190` 및 `:336-340`(거의 동일). weight_rebal 인덱스 추출은 `:147-150`. 라인이 finding의 147-202/307-348 보다 좁음. overfit_diagnostics.csv 전용. |
| B-F4 | **CONFIRMED** | `analyze_cols` slim 블록 `walk_forward_engine.py:67-68` & `:143-144` 동일. (model_portfolio.py 는 `:110`.) 상수 추출 대상. |
| B-F5 | **CONFIRMED** | `_calc_perf` `result_stitcher.py:186` `cumulative.iloc[-1] ** (12/months)` 에 final<=0 가드 없음 → nan. funnel `>` 비교(`overfit_diagnostics.py:83,90`)에서 `nan>x=False` → **재앙적 DD 구간을 조용히 NORMAL 로 오분류**. 엣지케이스(지속 >99% DD 필요)지만 무방비. |
| B-F6 | **CONFIRMED** | `WalkForwardEngine.__init__` `:280-300`, 위치 인자 9개(min_is_months…pipeline_params_override). 모두 default 있음. |
| B-F7 | **ADJUSTED** | `oos_ew_return = oos_factor_returns.mean()` `:558` 은 `available_factors`(`:546`)에 대한 EW. `result_stitcher.py` 주석(:23)이 "선정 팩터" 라 명명. 버그 아님 — 명확화 주석/리네임 정도. 최저 가치. |

### Reviewer C — Data I/O

| ID | 판정 | 검증 노트 |
|----|------|-----------|
| C-F1 | **CONFIRMED** | `download_validation.py:56` `load_factor_parquet(...)[["ddt","factorAbbreviation","gvkeyiid","val"]]` 전체 로드 후 슬라이스. `:57` mreturn 은 이미 `columns=`. `load_factor_parquet`(`parquet_io.py`)는 `columns=` 미지원. TOP 효율 pick. |
| C-F2 | **CONFIRMED** | `model_portfolio.py:227` 전체 history 로드, `:231-234` 전 프레임 category→object 캐스팅(주석이 OOM 근거 문서화, research.md:149). (a)columns= 저위험 / (b)offending col 만 캐스팅 = MEDIUM float/ordering 위험. |
| C-F3 | **CONFIRMED** | `parquet_io.py:132-133` eager `[pd.read_parquet(f) for f in split_files]` + concat → category 업캐스트 + 전 프레임 메모리 상주. |
| C-F4 | **CONFIRMED(권고 약화)** | `parquet_io.py:113-124` glob 후 파일명 int-parse 로 연도 필터. 파일시스템은 numeric predicate 불가 — **현 방식이 적절**. 단 연도 추출 로직을 paths.py 로 중앙화하는 정리는 유효. |
| C-F5 | **CONFIRMED** | `db/factor_query.py:49-86` `fetch_snp` 가 매 호출 conn_url+create_engine+query f-string+dispose 재생성. universe allowlist(`:61-64`) 존재 — 안전. 쿼리 템플릿 상수화 + (선택)엔진 풀링. |
| C-F6 | **CONFIRMED** | 35일 gap / 월별 factor·stock count / NaN-ratio 가 `parquet_io.py`(validate_loaded_factor_data, 35d gap `:269-280`, count `:229/:238`, NaN `:247`) 와 `download_validation.py`(35d gap `:78-86`, count `:89/:101`, NaN `:125-150`) 양쪽에 중복. 목적은 다르나 로직 중복 실재. |
| C-F7 | **CONFIRMED** | `validate_loaded_factor_data` 가 매 mp 로드(validate=True)마다 4회 전 history 스캔: `parquet_io.py:229,238,247,283`. 의도적 무결성 체크지만 gating/재사용 여지. |
| C-F8 | **CONFIRMED** | `save_factor_parquet_by_year` `parquet_io.py:54-55` 전 프레임 `.copy()` + `_year` 임시 컬럼. 방어적 copy 지만 대용량 메모리 영향 실재. |
| C-F9 | **CONFIRMED(부분 ADJUSTED)** | bare `except Exception:` `download_factors.py:87` → max_date="unknown" 무음. `pd.to_datetime(df["ddt"])` 반복은 `parquet_io.py:55`, `download_validation.py:113`, `dashboard_data.py:255` 에 존재하나 **패턴 상이**(literal 3중복 아님). |

### Reviewer D — Cross-cutting

| ID | 판정 | 검증 노트 |
|----|------|-----------|
| D-F1 | **CONFIRMED** | `STYLE_COLORS` 2중정의: `report_generator.py:21-29`(7개, Volatility 없음) vs `dashboard_charts.py:15-24`(8개, `Volatility:#9467bd`). 발산 실재. dashboard_charts 주석이 "복제" 의도 명시하나 단일 출처화 권고 유효. |
| D-F2 | **ADJUSTED(분리 필요)** | **진짜 크래시:** `reporting.py:247`(⚠+em-dash), `:249`(✓) `console.print()` 문자열 — cp949 콘솔에서 `mp --benchmark` 시 UnicodeEncodeError. **로그 문자열:** `config.py:22-26` em-dash 가 `logger.warning(...)` 안(런타임, RichHandler 가 보통 처리하나 ASCII 규칙 위반). **코스메틱:** `main.py:33,42` 화살표는 **주석/소스**(UTF-8 소스라 무크래시). → finding의 "main.py argparse help arrows" 는 부정확(주석임). |
| D-F3 | **CONFIRMED** | 경로 재유도: `model_portfolio.py:55-60`, `dashboard_data.py:19-21`, `download_factors.py:46-48`. `paths.py` 는 `mreturn_filename` 만 가짐(경로상수 없음). download_factors/download_validation 은 `mreturn_filename` 만 import. **A1-F9 와 통합.** |
| D-F4 | **CONFIRMED** | `run_model_portfolio_pipeline()` `:369-381` 가 `pipeline_params` 미수신/미전달. 생성자(`:84`)는 이미 수신. benchmark 경로만 override 구성(main.py:158-166). |
| D-F5 | **CONFIRMED** | `report_generator.py:16` `filter_and_label_factors` from factor_analysis, `:17` `OUTPUT_DIR, aggregate_factor_returns` from model_portfolio(순환). `aggregate_factor_returns` 실제 home 은 `service/factor/factor_returns.py`. **A1-F9 와 통합.** |
| D-F6 | **CONFIRMED** | `compute_kpis`(`dashboard_data.py:81-86`) 가 `_calc_perf`(`result_stitcher.py:186-193`) CAGR/MDD/Sharpe/Calmar 공식 중복. `float()` 래핑만 차이. **A1-F1 과 통합.** |
| D-F7 | **REJECTED** | "filter_and_label 두 번 호출" 거짓. `--report` 시 `model_portfolio.py:114-116` 이 `filter_and_label`(`:121`) **이전에** early-return → 메인은 호출 안 함. `generate_report`(`report_generator.py:151`)가 한 번만 호출. = 조건부 1회. (legacy PDF vs modern viz 중복 우려는 별개의 약한 관찰로만 남김.) |
| D-F8 | **CONFIRMED** | `dashboard.py:162-169` style_cap=0.25 하드코딩 후 `try/except Exception: pass`(noqa BLE001) 로 config override. except 좁히기. |
| D-F9 | **CONFIRMED** | `report_generator.py:99-108` L/N/S 분위 라벨 로직을 하드코딩 0.10 으로 inline 재구현, `factor_analysis.py:93-100`(`filter_and_label_factors`, 파라미터 `spread_threshold_pct`) 와 중복. 차트 라벨 전용. |
| D-F10 | **CONFIRMED** | `_run_benchmark_comparison`(`main.py:151-167`)이 새 `ModelPortfolioPipeline` 구성 + `run()` 재실행 → `mp --benchmark` 런타임 2배. wrapper 가 None 반환이라 재사용 불가. **D-F4 와 통합.** |

**판정 요약:** CONFIRMED 27, ADJUSTED 5 (A1-F1, A1-F9, B-F3, B-F7, D-F2), **REJECTED 2 (B-F2, D-F7)**. 가장 위험하다고 표시됐던 B-F2 가 이미 해소된 것이 핵심.

---

## 2. 통합 작업 항목 (Consolidated Work Items)

민감도: **SAFE** = 수치/행순서 변화 위험 없음 / **SENSITIVE** = 수치 diff 필요.

| WI | 제목 | 출처 findings | 파일 | 변경 내용 | 민감도 | 노력 | 가치 | 의존성 |
|----|------|---------------|------|-----------|--------|------|------|--------|
| **W1** | cp949 콘솔 크래시 제거 | D-F2 | `reporting.py:247,249`; `config.py:22-26` | ⚠/✓/em-dash → ASCII (`[!]`,`OK`,`-`). 로그 문자열 em-dash 도 정리 | SAFE | S | **높음(크래시)** | — |
| **W2** | `_calc_perf` nan 가드 | B-F5 | `result_stitcher.py:186` | `if cumulative.iloc[-1] <= 0: cagr=0.0` (또는 nan→0). funnel 오분류 차단 | SENSITIVE(현 nan 영역만 변함) | S | 중(정합성) | — |
| **W3** | STYLE_COLORS 단일화 | D-F1 | 신규 `service/report/style_colors.py`; `report_generator.py`, `dashboard_charts.py` | 8-style dict(Volatility 포함) 1곳, 양쪽 import | SAFE | S | 중 | — |
| **W4** | leaf paths 모듈 (경로 상수) | A1-F9, D-F3, D-F5 | `paths.py` 확장; `model_portfolio.py`, `dashboard_data.py`, `download_factors.py`, `universe.py`, `report_generator.py` | `PROJECT_ROOT/DATA_DIR/OUTPUT_DIR/HISTORY_DIR` 를 paths.py 로. 각 모듈 재유도 제거 | SAFE | M | 높음(여러 import 해금) | — |
| **W5** | 순환 import 정리 | A1-F9, D-F5 | `universe.py:33-37`, `report_generator.py:17` | `aggregate_factor_returns` 를 `service.factor.factor_returns` 에서 직접 import; OUTPUT_DIR/HISTORY_DIR 는 W4 paths 에서. universe lazy-import 제거 | SAFE | S | 중 | **W4** |
| **W6** | dead `deployed_weights` 제거 | A1-F3 | `weight_history.py:139,167-168,187,222,237,258-259`; `test_weight_history.py` 관련 3 테스트 | 휴면 파라미터/분기 삭제 + 해당 테스트 삭제. (dashboard 미사용 확인됨) | SAFE | S | 중(표면 축소) | — |
| **W7** | optimizer 기본 mode 정합 + w0 리네임 + months util | A1-F6, A1-F7, A1-F8 | `optimization.py:108`, `weight_construction.py:213`, `model_portfolio.py:63-67` | mode 기본값 `equal_weight` 로(또는 명시 강제); `w0`→`base_weights`; `_months_between` 유지/주석 (또는 안전 시 표준화) | SAFE | S | 낮음 | — |
| **W8** | `pipeline_params` 전달 + benchmark 재사용 | D-F4, D-F10 | `model_portfolio.py:369-381`; `main.py:131-137,151-167` | wrapper 가 `pipeline_params` 받아 전달하고 **pipeline 인스턴스 반환**; benchmark 가 재실행 대신 재사용 → `mp --benchmark` 2배 런타임 제거 | SENSITIVE(benchmark 산출물/CSV) | M | 높음(런타임) | — |
| **W9** | analyze_cols 상수 추출 | B-F4 | `walk_forward_engine.py:67-68,143-144`; `model_portfolio.py:110` | `ANALYZE_COLS` 모듈/공유 상수 1곳 | SAFE | S | 낮음 | — |
| **W10** | 공유 성과 헬퍼 (perf_from_returns) | A1-F1, A1-F10, B-F3(부분), D-F6 | 신규 `service/factor/perf.py`(또는 selection 인접); `benchmark_comparison.py`, `result_stitcher.py:186`, `dashboard_data.py:81-86`, `optimization.py`(별도유지) | CAGR/MDD/Sharpe/Calmar 단일 헬퍼. operand order 보존. **float32 site(optimization)는 별도 유지.** A1-F10 두 클론을 헬퍼+return_series 인자로 통합 | SENSITIVE | M | 높음(중복 4곳) | W2(가드 일관) |
| **W11** | L/N/S 라벨 로직 공유 | D-F9 | `report_generator.py:99-108`; `factor_analysis.py:93-100` | 라벨 헬퍼 추출, report 가 호출(하드코딩 0.10 → `spread_threshold_pct`) | SENSITIVE(차트 라벨; CSV 무관) | S | 중 | — |
| **W12** | `load_factor_parquet(columns=)` 푸시다운 | C-F1 | `parquet_io.py`(load_factor_parquet); `download_validation.py:56` | `columns=` 파라미터 추가(pyarrow pushdown), validation 호출부 적용 | SENSITIVE(로드 dtype/행순서 가능) | S | 높음(I/O) | — |
| **W13** | parquet 병합 메모리/dtype | C-F3, C-F4, C-F8 | `parquet_io.py:54-55,113-133` | concat 시 dictionary encoding 보존(read_table/concat_tables 또는 재-astype); 연도 추출 paths 중앙화; `_year` 임시컬럼 대신 standalone Series groupby | SENSITIVE(category dtype/행순서) | M | 중 | W4(paths 연도헬퍼) |
| **W14** | validation 중복 헬퍼화 + gating | C-F6, C-F7, C-F9 | `parquet_io.py:178-290`, `download_validation.py:63-168`, `download_factors.py:87` | 35d gap 상수 + 월별 count + NaN-ratio 공유 헬퍼; 무거운 스캔 gating/재사용; bare except 좁히고 로그; ddt 정규화 1회 | SAFE(진단 전용, 산출물 무관) | M | 중 | — |
| **W15** | factor_query 템플릿/엔진 정리 | C-F5 | `db/factor_query.py:49-86` | 쿼리 템플릿 상수화, (선택)엔진 풀링. allowlist 유지 | SAFE(SQL 결과 동일) | S | 낮음 | — |
| **W16** | `_construct_and_export` 분해 | A1-F2 | `model_portfolio.py:312-366` → `weight_construction.py` | pivot/MP-backfill 블록(`:340-363`) 이동. 결정성 가드 char-preserve | SENSITIVE(MP CSV 핵심 산출물) | L | 중 | W10(권장 후행) |
| **W17** | `run()` 분해 | B-F1, B-F7 | `walk_forward_engine.py:302-578` | `_tier1_rule_rebal/_tier2_weight_rebal/_assemble_oos_record` 추출. IS-슬라이스(:409)·sorted(:542) 보존. `oos_ew_return` 명확화 | SENSITIVE(백테스트+OOS 순수성) | L | 중 | — |
| **W18** | `WalkForwardEngine.__init__` dataclass | B-F6 | `walk_forward_engine.py:280-300`; `main.py:214-222`, research 호출부 | 9 knob → config dataclass | SAFE(호출부 동반 수정) | M | 낮음 | W17 권장 후행 |
| **W19** | dashboard except 좁히기 | D-F8 | `dashboard.py:162-169` | `except Exception` → `(ImportError, AttributeError)` | SAFE | S | 낮음 | — |
| **W20** | `ranking_method=="cagr"` 중복연산 정리 | A1-F4 | `universe.py:65,84-89` | cagr 경로일 때만 meta["cagr"] 와 rank_score 공유. **기본 tstat 라 휴면**; cumprod vs prod 말단차이 주의 | SENSITIVE(HIGH) | M | 낮음 | W10 |

---

## 3. 단계별 계획 (Phased Plan)

각 단계는 **앞 단계 검증 통과 후** 진행. 검증 명령은 §4 프로토콜 참조.

> 인터프리터: `C:\Users\IKM\.virtualenvs\bok-ZkEKkwfv\Scripts\python.exe` (이하 `PY`)

### Phase 0 — 긴급 정합성 (SAFE/소-SENSITIVE, 즉시)  ✅ 완료 (2026-06-25)
- **W1** [완료] cp949 크래시 — `reporting.py:247,249`(이모지/em-dash) + `config.py` warning em-dash ASCII화. 테스트 `test_reporting.py`(cp949 인코딩 가드).
- **W2** [완료] `_calc_perf` nan 가드 — `result_stitcher.py:185-192` final<=0 -> cagr=-1.0. 테스트 `test_result_stitcher_perf.py`(전손 + 정상경로 회귀).
- **W3** [완료] STYLE_COLORS 단일화 — 신규 `service/report/style_colors.py`, report_generator/dashboard_charts 가 import. 테스트 `test_style_colors.py`.

검증 결과: pytest 277 -> 282 passed (신규 5). `mp test test_data.csv` 산출물 28개 **byte-identical**(baseline 일치). W2 정상경로 byte-identical 회귀테스트 통과 — 전손 regime 외 실데이터 영향 없음.

검증: W1/W3 → 빠른 테스트만. W2 → 빠른 테스트 + (가능하면) full backtest diff(아래 nan 영역 외 동일 기대).
```
PY -m pytest tests/test_unit/ -v
PY main.py mp test test_data.csv            # 산출물 변화 없음 기대(W1/W3)
```

### Phase 1 — float 위험 0 구조 정리 (SAFE)  ✅ 핵심 배치 완료 (2026-06-25)
- **W4** [완료] 신규 `service/paths.py` (PROJECT_ROOT/DATA_DIR/OUTPUT_DIR/HISTORY_DIR 순수 상수, mkdir 부작용은 model_portfolio 가 유지). model_portfolio/dashboard_data/download_factors 재유도 제거, re-export 유지.
- **W5** [완료] `universe.py`/`report_generator.py` 가 `aggregate_factor_returns`(service.factor.factor_returns) + 경로상수(service.paths) 를 직접 import. universe lazy-import 제거, model_portfolio<->universe 순환 해소.
- **W6** [완료] dead `deployed_weights` 3개 함수/분기 제거 + 해당 테스트 2개 삭제(가드 1개 유지).
- **W9** [완료] `ANALYZE_COLS` 를 factor_analysis.py 단일 출처로, model_portfolio + walk_forward_engine(2곳) 가 import.
- **W19** [완료] `dashboard.py` `except Exception` → `(ImportError, AttributeError)`.
- **W7a** [완료] `optimize_constrained_weights` 기본 `mode` `"hardcoded"`→`"equal_weight"` (모든 호출부 명시 전달이라 무영향).
- **W7b** [보류] `w0` 리네임 — calculate_vectorized_return(최고 float 민감)의 변수 리네임은 별도 격리 검증으로.
- **W15** [보류] factor_query 템플릿 — live-SQL 무테스트라 검증 경로 확보 후.

검증 결과: pytest 280 passed (W6 에서 obsolete 2개 제거), import smoke 통과(순환 없음, re-export 일관), `mp test` 산출물 28개 **byte-identical**. 전부 mp/backtest 경로 영향 없는 SAFE 변경.
```
PY -m pytest tests/test_unit/ -v
PY main.py mp test test_data.csv && <diff _test 산출물 vs 베이스라인>
PY -c "import main, service.pipeline.universe, service.report.report_generator"   # 순환 import smoke
```

### Phase 2 — float-SENSITIVE 중복 제거  ✅ 안전 부분집합 완료 (2026-06-25)
- **W10** [완료, 범위 축소] `benchmark_comparison.py` 두 near-clone(`create_equal_weight_benchmark`/`create_mp_portfolio_return`)을 `_perf_from_return_series` 헬퍼로 통합. operand order 보존 -> byte-identical.
- **W8** [완료] `run_model_portfolio_pipeline` 가 pipeline 인스턴스 반환 + optional `pipeline_params` 수신. `_run_benchmark_comparison` 은 config mode==equal_weight 면 재사용, 아니면 forced equal_weight 재실행 유지. `mp --benchmark` 파이프라인 실행 2회->1회, benchmark_comparison.csv byte-identical.
- **W10 교차모듈 통합은 보류 (중요):** `_calc_perf`/benchmark 는 `(cum - running_max)/running_max`, `compute_kpis` 는 `cum/running_max - 1.0` 로 **drawdown 연산 순서가 다르다**(대수적으로 동일하나 float 말단 상이). 단일 헬퍼로 강제 통합하면 한쪽 산출물의 float 가 바뀐다. 따라서 synthesis 의 D-F6 "float LOW" 는 **실제로는 통합 비권장** — 각 사이트 operand order 보존이 우선. cross-module perf 통합은 별도 검증(대시보드 KPI float diff 허용 여부 확인) 후.
- **W11** [미실행] L/N/S 라벨 헬퍼.
- **W20** [미실행] cagr-method 중복연산 (기본 tstat 라 비활성).

검증 결과: pytest 280 passed, `benchmark_comparison.csv` byte-identical(reuse 경로), `mp test` 28 artifacts byte-identical. **walk-forward 미접촉이라 41분 백테스트 불필요** — fast track 으로 충분히 검증됨.
```
# 베이스라인(변경 전): aggregated_weights_* / total_aggregated_weights_* / meta_data.csv / walk_forward_results.csv 보존
PY main.py mp 2009-12-31 2026-03-31           # (또는 config PIPELINE_PARAMS backtest_start/end)
PY main.py backtest 2009-12-31 2026-03-31     # ~41분/회
# 변경 후 동일 명령 → diff. float 말단 자릿수만 차이는 허용(§4).
```

### Phase 3 — 데이터 I/O 효율  ✅ 완료 (2026-06-25)
- **W12** [완료] `load_factor_parquet(columns=)` pyarrow 컬럼 푸시다운 + validate_parquet_coverage 적용. mp/backtest 무영향(default None), 신규 테스트로 byte-identity 증명.
- **W14** [완료] `month_gap_issues` 공유 헬퍼(두 validator 중복 제거) + 35d 상수 + cp949 화살표(U+2192->'->') + download_factors bare except 축소. 진단 로직이라 산출물 무영향.
- **W13** [완료, C-F8 한정] `save_factor_parquet_by_year` full-frame copy 회피(외부 연도 Series groupby). save 경로라 mp/backtest 무영향.
  - **C-F3/C-F2b 보류(중요):** `_load_data` 가 OOM 방지로 category->object 강제 캐스팅(model_portfolio.py:228). 따라서 load dtype 보존(C-F3)은 net 효과 미미(즉시 상쇄). 캐스팅 제거(C-F2b)는 category groupby(observed=False) 미관측 조합 행 생성 -> 산출물 변경 위험이라 미실행.
  - **C-F4 보류:** 연도 추출은 현 파일명 방식 적절(reviewer 판단).

검증 결과: pytest 284 passed (parquet_io +4: columns split/single, month_gap helper, no-mutation). Phase 3 전 항목이 mp/backtest 수치 경로 미접촉(W12 opt-in, W14 진단, W13 save) -> fast track 검증. 세션 종료 전 누적 full backtest 회귀 게이트로 최종 확인.

### Phase 4 — 대형 분해 (SENSITIVE)  ✅ 핵심 추출 완료 (2026-06-25)
- **W16** [완료] `_construct_and_export` 의 피벗 단계 -> `weight_construction.build_pivoted_export`. mp 경로 전용이라 `mp test` 28종 byte-identical 로 검증(백테스트 불필요).
- **W17** [완료] `run()` 277줄에서 순수계산 2블록 추출: `_rank_and_select`(랭킹/dedup/히스테리시스), `_assemble_oos_record`(OOS 수익+레코드). **제어흐름(continue/keep-cached)은 의도적으로 미변경** — 버그 위험 회피, 순수계산만 verbatim 이동. full backtest 산출물 3종 커밋기준선과 git diff 공집합 + md5 일치로 검증.
- **W17b 보류 (가드절 평탄화):** Tier2 의 6단 중첩 if/else 를 early-return 가드절로 펴는 제어흐름 리팩토링은 byte-diff 검증이 어렵고(상태 carry/skip 경로) 41분/회 비용이라 별도 집중 작업으로 분리.
- **W18** [미실행] `__init__` 9-knob -> dataclass (research 호출부 동반 수정 필요).

검증 결과: backtest baseline 확보(/tmp/bt_baseline) + 커밋기준선 갱신(43f5ea0) 후,
W16=mp test 28 byte-identical, W17=full backtest 3산출물 git diff 공집합. pytest 280 passed.

> **검증 인프라 발견:** 커밋된 walk-forward 산출물이 56fc16e 이후 stale 였음 -> 43f5ea0 에서
> 현재 결정론적 출력으로 갱신. 이제 backtest-path 변경은 output/ 3파일의 git diff 로 회귀 검증 가능.
```
PY -m pytest tests/test_unit/test_oos_purity.py tests/test_unit/test_determinism.py -v
PY main.py backtest 2009-12-31 2026-03-31     # 변경 전/후 byte-diff (float tol)
```

---

## 4. 전역 검증 프로토콜 (Global Verification Protocol)

### 루프 (모든 작업 공통)
1. **변경 전 베이스라인 보존**: 해당 검증 명령을 먼저 실행, 산출물 복사.
2. **변경 적용 (단일 작업 단위 커밋).**
3. **변경 후 재실행 + diff.**
4. **pytest 통과.** 변경 함수에 테스트 추가/수정.

### 검증 트랙
- **A. 테스트 트랙 (빠름, 모든 작업)**
  - `PY main.py mp test test_data.csv` → `_test` 접미사 산출물 diff
  - `PY -m pytest tests/test_unit/ -v`
- **B. 실데이터 트랙 (느림, ~41분/회 — SENSITIVE 한정)**
  - `PY main.py mp <backtest_start> <backtest_end>` (config `PIPELINE_PARAMS`: 현재 **2009-12-31 ~ 2026-03-31**)
  - `PY main.py backtest <backtest_start> <backtest_end>`
  - 비교 대상: `aggregated_weights_*`, `total_aggregated_weights_*`, `meta_data.csv`, `walk_forward_results.csv`, (해당 시)`overfit_diagnostics.csv`, `benchmark_comparison.csv`

### float 허용 정책
- 백테스트 산출물은 byte-결정적(MEMORY: 2026-06-24 고정). 따라서 **byte-diff 가 1차 회귀 체크**.
- **허용**: float 말단(약 1e-9 이하) 자릿수 차이 — 합산 순서/연산 재배열에서 기인. 행 **개수/순서/키/부호/선정 팩터 집합**은 동일해야 함.
- **불허**: 행 수 변화, 선정 팩터 집합 변화, 부호 변화, 1e-6 이상 값 차이(의도된 변경 제외).
- byte-diff 가 광범위하면 `numpy.isclose(atol=1e-9, rtol=1e-7)` 로 수치 동등성 확인 후 판정.

### 트랙 배정 요약
| 작업류 | A(빠름) | B(full ~41분) |
|--------|:------:|:------:|
| SAFE (W1,W3,W4,W5,W6,W7,W9,W14,W15,W19) | 필수 | 불필요 |
| SENSITIVE-차트/진단 (W11,W14-overfit) | 필수 | overfit_diagnostics.csv 만 비교 |
| SENSITIVE-산출물 (W2,W8,W10,W12,W13,W16,W17,W20) | 필수 | **필수** |
| W17/W16 (OOS·결정성) | 필수 | **필수 + test_oos_purity/test_determinism** |

---

## 5. 권장 첫 배치 (5–8 items, 고가치·저위험 우선)

1. **W1 — cp949 크래시 제거** (Phase 0): `mp --benchmark` 가 cp949 콘솔에서 현재 **실제 크래시**. SAFE/S. 즉시.
2. **W2 — `_calc_perf` nan 가드** (Phase 0): 재앙적 DD 를 NORMAL 로 오분류하는 silent 정합성 버그. S. nan 영역 외 산출물 불변.
3. **W3 — STYLE_COLORS 단일화** (Phase 0): 발산하는 2중정의(Volatility 누락). SAFE/S.
4. **W4 — paths 모듈** (Phase 1): 다른 import 정리(W5)와 향후 W13 연도헬퍼를 해금하는 키스톤. SAFE/M.
5. **W5 — 순환 import 정리** (Phase 1, W4 후): `aggregate_factor_returns` 직접 import + lazy 제거. SAFE/S.
6. **W6 — dead `deployed_weights` 제거** (Phase 1): production 휴면 + dashboard 미사용 확인됨. 표면 축소. SAFE/S.
7. **W9 — ANALYZE_COLS 상수** (Phase 1): 3곳 동일 리스트. SAFE/S.
8. **W19 — dashboard except 좁히기** (Phase 1): noqa BLE001 제거. SAFE/S.

근거: 전부 **검증 빠른 트랙만으로 충분**(SAFE, 또는 W2 의 격리된 nan 영역)이라 ~41분 백테스트 없이 안전하게 머지 가능. W1/W2 는 실제 결함 수정, W3–W9/W19 는 위험 0 구조 정리로 후속 Phase 2 의 SENSITIVE 통합(W10/W8)을 위한 발판을 만든다.

> **주의:** 가장 위험하다고 표시됐던 B-F2(Tier2/evaluate_universe 통합)와 D-F7(report 이중호출)은 **이미 해소/거짓**이므로 본 배치 및 전체 계획에서 제외했다. 잘못된 전제로 SENSITIVE 변경에 착수하지 않도록 검증 원장을 반드시 신뢰할 것.
