# QMind 심층 분석 보고서

> 최종 갱신: 2026-09-16
> 분석 범위: `service/` 전 모듈 + `config.py` / `main.py`. 테스트는 `tests/test_unit/` (`python -m pytest tests/test_unit/ -v`)

---

## 1. 시스템 개요 (Overview)

### 1.1 목적

QMind(구 BOK — 원래 MXCN1A 전용으로 만들었다가 MXWO 로 확장, 2026-09-09 개명)은 **팩터 기반 Model Portfolio(MP) 생성 파이프라인**이다. 200+개 금융 팩터를 분석하여 최종 종목별 투자 비중(MP)을 산출하고, Bloomberg Optimizer에서 바로 사용 가능한 CSV를 생성한다. **MXCN1A(중국 A주)와 MXWO(MSCI World) 두 유니버스를 한 코드베이스가 지원**하며(2026-09-02 통합), 유니버스는 `BENCHMARK`(`.env` 우선, 없으면 `config.py` 상수)가 결정한다 — 아래 [1.1 유니버스 분리](#11-유니버스-분리-2026-09-02) 참조. 본문 서술은 MXWO 채택 스택 기준이며, MXWO의 MP는 **롤링 IS 48개월 + rank_score 순수 Top-50 선정(클러스터 dedup off) + ERC(수축 0.2)·TS모멘텀 틸트 가중 + `style_cap`(25%)/섹터 숏캡(15%)** 으로 구성된다 (관례상 라벨은 Constrained EW) — 공분산/리스크 모델 기반의 종목단 최적화는 커밋 `8dfb64e`에서 제거됨.

### 핵심 Funnel 구조

> Funnel 다이어그램 및 단계별 요약은 [`README.md`](README.md) 참조. 이 문서는 코드 수준 구현 상세만 다룬다.

### 1.2 아키텍처 패턴

**하이브리드 구조: Pipeline 클래스 오케스트레이터 + 순수 함수 모듈**

`ModelPortfolioPipeline` 클래스가 단계를 순차 조율하되, 각 단계의 실제 로직은 `service/pipeline/`·`service/factor/` 모듈의 순수 함수에 위치한다. 클래스는 중간 결과물(`self.meta`, `self.weights` 등)을 인스턴스 변수로 보관하여 디버깅과 사후 분석을 지원한다.

```
main.py (CLI)
  └→ ModelPortfolioPipeline.run()  [오케스트레이터]
       ├→ factor_analysis.py       [2]~[3] 5분위 분석 + 섹터 필터/라벨
       ├→ universe.py              [4] L/S 수익률 행렬 + 선정
       │    ├→ factor/factor_returns.py   aggregate_factor_returns (엔진·별첨과 공유)
       │    └→ factor/selection.py        rank_score / select_factors (엔진과 공유)
       ├→ optimization.py          [6] 가중치 (erc + TS 틸트 + style_cap)
       ├→ weight_construction.py   [7] 종목 전개, 섹터 숏캡, 배포 배수, 피벗
       └→ weight_history.py        회차 간 이력 (mp_weight_history/)
```

### 1.3 기술 스택

| 계층 | 기술 |
|------|------|
| 런타임 | Python 3.10.11, pipenv |
| 데이터 | pandas (주력), numpy, scipy (상관 클러스터링), joblib (팩터별 병렬) |
| DB | MS SQL Server via SQLAlchemy + pyodbc (ODBC Driver 17) |
| 가중 | NumPy 벡터연산 (ERC = Spinu CCD 자체 구현, 외부 솔버 없음) |
| I/O | pyarrow (parquet, zstd 압축, 연도별 분할), CSV |
| CLI/UX | argparse, Rich (로깅, 프로그레스바, 테이블) |
| 보고서 | matplotlib (별첨 PDF), plotly (대시보드 HTML), openpyxl (별첨01 xlsx) |
| 테스트 | pytest, pytest-cov, pytest-xdist |
| 설정 | python-dotenv (.env) |

### 1.4 진입점 (Entry Points)

**CLI 4개 커맨드** (`main.py`: download / mp / backtest / viz):

| 커맨드 | 용도 | 호출 경로 |
|--------|------|-----------|
| `python main.py download <start> <end>` | SQL → parquet 다운로드 | `run_download_pipeline()` |
| `python main.py viz [date] [--open]` | 기존 산출물 → HTML 대시보드 (read-only) | `build_dashboard()` |
| `python main.py mp <start> <end>` | parquet → MP CSV 생성 | `run_model_portfolio_pipeline()` → `ModelPortfolioPipeline.run()` |
| `python main.py mp test <file>` | 소량 데이터 테스트 모드 | 동일 경로, `test_file` 인자 활성 |
| `python main.py mp --report` | PDF 보고서만 생성 후 종료 | `_generate_report()` → `return` (early return) |
| `python main.py backtest <start> <end>` | Walk-Forward OOS 백테스트 + 과적합 진단 | `WalkForwardEngine.run()` → `generate_overfit_report()` |

---

### 1.1 유니버스 분리 (2026-09-02)

- `config.py`: `BENCHMARK = os.getenv("BENCHMARK") or "MXWO"` (폴백 기본값 2026-09-09 MXCN1A -> MXWO; `.env` 없는 CI 가 이 경로) -> `UNIVERSES[BENCHMARK]`(universe/server/db; `.env` 값이 있으면 우선) -> `PARAM`.
  개발은 `main_mxwo` / `main_mxcn1a` 브랜치(형제 폴더 워크트리, 각자 `.env`) -> `main` 머지 (README "유니버스 전환" 표).
  `PIPELINE_PARAMS = {**_COMMON_PARAMS, **_UNIVERSE_PARAMS[BENCHMARK]}`. 미등록 값은 `KeyError`.
- `service/paths.py`: `OUTPUT_DIR = output/{BENCHMARK}/` (구 "MXCN1A 만 루트 `output/`" 예외 제거), `HISTORY_DIR = OUTPUT_DIR/mp_weight_history/`.
- 유니버스 종속 데이터는 전부 `data/{BENCHMARK}/` 폴더 (2026-09-09, 구 `{BENCHMARK}_` 접두어): 팩터/수익률 parquet, `country_map.parquet`, `bmwgt.parquet`,
  `bm_returns.csv`(대시보드 BM 오버레이), `mp_target_gross.csv` / `mp_multiplier.csv`(배포 배수 이력). 파일이 없으면 해당 기능은 no-op
  (MXCN1A: BM 오버레이 생략). 거래세는 파일 유무가 아니라 `apply_country_tax` 로 명시 제어 (MXCN1A False —
  2026-09-02 재다운로드가 `MXCN1A_country_map.parquet` 를 만들자 HKG 등록 A주 4종에 홍콩 인지세가 붙는 것을 막기 위해 도입).
- sharpe1 에서 추가된 키(`min_coverage_pct`, `sector_short_cap`, `mp_target_gross`, `is_window_months`, `deploy_step`, `bm_short_cap`, `weight_rebal_months`)는
  MXCN1A 쪽 값이 전부 "미적용"이라 코드 경로가 main 시절과 동일하다 (통합 시 mp test / 실데이터 백테스트로 검증).

| 항목 | MXCN1A (중국 A주) | MXWO (MSCI World) |
|---|---|---|
| 데이터/서버 | GLOBAL, 2009-12~ | kb_global, 2015-06~ |
| `transaction_cost_bps` | 20 | 10 (+ `COUNTRY_TAX_BPS` 국가별 거래세) |
| `is_window_months` | None (expanding) | 48 (롤링) |
| `use_cluster_dedup` | True (winner_median, ~28 가변) | False (순수 Top-50) |
| `selection_hysteresis` | 0.5 | 0.25 |
| `weight_rebal_months` | 3 | 1 |
| `erc_shrinkage` / `ts_mom_scale` | 0.5 / 0.5 | 0.2 / 0.2 |
| `min_coverage_pct` | 0 | 0.10 |
| `sector_short_cap` | None | 0.15 |
| `mp_target_gross` | 0.14 (롱 +7% / 숏 -7%, 전 기간 소급 2026-09-17) | 0.40 (롱 +20% / 숏 -20%) |
| `apply_country_tax` | False (A주는 등록지 무관하게 본토 인지세 대상 — 등록지 세율표 부적합) | True (COUNTRY_TAX_BPS, 실측 회계 전용) |
| 출력 경로 | `output/MXCN1A/` | `output/MXWO/` |
| 유니버스 종속 데이터 | `data/MXCN1A/` (+ `mp_target_gross.csv`) | `data/MXWO/` (+ `mp_target_gross.csv`, `mp_multiplier.csv`, `bm_returns.csv`, `bmwgt.parquet`, `country_map.parquet`) |
| 정본 실측 (배포 기준, 2026-09-16 look-ahead 제거 후) | Sharpe 0.730 / MDD -0.76% / TE 0.51% (롱숏 ±7%, 거래세 미적용) | Sharpe 0.661 / MDD -1.99% / TE 1.03% (롱숏 ±20%, 거래세 반영) |

공통 항목(style_cap 0.25, spread 0.05, ERC 모드, ts_mom_window 3, deploy_step 1.0 등)은 `config.py`의 `_COMMON_PARAMS`,
유니버스별 항목은 `_UNIVERSE_PARAMS[BENCHMARK]` — `PIPELINE_PARAMS = {**_COMMON_PARAMS, **_UNIVERSE_PARAMS[BENCHMARK]}`.
채택 근거(실험 문서·날짜)는 각 값 옆 주석 참조. MXCN1A: [`mxcn1a_component_ablation_20260805.md`](docs/experiments/mxcn1a_component_ablation_20260805.md),
MXWO: [`mxwo_sharpe_ladder_20260729.md`](docs/experiments/mxwo_sharpe_ladder_20260729.md).

## 2. 데이터 흐름 (Data Flow)

### 2.1 전체 파이프라인 흐름도

```
[SQL Server]                [파일 시스템]              [출력]
     │                           │                       │
     ▼                           │                       │
 download 커맨드                  │                       │
     │                           │                       │
     ├─ fetch_snp()              │                       │
     │  (SQL query w/            │                       │
     │   ROW_NUMBER dedup)       │                       │
     ▼                           │                       │
 _build_pipeline_ready()         │                       │
     │                           │                       │
     ├─ M_RETURN 분리            │                       │
     ├─ factor_info merge        │                       │
     ├─ Undefined 섹터 제거       │                       │
     ├─ categorical 변환          │                       │
     ▼                           ▼                       │
 data/{benchmark}/factor_YYYY.parquet (연도별, zstd)       │
 data/{benchmark}/mreturn.parquet  (단일, zstd)          │
     │                           │                       │
     │    mp 커맨드 시작 ──────────┘                       │
     │         │                                         │
     │    [1] _load_data() + _prepare_metadata()           │
     │         │                                         │
     │         ├─ load_factor_parquet(validate=True)      │
     │         │    ├─ 연도별 분할 자동 병합 (분할 파일만)     │
     │         │    ├─ categorical -> object 변환                │
     │         │    └─ 9가지 무결성 검증                  │
     │         ├─ mreturn.parquet 로드 (M_RETURN)        │
     │         ├─ Test: CSV 로드 + fld 파싱              │
     │         ├─ factor_info.csv merge (factorOrder)     │
     │         └─ M_RETURN merge (gvkeyiid + ddt 기준)   │
     │         │                                         │
     │    [2] _analyze_factors()                          │
     │         │                                         │
     │         └─ calculate_factor_stats_batch()          │
     │              │                                    │
     │              ├─ batch lag: groupby(gvkeyiid,      │
     │              │   factorAbbr).shift(1)              │
     │              ├─ per-factor: rank → percentile      │
     │              │   → quantile(Q1~Q5)                │
     │              └─ sector×quantile 평균 수익률        │
     │                 + 팩터 스프레드(Q1-Q5)              │
     │         │                                         │
     │    [3] filter_and_label_factors()                  │
     │         │                                         │
     │         ├─ 음의 팩터 스프레드(Q1<Q5) 섹터 제거      │
     │         └─ 스프레드x5% 임계 기반 L(1)/N(0)/S(-1) 라벨    │
     │         │                                         │
     │    [4] evaluate_universe()                        │
     │         │                                         │
     │         ├─ aggregate_factor_returns()              │
     │         │    └─ per-factor: L/S 분리 →             │
     │         │       vectorized return (10bp cost)      │
     │         │       → 롱-숏 수익률 합산                 │
     │         ├─ rank_score(t-stat, 롤링 48M) -> Top-50   │
     │         └─ meta_data.csv 저장                      │
     │         │                                         │
     │    [6] optimize_constrained_weights()               │
     │         │                                         │
     │         ├─ mode="erc"(기본): ERC + TS 모멘텀 틸트     │
     │         │    + 스타일 캡 재분배                       │
     │         └─ equal_risk_weight/equal_weight/hardcoded  │
     │         │                                         │
     │    [7] _construct_and_export()                     │
     │         │                                         ▼
     │         ├─ 종목별 동일가중 비중 + MP 합산    → weights_factor_*.csv
     │         ├─ 섹터 숏캡 + 배포 배수(목표 gross)  (위 파일에 반영)
     │         ├─ 스타일별 집계                    → weights_style_*.csv (+ weights_mp_* = MP 행만)
     │         └─ 피벗 테이블 (Bloomberg용)        → weights_pivot_*.csv
```

### 2.2 단계별 데이터 변환 상세

#### [1] _load_data + _prepare_metadata: 데이터 로딩

2가지 경로 존재 (구 단일 파일 / raw parquet fallback 은 제거됨 — 분할 파일만 지원, 없으면 `FileNotFoundError`):

| 경로 | 조건 | 특징 |
|------|------|------|
| **연도별 분할** | `data/{benchmark}/factor_YYYY.parquet` 파일 존재 | `load_factor_parquet()`이 자동 병합. merge 불필요, categorical→object 변환만 수행. `validate=True`로 9가지 무결성 검증 (시간순 정렬 `UNSORTED_LAG_GROUPS` 포함 — lag `shift(1)` 전제 보호) |
| Test | `test_file` 인자 전달 시 | CSV 로드, `fld` 컬럼에서 regex로 factorAbbreviation 파싱, factor_info merge |

**중요 변환**: Pipeline-ready parquet에서 로드 시 `categorical → object` 변환을 수행한다. 이유: `pivot_table`/`groupby`에서 `observed=False` 사용 시 categorical의 전체 카테고리 조합이 메모리를 폭발시키는 OOM 문제를 방지.

**반환값**: `(raw_data, market_return_df, start_date, end_date)`

#### [1 계속] _prepare_metadata: 메타데이터 병합

- `factor_info.csv`에서 factorAbbreviation, factorName, styleName, factorOrder 로드
- Pipeline-ready parquet은 이미 factorOrder가 포함되어 있으므로 merge 생략
- Test 모드(CSV): `factor_info` merge + `sec != 'Undefined'` 필터
- **M_RETURN merge**: `gvkeyiid + ddt` 기본 키 + 가용한 추가 키(`ticker, isin, sec, country`) 사용

**핵심 판단**: `already_merged = "factorOrder" in raw_data.columns`로 경로 분기

#### [2] _analyze_factors → calculate_factor_stats_batch: 5분위 분석

**하이브리드 배치 전략** (성능 최적화의 핵심):

```python
# Step 1: batch lag (전체 DataFrame에 한번만)
df["val_lagged"] = df.groupby(["gvkeyiid", "factorAbbreviation"])["val"].shift(1)

# Step 2: descending 팩터는 val_lagged에 -1 곱하기 (배치)
df.loc[desc_mask, "val_lagged"] *= -1

# Step 3: per-factor 루프 (2키 groupby가 3키보다 2.8x 빠르므로)
for factor_abbr in factor_abbr_list:
    fdf = grouped.get_group(factor_abbr)
    grp = fdf.groupby(["ddt", "sec"])["val_lagged"]
    fdf["rank"] = grp.rank(method="average", ascending=True)
    # ... percentile → quantile → sector return → spread
```

**1개월 래그 메커니즘**: `groupby("gvkeyiid").shift(1)` — 동일 종목 내에서 전월 팩터값을 당월에 매핑. look-ahead bias 방지의 핵심.

**5분위 버킷화 규칙**:
- 백분위 = `(rank - 1) / (count - 1) * 100`
- 버킷 경계: `[0, 20, 40, 60, 80, 105]` (105인 이유: 100% 종목도 Q5에 포함시키기 위한 여유)
- `include_lowest=True, right=True`
- **test_mode=False일 때**: 섹터-날짜 그룹 내 종목 수 ≤ 10이면 `percentile = NaN` → 해당 종목 분위 할당 제외

**sort_order 처리**: `sort_order=0`(낮을수록 좋은 팩터)이면 `val_lagged *= -1`로 방향 통일. 이후 모든 rank는 ascending=True.

**롤링 IS 윈도우** (`is_window_months`, MXWO 기본 48, 2026-07-28 채택): mp `run()`이
`slice_recent_months()`로 merged 데이터를 최근 N개월+lag 기저 1개월로 잘라 규칙
학습·선정·ERC 가중을 레짐 적응형으로 만든다. walk-forward 엔진은 동일 의미의
`is_window_months`를 Tier 1(merged_is)·Tier 2(ret_df_is) 슬라이스에 적용 (CLI
`--is-window-months`, 미지정 시 config, 0=expanding 강제). **use_cluster_dedup 는
MXWO 에서 off** — 롤링 IS 의 출렁이는 rank_score 분포에서 winner_median 의 전역
중위값 바닥이 오작동 (절단 실험: dedup 이 EW_Top50 Sharpe 0.20 을 CEW -0.12 로
파괴, off 시 +0.24). w36~72 스윕 결과 36~48 고원 — 경계점 w36 대신 내부점 w48
채택 (full Sharpe 0.412, 최근 3년 1.397). 지역 중립 랭킹(`ranking_group=
"region_sector"`)은 전 윈도우에서 sector 대비 열위로 기각 — 국가 편중(특히 미국
모멘텀)이 노이즈가 아니라 알파원이었음 (docs/superpowers/specs/2026-07-28-mxwo-region-neutral-ranking-design.md;
구현은 2026-09-02 정리에서 제거, git 이력 참조).

**2026-07-29 Sharpe 사다리 채택분** (상세: docs/experiments/mxwo_sharpe_ladder_20260729.md):
`optimization_mode="erc"`(상관 인지 ERC, cov 48M 대각수축 0.2 — 2026-08-07 전구간 스윕 후 0.7에서 변경) + `spread_threshold_pct=0.05`
+ `selection_hysteresis=0.25` + `weight_rebal_months=1` + `deploy_step=1.0`(10bp 전환 후 부분조정 역전으로 전량 채택 2026-07-30;
`blend_deploy_weights()` — mp/엔진/비용스크립트 공용). **비용의 두 역할 분리가 핵심**:
`backtest_cost_multiplier=0.6`은 선정 입력용(비용 인지 선정이 A/B 최적; 22bp로 올리면
선정 왜곡으로 정본 0.19까지 붕괴)이고, factor-level 성과 회계는 고회전 구성에서 실비용을
최대 1.8배 과소계상하므로 **정본 성과 판단은 `research/mp_level_cost_backtest.py` 실측 기준**
(채택 구성 실측 **2026-07-30 정정**: 초기 ERC 구현이 음의 상관에서 붕괴해 아티팩트 집중
포트폴리오로 0.564가 나왔으나 재현 불가 판정 — 정식 Spinu CCD 솔버 기준 net 0.368 /
gross 0.682 / 월비용 7.4bp / netting 0.65; factor-level 정본 CSV 0.390. 상세: mxwo_sharpe_ladder 로그). mp_level_cost_backtest 는 `--weight-rebal-months/--hysteresis/
--is-window-months/--pp-json` 으로 임의 구성 실측 가능 (parity 9.9e-17 검증).

**2026-07-30 오후 채택분 (비용 10bp 전환 + Calmar 과제 생존자)**: ① `transaction_cost_bps`
20->10 (MXWO 실집행 기준, 사용자 지정) — 선정 입력(10x0.6=6bp)과 실측 회계 모두 변경.
② `deploy_step` 0.5->1.0 — 비용 인하로 트레이드오프 역전 (실측 0.672 vs 0.604, 단조).
③ `ts_mom_window=6/ts_mom_scale=0.5` — 팩터 TS 모멘텀 틸트 (`apply_ts_momentum_tilt()`,
mp/엔진/실측 3곳 공용; 창 6/9/12·감쇠 0.5/0.7 전부 유효 고원). ④ `sector_short_cap=0.15`
— MP 종목 합산 후 섹터별 숏 gross 상한 (`apply_sector_short_cap()`, weight_construction;
2020-11 백신 로테이션형 숏 crowding 완화. 종목 레벨이라 factor-level 백테스트에는 미반영
— 실측 판단). **2026-08-25 수렴 수정**: 구 1-pass 재분배는 수혜 섹터가 캡을 재초과해도
방치했음 (실측 98개월 중 35개월 발동, 2020-11 포함). water-filling(초과 섹터 캡 동결 후
잔여 섹터 재분배 반복, `sector_short_cap_scales()` — 프로덕션/백테스트 stock_level 공용)으로
교체, 재위반 시 수렴 보장. 숏 섹터 수 < 1/cap 이면 캡 우선(숏 gross 축소 + 롱 동반 축소로
달러 중립 유지; 실측 이력 발동 0회). 실측 영향: net Sharpe 0.714→0.715 (성과 중립, 제약 준수화). 최종 실측 (10bp, 채택 스택): **net Sharpe 0.692 / MDD -4.95% / Calmar 0.352**
(EWMA/GARCH fast-vol, semi-cov, 종목 캡, 팩터 MDD 필터는 실측 기각 — 실험 로그 참조).

**단면 커버리지 필터** (`min_coverage_pct`, 기본 0.10, 2026-07-27 채택): 월별 (유효 관측 종목수 / 유니버스 종목수)의 기간 평균이 임계 미만인 팩터를 lag 직후 배치로 제외. 은행 전용 팩터(MXWO에서 NaN ~99%, 7종)처럼 구조적으로 희소한 팩터는 L/S 폭이 좁아 노이즈가 큰데도 클러스터 선정 슬롯과 스타일 예산을 차지하는 문제를 방지. MXWO A/B: 제외 시 CEW 전체 Sharpe 0.106→0.160, 최근 3년 0.056→0.230 (EW_Top50/EW_All 불변 — 효과는 전량 선정 단계에서 발생). **walk-forward에서는 IS 학습(`_run_rule_learning`)에만 적용**하고 전체 데이터 사전계산(`factor_stats_full`)에는 적용하지 않는다 — IS에서 탈락한 팩터는 `kept_abbrs`에서 빠져 OOS에 반영되고, 임계 근처에서 IS/full 커버리지가 엇갈릴 때 kept 팩터의 full stats가 사라지는 불일치를 피하기 위함.

**반환값**: `List[(sector_return_df, None, spread_series, merged_df)]` — quantile_return_df는 None (downstream에서 재계산하므로 불필요)

#### [3] filter_and_label_factors: 섹터 필터 + L/N/S 라벨링

**음의 스프레드 제거 로직**:
```python
# 섹터별 Q1-Q5 스프레드 계산
tmp["spread"] = tmp["Q1"] - tmp["Q5"]
# 음수 스프레드 = 팩터가 역방향으로 작용하는 섹터 → 제거
to_drop = tmp.loc[tmp["spread"] < 0, "sec"].tolist()
```

**L/N/S 라벨 결정 (`spread_threshold_pct` 임계값, 현 0.05)**:
```python
spread = Q1_mean - Q5_mean          # 섹터 제거 후 재계산 (기하평균)
if not spread > 0: 팩터 탈락         # 유효성 가드 (2026-07; NaN 포함)

thresh = spread * spread_threshold_pct   # 현 0.05 (구 0.10)

# 롱 확장: Q1부터 내려가며, 수익률이 (Q1 - thresh) 이상인 연속 분위
q_mean["long"] = (q_mean["mean"] > Q1_mean - thresh).cumprod()

# 숏 확장: Q5부터 올라가며, 수익률이 (Q5 + thresh) 이하인 연속 분위
q_mean["short"] = (q_mean["mean"] < Q5_mean + thresh).abs()[::-1].cumprod()[::-1] * -1

# 합산: long=1, short=-1, neutral=0
q_mean["label"] = q_mean["long"] + q_mean["short"]
```

이 로직의 의미: Q1과 Q5 사이 팩터 스프레드의 `spread_threshold_pct`(5%)를 허용 범위로 두고, Q1에 가까운 수익률을 보이는 분위도 롱에, Q5에 가까운 분위도 숏에 포함시킨다. 결과적으로 Q1=L, Q2~Q4=일부 L/N/S, Q5=S가 된다.

**유효성 가드 (2026-07, EPSEstDispFY1C 사례)**: 섹터별 스프레드 체크(음수 섹터 제거)만으로는 남은 섹터를 **합산**한 전체 스프레드의 역전을 못 잡는다 (섹터별 양수라도 풀링+기하평균 재계산이 음수 가능). 전체 스프레드가 양수가 아니면(<=0 또는 NaN) "Q1이 Q5보다 좋다"는 전제가 깨진 죽은/역전 팩터이므로 탈락시킨다. 수학적으로 spread > 0 이면 Q1은 항상 롱, Q5는 항상 숏이 보장되므로 이 가드 하나로 한쪽 라벨 소멸(롱-only 시장 베타가 t-stat 랭킹 오염)까지 원천 차단된다. 구 동작(warning 후 유지)은 2026-06 데이터에서 롱-only 팩터가 MP에 선정되는 사고로 폐기.

#### [4] evaluate_universe: 팩터 유니버스 평가 및 선정 (클러스터 dedup)

```python
# 1. 팩터별 순수익률 행렬 구성
ret_df = aggregate_factor_returns(filtered_data, kept_abbrs, backtest_start=pp["backtest_start"], cost_bps=pp["transaction_cost_bps"])
# ↳ per-factor: construct_long_short_df → calculate_vectorized_return → net_L + net_S

# 2. 첫 행 = 0 (시작 기준점)
ret_df.loc[ret_df.index[0]] = 0.0

# 3. 0 수익률 월이 max_zero_return_months(10) 초과인 팩터 제거 (데이터 불충분)
valid = ret_df.columns[(ret_df == 0).sum() <= pp["max_zero_return_months"]]

# 4. CAGR(참조 컬럼) + rank_score 계산 — factor_ranking_method (기본 tstat)
#    walk-forward Tier 2 와 동일한 factor.selection.compute_rank_score() 공유
meta["cagr"] = ((1 + ret_df).cumprod().iloc[-1] ** (12 / months) - 1).values
meta["rank_score"] = compute_rank_score(monthly_rets, ranking_method, style_map)

# 5. 선정 — factor.selection.select_factors() 단일 진입점 (walk-forward Tier 2 와 공유, 2026-09-16)
#    MXWO 채택: use_cluster_dedup=False -> rank_score 순수 Top-50 절단
#    (롤링 IS의 불안정한 클러스터 구성에서 dedup이 좋은 팩터를 날림; 2026-07-28
#     A/B on -0.12 / off +0.41. 중복 관리는 [6] ERC 가중이 담당)
#    MXCN1A: use_cluster_dedup=True, cluster_method=winner_median (1등 보호 + 전역 중위값 바닥)
#    + 선정 히스테리시스 (incumbents = 직전 factor_styles raw_weight>0; test 모드 skip)
selected = select_factors(monthly_rets, scores, pp, top_n, incumbents=prev_selected, margin=margin)
```

**aggregate_factor_returns 내부 흐름**:
```
per-factor:
  labeled_data → construct_long_short_df()
    → long_df (label=1, signal="L")
    → short_df (label=-1, signal="S")
  → calculate_vectorized_return(long_df) → net_L
  → calculate_vectorized_return(short_df) → net_S
  → net = net_L + net_S
```

**calculate_vectorized_return 핵심 로직**:
- `pivot_table`으로 (날짜 × 종목) 행렬 생성
- 당월 목표 비중을 당월 수익으로 표류시킨 `w_pre` 산출 (월간 리밸이라 구 "리밸 블록별 cumprod" 는 항등 — 2026-09-16 단순화, 출력 byte 동일)
- 턴오버 = abs(다음 목표 비중 - `w_pre`), 편입 매수/편출 매도 포함 (미보유 월 비중 = 0 처리; 2026-07 수정 — 구 버전은 연속 보유 종목만 계상해 비용 과소)
- 거래비용 = 10bp × 턴오버 (MXWO; 마지막 월은 다음 목표 비중이 없어 비용 0)

#### [6] optimize_constrained_weights: 비중 결정

**가중치 결정 모드 (erc/equal_risk_weight/equal_weight/hardcoded)**:

| 모드 | 용도 | 동작 |
|------|------|------|
| `erc` (기본, 2026-07-29 채택) | 프로덕션/백테스트 | 상관 인지 ERC (cov 48M, 대각수축 0.2, Spinu CCD) + 스타일 캡 재분배 |
| `equal_risk_weight` (구 기본) | 연구 | IS 변동성 반비례(1/sigma) — 상관 무시 특수 케이스 (수축 1.0 극한과 동일 방향) |
| `equal_weight` | 연구 (구구 기본) | 1/N 동일가중 + 스타일 캡 재분배 |
| `hardcoded` | 구 프로덕션 | `data/hardcoded_weights.csv`에서 고정 가중치 로드 |

**erc 모드 알고리즘 (MXWO 채택 스택)**:

```
1. base 가중: 롤링 48M 수익률 cov -> 대각 수축 0.2 -> _solve_erc_ccd()
   (리스크 기여 w_i*(Σw)_i 균등해. 음상관 팩터도 양수 비중 보장 —
    구 곱셈 반복 솔버의 붕괴 결함은 2026-07-30 Spinu CCD 교체로 해결)
2. 스타일 캡 적용 (명목비중 기준):
2. TS 모멘텀 틸트 (ts_mom_window=3, scale=0.2): trailing 3M 자기수익 음수
   팩터의 base 비중 x0.2. 캡 재분배 '이전' 적용 — 캡 준수 보장 (2026-08-06
   순서 교정, main/MXCN1A 와 동일. 구 캡-후-틸트는 EQ 26.1% 초과 사례)
3. 스타일 캡 적용:
   a. 스타일별 비중 합계 계산
   b. 25% 초과 스타일: 비례 축소 (cap / share)
   c. 정규화 (합=1) / 수렴까지 반복 (최대 10회)
4. CAGR/MDD 계산 (기록용)
```

- 채택 근거: Sharpe 사다리 + MP-level 실측 + ERC 붕괴 정정 —
  [mxwo_sharpe_ladder_20260729.md](docs/experiments/mxwo_sharpe_ladder_20260729.md)
- (참고) ERW 채택 이력: [equal_risk_weight_20260722.md](docs/experiments/equal_risk_weight_20260722.md)
- 리스크 예산(w×σ) 기준 스타일 캡(구 `style_cap_basis="risk"`)은 A/B 열위로 기각 -> 구현 제거 (2026-09-02)
- 주의: 캡 재분배 루프는 float32 라 편중 초기가중에서 ~1e-4 캡 초과 잔차 가능 (경고 로그)

**test_mode**: 스타일 캡 재분배 자체를 생략 (소량 데이터에서 제약 충족 불가 방지)

#### [7] _construct_and_export: MP 구성 + CSV 출력

**종목별 비중 계산**:
```python
# 동일가중: label(±1) × factor_weight / count_per_group
df["mp_ls_weight"] = df["label"] * w / count_per_group
df["ls_weight"] = df["label"] / count_per_group
```

**style_ls_weight 계산** (스타일 내 정규화):
```python
# 스타일별 factor_weight 합계 계산
style_totals = unique_factor_fw.groupby(["ddt", "style"])["factor_weight"].sum()
# ls_weight를 스타일 비중으로 정규화
style_ls_weight = ls_weight * factor_weight / style_fw_sum
```

**MP(모델 포트폴리오) 집계**:
```python
# 전체 팩터의 mp_ls_weight 합산 → MP 행
agg_w = weight_raw.groupby(["ddt", "ticker", "isin", "gvkeyiid", "sec"])[["mp_ls_weight", "factor_weight"]].sum()
agg_w["style"] = "MP"
agg_w = apply_sector_short_cap(agg_w, sector_short_cap)   # MXWO 0.15 (water-filling), MXCN1A None
```

**출력 파일 5종**:

| 파일 | 내용 | 용도 |
|------|------|------|
| `weights_factor_*.csv` | 팩터별 + MP 행이 모두 포함된 전체 가중치 | 감사 추적 |
| `weights_style_*.csv` | 스타일별 집계 | 스타일 노출 모니터링 |
| `weights_mp_*.csv` | 위 파일의 style=MP 행만 (종목별 최종 롱/숏 비중) | 최종 비중 확인 |
| `weights_pivot_*.csv` | 피벗 형태 (행=종목, 열=스타일×팩터) | Bloomberg Optimizer 입력 |
| `meta_data.csv` | 팩터 성과 지표 (CAGR, 순위) | 팩터 선정 근거 |

### 2.3 다운로드 파이프라인 (download 커맨드)

SQL Server -> `_build_pipeline_ready()` (M_RETURN 분리, factor_info merge, categorical 변환) -> 연도별 분할 parquet (zstd). 상세 CLI 사용법은 [`README.md`](README.md) 참조.

**두 가지 모드:**
- **전체 모드** (기본): 기존 parquet을 덮어쓰며 전체 재다운로드 (구 `data_backup/` 스냅샷은 2026-09-09 폐지 — data/ 가 git 추적이라 history 가 백업)
- **증분 모드** (`--incremental`): `end_date` 월만 다운로드, 해당 연도 파일만 갱신 (~20MB I/O). 과거 월 backfill(기존 최신 월보다 이전 월 재다운로드) 시에는 `(factorAbbreviation, ddt)` 로 재정렬해 저장 — append 순서가 깨지면 5분위 분석의 lag(`shift(1)`)가 조용히 오염되기 때문 (로드 시 `UNSORTED_LAG_GROUPS` 검증으로도 방어)

**저장 후 검증** (`download_validation.validate_parquet_coverage`): 빈 월, 팩터/종목 수 급감, M_RETURN 정합성 등 5가지

---

## 3. 핵심 의존성 (Dependencies & Touched Files)

### 3.1 내부 의존성 맵

```
main.py
  ├→ config.py (PARAM)
  ├→ service/download/download_factors.py
  │    ├→ config.py (PARAM)
  │    ├→ db/factor_query.py
  │    │    └→ config.py (PARAM)
  │    ├→ service/download/parquet_io.py (save/load/validate)
  │    └→ service/download/download_validation.py (validate_parquet_coverage)  # print_coverage_report -> service/report/reporting.py
  ├→ service/pipeline/model_portfolio.py
  │    ├→ config.py (PARAM)
  │    ├→ service/download/parquet_io.py (load_factor_parquet)
  │    ├→ service/pipeline/factor_analysis.py (prepend_start_zero 포함)
  │    ├→ service/pipeline/optimization.py
  │    └→ service/pipeline/weight_construction.py (build_factor_weight_frames, aggregate_mp_weights, calculate_style_weights 포함)
  └→ service/backtest/ (backtest 커맨드)
       ├→ walk_forward_engine.py (WalkForwardEngine)
       │    ├→ data_slicer.py
       │    ├→ result_stitcher.py (WalkForwardResult)
       │    └→ 기존 pipeline 모듈 순수 함수 직접 호출
       └→ overfit_diagnostics.py
```

### 3.2 외부 의존성

| 의존성 | 용도 | 장애 시 영향 |
|--------|------|-------------|
| **MS SQL Server** (.env에서 SERVER_NAME 로드) | 팩터 원시 데이터 | download 커맨드 실패. mp 커맨드는 기존 parquet으로 동작 가능 |
| **ODBC Driver 17** | DB 연결 | download 불가 |
| `.env` 파일 | DB 비밀번호, 서버 주소, 계정명 등 | `USER_PWD`, `SERVER_NAME`, `USER_NAME` 미설정 시 각각 warning 로그 + DB 연결 실패 |
| `factor_info.csv` | 팩터 메타데이터 (200+ 팩터) | merge 실패 → 분석 불가 |
| `data/hardcoded_weights.csv` | 프로덕션 고정 가중치 (10개 팩터) | hardcoded 모드 실패 |
| `data/{benchmark}/factor_YYYY.parquet` | 연도별 분할 팩터 데이터 (Git 추적) | mp 커맨드 실패 (`FileNotFoundError`, download 선행 필요) |
| `data/{benchmark}/mreturn.parquet` | 시장 수익률 (Git 추적) | mp 커맨드 실패 |

### 3.4 영향 범위 (Blast Radius)

| 변경 대상 | 영향 범위 |
|-----------|----------|
| `factor_analysis.py` 분위 로직 | 모든 downstream (라벨링, 수익률, 가중치, 최종 CSV) |
| `weight_construction.py` 수익률 계산 | `aggregate_factor_returns` → 팩터 순위 → 최적화 → 가중치 |
| `optimization.py` 가중치 계산 | 최종 가중치 (erc 기본 / equal_risk_weight / equal_weight / hardcoded) |
| `optimization.py` hardcoded 가중치 | **프로덕션 MP 직접 영향** — 가장 위험 |
| `config.py` PARAM | 전 모듈 (DB 연결, 벤치마크명, 파일 경로) |
| `config.py` PIPELINE_PARAMS | 파이프라인 비즈니스 파라미터 (style_cap, 거래비용, 팩터 수, 임계값 등). 이전 코드 내 산재하던 매직넘버를 중앙 집중화 |
| `factor_info.csv` 팩터 목록 | 분석 대상 팩터 전체 변경 |
| `hardcoded_weights.csv` | 프로덕션 MP 가중치 직접 변경 |
| `_construct_and_export` 출력 로직 | CSV 포맷 변경 → Bloomberg Optimizer 연동 영향 가능 |

### 3.5 데이터 파일 상세

**hardcoded_weights.csv** (프로덕션 고정 가중치):

| 팩터 | 가중치 | 스타일 |
|------|--------|--------|
| SalesAcc | 22.46% | Historical Growth |
| PM6M | 22.09% | Price Momentum |
| 90DCV | 19.69% | Volatility |
| RevMagFY1C | 12.14% | Analyst Expectations |
| SalesToEPSChg | 6.51% | Earnings Quality |
| Rev3MFY1C | 5.91% | Analyst Expectations |
| CashEV | 4.00% | Valuation (강제 4% 조정) |
| 52WSlope | 3.68% | Price Momentum |
| TobinQ | 2.56% | Capital Efficiency |
| 6MTTMSalesMom | 0.95% | Historical Growth |

### 3.6 MTD 손익 스크립트 (`research/mtd_pnl.py`, 2026-09-17)

파이프라인 밖의 독립 스크립트. 사용법은 README, 여기는 구현 규칙만.

- **상장 선택** (`_pick_listing`): OpenFIGI 상장 리스트에서 (1) 도메사일(`country_map.parquet`) 본상장 exchCode 후보
  `COUNTRY_EXCH` -> (2) `EXCH_PRIORITY` 순, 그 상장의 **composite FIGI** 를 저장. `US` 복합코드는 같은 티커가
  UN/UW/UA(NYSE/Nasdaq/Amex) 에도 있을 때만 인정 — 없으면 OTC (예: Airbus `EADSF`) 이므로 제외.
  요청은 `marketSecDes=Equity` (REIT 포함 — `Common Stock` 필터는 REIT 49개를 놓침). 도메사일과 본상장이 다른 소수
  (Coca-Cola HBC CHE->LN, ArcelorMittal LUX->NA)는 가격 결측으로 드러나므로 `figi_map.csv` 에서 손으로 교체
- **Bloomberg 식별자**: `/isin/<ISIN>` 은 터미널 기본 가격소스 때문에 미국 OTC 라인(KDDI -> `KDDIF US`)으로 풀린다.
  `/bbgid/<composite FIGI>` 는 복합 상장(`9433 JP`)으로 정확히 풀림 — 반드시 후자 사용
- **필드**: `TOT_RETURN_INDEX_GROSS_DVDS` 는 요청 구간 시작가에 리베이스된 배당 재투자 지수 (배당 없는 구간은 PX_LAST 와 동일).
  끝/시작 비율 = 총수익률. USD 환산은 `HistoricalDataRequest` 의 `currency=USD` 오버라이드 (별도 요청 1회). 200 종목씩 청크
- **기준가**: 날짜 정확 일치가 아니라 심볼별 "그 날짜 이하 마지막 거래일" (`asof_ret`, ffill). 국가별 휴일·시차 대응
- **yfinance 기각 사유** (2026-09-17): 1,200 종목 다운로드를 몇 번 반복하자 429 rate limit (시간당 ~2,000 요청) 으로 수십 분
  대기가 필요했고, 당일 실행 시 진행 중인 당일 봉이 섞여 들어와(end 배타 규칙 무시) 값이 흔들렸다 (CRDO -33.5% vs Bloomberg -28.6%).
  Bloomberg 티커 != Yahoo 티커(북유럽 종류주 `ATCOA`/`ATCO-A.ST`, 싱가포르 코드) 보정도 필요했다. 매핑 파일이 NaN 으로
  덮이는 사고가 있었으므로, 소스를 다시 바꿀 때도 "광역 실패 시 매핑 파일 불변" 가드는 유지할 것
- **의존성**: `blpapi` (Bloomberg 전용 pip 인덱스, Pipfile `[[source]] bloomberg`), OpenFIGI 는 `requests` 직접 호출
  (키 없으면 10건/요청·분당 25요청, `OPENFIGI_API_KEY` 는 100건/요청)
- **MXCN1A**: 산출물 ticker 가 이미 Bloomberg 형식(`000001 CH Equity`) 이라 FIGI 매핑 없이 직접 조회 (`attach_securities`)

#### 3.6.1 일별 대시보드 (`research/mtd_dashboard.py`, 2026-09-17)

- **캐시 필드는 일수익률** `DAY_TO_DAY_TOT_RETURN_GROSS_DVDS` (%/100). 총수익지수는 요청 시작일에 리베이스되므로 증분
  append 하면 기준이 어긋난다. 일수익률은 날짜별 독립이라 안전. 증분 기준은 캐시 최신일 (결측 종목이 있어도 전체 재조회 안 함)
- **거래일 정의**: BM 지수(`{BENCHMARK} Index`)가 수익률을 찍는 날. 별도 달력 없음. 규칙 리밸런싱일 = 그 달 3번째 거래일
- **두 북 이어붙임** (`stitch`): 종목 x 날짜 누적기여 행렬. 구간 A(<= 리밸런싱일) 는 전월 북 buy-and-hold 누적, 구간 B 는
  A 말값(전월 북 종목에 고정) + 당월 북 누적. 손익은 NAV 대비 오버레이라 구간 합산이 가산적
- **전월 북 로딩**: 구형 `total_aggregated_weights_*_mp*.csv` (style=='MP' 행) 도 읽는다 (`style` 파일 제외, isin NaN 행 제외).
  배포 규모의 정본은 `mp_target_gross.csv` 이력 — 파일 gross 가 다르면 그 목표값으로 재스케일 (MXCN1A 7월 북 gross 94% -> 14%, 2026-09-17 전 기간 소급)
- **HTML**: 데이터 JSON 을 템플릿(`mtd_dashboard_template.html`) 의 `/*__DATA__*/null` 자리에 주입. 종목 x 날짜 **일수익률**을
  통째로 넣고(1,250 종목 x 22일 ~ 600KB) 이어붙임·분해는 JS 가 계산 -> 리밸런싱일을 월말/1영업일/.../N영업일로 바꿔 보며
  타이밍 효과를 비교할 수 있다 (기본값 = 로그의 실제 일자, 다른 값이면 "가정" 배지). "전월 북 규모를 당월 gross 로 맞춤"
  토글은 w_prev 에 gross_cur/gross_prev 를 곱한다 (MXCN1A 7월 북 gross 94% 처럼 배포 규모 기록이 의심될 때).
  롱/숏 분류는 그 날짜의 활성 북 비중 부호. 스타일은 종목별 **부호 있는 스타일 비중**으로 같은 이어붙임을 계산.
  **섹터 숏캡 적용 후** (2026-09-18): 캡·스무딩은 종목 MP 행에만 적용되고 팩터 행 `mp_ls_weight` 는 캡 전 값이므로,
  종목별 비율 `ls_weight(캡 후, 배포 scale 포함) / Σ팩터 mp_ls_weight(캡 전)` 을 그 종목의 팩터·스타일 비중에 곱한다
  (deploy_step=1.0 이라 정확, 블렌딩이 있으면 비례 배분 근사). 결과: 스타일·팩터 기여 합 == MP 기여 (오차 0).
  표의 합계는 두 줄 — "netting 전"(스타일 간 상쇄 안 한 베팅 크기, 롱 28.45%) / "netting 후, MP"(실제 배포, 17%).
  2026-09-17 채팅의 "롱/숏 각 8.48%" 는 팩터 간·캡 전 값이라 대시보드(6.49%)보다 크다.
- **팩터 상하위 5**: 종목별 `{팩터 인덱스: 비중}` 희소 리스트(`f_prev`/`f_cur`, 두 북 합쳐 ~48k 항목, HTML ~1.5MB)를 넣고
  스타일과 같은 이어붙임으로 팩터 기여 계산. 팩터->스타일 맵은 `load_book` 이 모듈 전역 `FACTOR_STYLE` 에 누적
  (DataFrame.attrs 는 merge 에서 소실, dict 반환 apply 는 MultiIndex 로 펼쳐지므로 루프로 생성)
- **종목명/팩터명**: 종목명은 Bloomberg `NAME`(`mtd_pnl.bdp`, ReferenceDataRequest) 을 `data/{BM}/security_names.csv` 에 캐시
  (없는 sec 만 조회). 팩터는 `data/factor_info.csv` 의 `factorName`(영문 정식명, 전 팩터) 만 표 두 번째 줄에 표시 —
  `factor_desc_kr.csv` 한글 설명은 40개만 있어 일관성 때문에 쓰지 않는다 (사용자 결정 2026-09-18)
- **리포트 탭 레이아웃**: 화면과 인쇄가 같아야 하므로 반응형 클래스 대신 고정 열 (KPI `1fr 1fr 1fr 1.6fr 1fr`, 표는 한 열로 쌓기).
  인쇄에서는 글자·여백만 축소. 수익률·기여는 KPI/표/차트 툴팁 모두 % 소수점 4자리
- **현재 포트 탭** (2026-09-18, 구 iframe 내장 철회): `load_book` 이 북별 `factor_weight`(캡 후 배분) 를 `FACTOR_WEIGHT[날짜]` 에 담아
  `factor_weight`/`factor_weight_prev`/`style_cap` 으로 내보내고, JS 가 `D.stocks` 의 `w_cur`/`style_cur` 로 섹터·종목 스타일 분해
  (barmode=relative + 순노출 ◆), 스타일 배분(캡선), 전월 대비 팩터 델타(±15), 비중 순위 표를 그린다. 구 `service/report/dashboard.py`
  의 '현재 포트/배팅' 섹션과 같은 정보를 같은 디자인으로 — 백테스트 섹션은 포함하지 않음 (사용자 선택). 조립 방향은 갱신 빈도 기준
  (MTD 월 10~20회 > viz 월 1~2회) 이었으나 iframe 은 디자인 불일치로 기각
- **배포 기준 통일** (2026-09-21 사용자 지정 '모든 비중·수익률 배포 기준'): (1) 팩터/스타일 '비중' = 배포 롱/숏 gross (JS 가 `f_cur`/`style_cur` 합산,
  캡 후) — 팩터 배분 비율(factor_weight, 합 100%)은 현재 포트 탭의 캡 차트에만 남김. (2) 1M/3M/YTD/1Y = **현재 배포 비중으로 그 기간 보유 시 기여** (2026-09-21 재지정): JS 가 팩터별 현재 한쪽 다리 크기
  (롱+|숏|)/2 x `factor_returns_matrix` 복리수익률(`factor_horizons`). MP 합계 = 팩터 합. 과거 실제 비중이 아니라 '지금 북을 그때 들고
  있었다면' 이라 팩터 간 비교용. 대안(과거 실제 기여 = factor_contrib x 배포 배수)은 `deploy_multiplier_series()` 에 남겨 둠; 배수(월) = mp_target_gross.csv 목표 ÷ deploy_multiplier 스냅샷의 book_gross_before, 둘 다 계단식
  ffill + 최초 이전 bfill (사용자 규칙). MXWO 8월 0.3999(기록값과 동일), 최초 이전 0.4455; MXCN1A 전 기간 ~0.15 (14% 소급과 일치).
  (3) 현재 포트 탭 팩터 델타/순위도 배포 gross 기준 (전월 북은 그 달 배포 규모 그대로 — 규모 변경분이 델타에 포함)
- **종목 기여 탭** (2026-09-21, Bloomberg PORT 캡처 참고): 설정 바 기간·기준 공유. 평균 비중 = Σ_t w_active(t)·drift / (idx+1)
  (`avgWeight`: t<=k 전월 북 x G[t], 이후 당월 북 x G[t]/G[k+1]; 모델은 당월 북 x G[t]), 수익률 = G[idx+1]−1, 기여 = contribAt,
  배포 비중·스타일 열 = 기준일의 **활성 북** (리밸런싱일 이전이면 전월 북 `style_prev` x 규모 맞춤 배수, 이후 당월 북 `style_cur`).
  합계 기여 == MTD 실현. 검색·정렬·롱/숏 필터는 JS, 인쇄 제외. Bmrk/Alloc/Selec 열은 BM=0 이라 생략. 이 탭만 `main` 의
  max-w-6xl 을 풀어 화면 전체 폭 사용(15열, 1,600px 에서 가로 스크롤 없음), 셀 11.5px/4px 로 촘촘하게
- **Bloomberg PORT 대조 (2026-09-21, MXCN1A MTD 9/18)**: Bloomberg Active CTR 9.60bp vs 대시보드 2.90bp. 로컬(CNY) 수익률은 종목 단위로
  일치(중앙값 차이 0bp) -> 계산 방식 차이가 아니라 **MP != AP**. 분해: 겹침 394종목 +4.44bp (부호 반대 50종목 -0.99, 크기 2배 이상 차이 88종목
  +1.64, 나머지 +3.79 = 실제 매매 시점·일별 링크), Bloomberg 에만 있는 26종목 +2.16bp (월중 지수 편입 등), MP 에만 있는 종목 -0.10bp.
  핵심 원인: Bloomberg 최적화 유니버스 = 실제 BM(소형주 제외)이라 MP 526종목 중 133종목(gross 3.6%p)이 AP 에 없음. 숏 절단은 1종목(무관).
  분석 스크립트: scratchpad `cmp_bbg.py`/`cmp2.py`/`cmp3.py` (일회성, 저장소 밖)
- **종목 기여 (임시) 탭** (2026-09-21): `bm_restrict()` — `bm_universe.csv` 밖 종목 비중 0, 남은 롱·숏을 각각 목표(gross/2)로 비례 조정
  (사용자 지정: 롱숏 합이 0 이 안 되면 비례 조정). 종목별 배율을 스타일 열에도 곱함(`sb_prev/sb_cur`), 팩터 열은 없음. 조정 내역(`bm_adj`)을
  탭 머리에 표시. 파일이 없는 유니버스(MXWO)는 탭 숨김. 결과 MXCN1A 9/18: 6.84bp (Bloomberg 9.60bp 와의 잔차는 AP 최적화·매매 시점)
- **비중 표기 통일** (2026-09-23 사용자 지정): 대시보드 MTD·주간 리포트 탭의 스타일 표와 팩터 상하위 10 표, 한 장 PDF 의 스타일 표와
  팩터 차트 주석 모두 `배분% (롱 / 숏)` 한 열 — 배분 = 팩터 배분 비율(factor_weight, 합 100%; 스타일은 그 합), 괄호 = 배포 롱/숏
  소수점 1자리(섹터 숏캡 후). 2026-09-23 최종: 괄호는 **롱·|숏| 평균 한 값** `25.0% (3.9%)` — BM 유니버스 제한 후 팩터별 롱/숏이
  어긋나므로(소형주 제외가 다리마다 달라 롱/|숏| 비율 0.77~1.58) 한쪽 다리 크기로 요약. 합계 netting 전 = `100.0% (Σ롱% / Σ숏%)`,
  netting 후 = `(MP 롱% / 숏%)`. `wfmt()` 헬퍼 (JS·Python 동일 서식)
- **한 장 PDF 레이아웃** (2026-09-23): 두 섹션이 같은 구조 — 왼쪽 가로 막대(기여 bp, 위가 1등, 막대 끝 라벨), 오른쪽 `비중 (롱 / 숏)` 주석
  (`contrib_chart`/`right_notes` 공용). 스타일 섹션도 기여 내림차순, 합계 두 줄은 차트 아래 텍스트. 스타일 범례는 제목 아래 한 줄로 한 번만.
  주의: 사용자가 PDF 를 뷰어에 열어 두면 PermissionError 로 덮어쓰기 실패 — 닫고 재실행
- **MTD 탭 재구성** (2026-09-21 피드백 '난잡'): 스타일 차트+표 -> 표 하나(배분 막대·롱/숏·기여 막대), 팩터는 상·하위 10 (비중순/기여순)
  + 지평 성과 열. 1W 기여 = 누적기여(idx) − 누적기여(idx−5) (이어붙임 그대로), 1M/3M/YTD/1Y = `factor_horizons()` 가
  `factor_returns_matrix_*.csv`(백테스트 팩터 L/S 월수익률) 를 복리 — 마지막 월말까지이며 배포 북 기여와는 정의가 다르다 (표 머리에 명시).
  국가·롱/숏 표는 `<details>` 로 접음. 설정 바를 헤더에 붙여 MTD·주간 리포트가 같은 상태임을 드러냄 (현재 포트 탭에선 숨김)
- **디자인** (2026-09-18 사용자 피드백 '색·폰트', 톤 '깔끔한 리서치 리포트'): 라이트 기본(종이 배경 #f5f6f8, 잉크 #14171c, 파랑 포인트 #2f5fd1 하나),
  다크는 토글. 카드 대신 얇은 괘선과 여백, KPI 는 divide-x 한 줄. 스타일 8색은 템플릿 `STYLE_COLORS` 단일 출처 (표 좌측 색띠·차트 공용)
- **테마/인쇄**: 색은 전부 CSS 변수(`:root` 다크, `html.light` 라이트, `@media print` 라이트 강제). 토글은 localStorage
  (`qmind-mtd-theme`, try/catch). Plotly 는 테마 전환 시 `relayout` 으로 폰트/그리드 색 재적용. 인쇄는 `.no-print`(선택기·슬라이더·토글)
  숨김 + `.print-only` 머리글(유니버스·기준일·실현/모델/BM·리밸런싱) + `break-inside: avoid`, A4 세로 12mm 여백
  검증: JS 시리즈가 파이썬 콘솔 값(실현 +0.2929% / 모델 +0.3573%)과 일치 (2026-09-17)

---

## 4. 주요 제약 사항 및 엣지 케이스 (Constraints & Edge Cases)

### 4.1 건드리면 안 되는 로직

#### 4.1.1 1개월 래그 (`shift(1)`)
- **위치**: `factor_analysis.py` (batch/단건 모두)
- look-ahead bias 방지의 핵심. 상세 메커니즘은 §2.2 [2] 참조. `gvkeyiid` 단위 적용 필수

#### 4.1.2 hardcoded 가중치 모드
- **위치**: `optimization.py:_get_hardcoded_weights()`
- **주석**: `"이 주석 지우지 말것! DO NOT DELETE THIS COMMENT!"`
- **이유**: 프로덕션 MP의 실제 투자 가중치. `_get_hardcoded_weights()`의 CSV 경로와 반환 구조를 변경하면 프로덕션 포트폴리오가 깨짐
- **특이사항**: `Valuation` 스타일(CashEV)은 시뮬레이션 결과와 무관하게 강제로 4%로 설정 (투자 위원회 결정)

#### 4.1.3 스타일 캡 25%
- **위치**: `config.py:PIPELINE_PARAMS["style_cap"]` → `optimization.py` 파라미터로 전달
- **이유**: 프로덕션 규제 요건. 단일 스타일 집중 위험 통제

#### 4.1.4 거래비용 10bp (MXWO)
- **위치**: `config.py:PIPELINE_PARAMS["transaction_cost_bps"]` → `factor_returns.aggregate_factor_returns()` → `weight_construction.py` 파라미터로 전달
- **이유**: MXWO 선진국 대형주 실집행 기준 10bp (2026-07-30 사용자 지정; MXCN1A는 20bp). 변경 시 모든 팩터의 순수익률과 순위가 변동. 백테스트는 여기에 `backtest_cost_multiplier`(0.6)를 곱해 netting 반영

#### 4.1.5 sort_order(factorOrder) 방향 통일
- **위치**: `factor_analysis.py` batch [4] 단계
- **의미 (factor_info.csv 실데이터 기준)**: `factorOrder=0` = **높을수록 좋음** (ROE, ROIC, CashEV 등), `factorOrder=1` = **낮을수록 좋음** (부채비율 DA/LTDE, PM6M=Price Reversal 등)
- **로직**: factorOrder=0 팩터의 val_lagged에 -1을 곱해 전 팩터를 "낮을수록 좋음"으로 통일 -> ascending rank 1(=Q1)이 항상 "좋은" 종목. 이 로직이 누락되면 해당 팩터의 L/S가 뒤집힘
- (과거 이 절과 docstring 이 0/1 의미를 반대로 기재했었음 — 코드 동작은 처음부터 위와 같았다)

### 4.2 숨겨진 규칙 / 암묵적 계약

#### 4.2.1 파이프라인 실행 순서 불변
`run()` 내 [1]~[7]은 순차 의존성이 있다. 순서 변경 불가.

#### 4.2.2 M_RETURN merge 키 정합성
`_prepare_metadata`에서 M_RETURN은 `merge_keys = ["gvkeyiid", "ddt"]` + 가용한 추가 키로 inner join된다. Pipeline-ready parquet은 `(gvkeyiid, ddt)` 2키만으로 충분하지만, test CSV는 추가 키(`ticker, isin, sec, country`)가 포함되어 있어 자동으로 사용된다. **merge 키가 달라지면 행 수가 달라질 수 있음**.

#### 4.2.3 quantile 경계값 105
`pd.cut(bins=[0, 20, 40, 60, 80, 105])` — 상한이 100이 아닌 105인 이유: 백분위 100%인 종목(섹터-날짜 그룹에서 rank=count)도 Q5에 포함시키기 위함. `right=True`이므로 100은 (80, 105] 구간에 해당.

#### 4.2.4 `ret_df.loc[ret_df.index[0]] = 0.0`
`evaluate_universe`에서 수익률 행렬의 첫 행을 0으로 설정한다. 이는 `factor_analysis.prepend_start_zero()`와는 별개의 처리이며, aggregate 이후 첫 날짜의 수익률을 기준점 0으로 강제한다. CAGR 계산의 시작점 역할.

#### 4.2.5 categorical 변환 타이밍
다운로드 시 `object -> categorical` (zstd 최적화), 파이프라인 로드 시 `categorical -> object` (groupby OOM 방지). 상세는 §2.2 [1] 참조. categorical + `observed=False`는 OOM을 유발한다.

#### 4.2.6 `report` 모드의 early return
`_generate_report()`는 보고서 생성 후 반환하고, `run()`에서 `return`으로 이후 단계를 스킵한다. (이전에는 `sys.exit(0)`이었으나 테스트 가능성을 위해 제거됨)

#### 4.2.7 `(ret_df == 0).sum() <= 10` 필터
수익률이 0인 월이 `PIPELINE_PARAMS["max_zero_return_months"]`(10)개를 초과하는 팩터는 데이터 불충분으로 제거된다. **2026-09-16 교정:** 카운트에서 첫 행(강제 기준점 0)을 제외한다 — 구 코드는 기준점 행까지 세서 설정값 10 의 실질 의미가 "실제 0 수익 월 9개 초과"였다. mp(`evaluate_universe`)·엔진 Tier 2·별첨(`report_generator`) 세 곳 모두 `iloc[1:]` 로 세고 config 값을 쓴다 (별첨은 구 코드가 10 을 직접 하드코딩했었음). 경계(정확히 10개월)에 걸린 팩터만 결과가 달라진다 — A/B 결과는 §6.2.2.

#### 4.2.8 factor_weight의 neutral 제거
```python
weight_raw["factor_weight"] = weight_raw["factor_weight"] * (weight_raw["mp_ls_weight"] != 0).astype(int)
```
`mp_ls_weight`가 0인 행(neutral 종목)의 `factor_weight`를 0으로 만든다. 중립 종목의 팩터 가중치를 제거하는 효과. (이전에는 `np.sign()**2`로 동일 효과를 냈으나 가독성을 위해 명시적 boolean mask로 변경)

### 4.3 알려진 엣지 케이스

#### 4.3.1 단일 종목 섹터
섹터-날짜 그룹에 종목이 1개뿐이면 `count - 1 = 0`. `np.where(count > 1, ..., np.nan)` 가드로 division by zero를 방지하며, `percentile = NaN` → quantile 할당 불가 → 해당 종목 제외. test_mode에서도 동일.

#### 4.3.2 동일 팩터값 종목들
`rank(method="average")` 사용으로 동일 값 종목들은 평균 순위를 받음. 그러나 모든 종목의 팩터값이 동일하면 전부 같은 percentile → 하나의 분위에만 몰림.

#### 4.3.3 히스토리 3개월 미만 팩터
`ddt.unique() <= 2`이면 건너뜀 (batch 모드: `date_counts > 2`). 정확히 3개월이면 lag 적용 후 2개월 데이터로 분석 진행.

#### 4.3.4 hardcoded_weights.csv에 없는 팩터
`_construct_and_export`에서 `fac not in factor_idx_map`이면 해당 팩터를 건너뜀 (warning 로그). hardcoded 가중치의 팩터가 실제 데이터에 존재하지 않으면 해당 가중치는 무시됨.

#### 4.3.6 증분 다운로드 후 팩터 구성 변화
증분 모드로 새 월을 추가할 때, 기존 월에 없던 새 팩터가 등장하거나 기존 팩터가 누락될 수 있음. `download_validation.validate_parquet_coverage`의 `FACTOR_MISSING_LATEST` 경고로 감지하지만 자동 수정은 없음.

#### 4.3.6a 연도 경계 증분 다운로드
`end_date=2027-01-31` 증분 다운로드 시 `affected_year=2027`이므로 `data/{benchmark}/factor_2027.parquet` 파일이 자동 생성된다. 기존 2026 파일은 변경되지 않음.

#### 4.3.7 M_RETURN merge 시 행 손실
`inner join`이므로 M_RETURN에 없는 종목-날짜는 삭제됨. 이는 의도된 동작이지만, M_RETURN parquet에 데이터 누락이 있으면 분석 대상 종목이 줄어듦.

#### 4.3.8 ticker 6자리 제로패딩
```python
df["ticker"] = df["ticker"].astype(str).str.zfill(6).add(" CH Equity")
```
Bloomberg 형식으로 변환. 원본 ticker가 6자리를 초과하면 잘리지 않고 그대로 사용됨 (현재 중국 주식은 6자리이므로 문제 없음).

### 4.4 기술 부채 / 주의 사항

#### 4.4.1 construct_long_short_df의 시작일 (파라미터화 완료)
```python
def construct_long_short_df(labeled_data_df, backtest_start="2017-12-31"):
```
`weight_construction.py` — 시작일이 `backtest_start` 파라미터로 전달됨. `PIPELINE_PARAMS["backtest_start"]`에서 중앙 관리되며, `aggregate_factor_returns()`를 통해 전달.

#### 4.4.2 SQL injection 완화
`factor_query.py` — universe 테이블명이 f-string 삽입. `config.UNIVERSES` 에서 파생한 `ALLOWED_UNIVERSES` allowlist 로 `_build_engine()` 에서 방어.

### 4.5 테스트 커버리지 현황

`tests/test_unit/` 28개 파일 · 335 케이스 (2026-09-16, `python -m pytest tests/test_unit/ -v`, 약 10초).
계약 테스트로 고정한 것: 결정적 출력(`test_determinism`), IS/OOS 경계(`test_oos_purity`),
선정 단일 진입점·CLI parity(`test_select_factors`), 섹터 숏캡 water-filling(`test_bm_short_cap`, `test_stock_level`),
ERC 솔버 RC 균등(`test_optimize_constrained_weights`). 통합 테스트(`tests/test_integration/`)는 실데이터·DB 의존이라
로컬 전용. 미커버: `download_factors`(DB 의존), `report_generator`(PDF 렌더).

### 4.6 성능 특성

| 단계 | 시간 복잡도 | 실측 (200+ 팩터, ~70개월) |
|------|------------|--------------------------|
| 데이터 로딩 | O(N) | ~2-5초 (parquet 로드) |
| 5분위 분석 (batch) | O(F × N/F × log(N/F)) | ~10-30초 |
| 섹터 필터링 | O(F × N/F) | ~5초 |
| 수익률 집계 | O(F × T × S) | ~30-60초 (가장 느림) |
| 가중치 계산 (EW) | O(K × styles) | <1초 |
| 가중치 산출 + CSV | O(factors × rows) | ~2초 |

총 실행 시간: ~1-3분 (200+ 팩터, 70개월 데이터 기준)

---

## 부록: 주요 수식

### CAGR (연환산 수익률)
```
CAGR = (cumulative_return)^(12/months) - 1
# months = len(ret_df) - 1  (첫 행은 기준점 0이므로 제외)
# evaluate_universe, optimize_constrained_weights 모두 동일 기준 적용
```

### MDD (최대 낙폭)
```
MDD = min(cumulative / running_max - 1)
```

### 팩터 랭킹 (meta_data.csv)
```
rank_score  = compute_rank_score(monthly_rets, factor_ranking_method)  # 기본 tstat
rank_total  = rank_score 내림차순 순위
rank_style  = 스타일 내 rank_score 내림차순 순위
# (구 Monte Carlo 시절의 rank_CAGR x 0.6 + rank_MDD x 0.4 복합 랭크는 폐기됨)
```

### 거래비용
```
trading_cost = (cost_bps / 10000) × turnover
turnover = sum |new_weight - drifted_weight|   (편입/편출 포함: 미보유 = 0 으로 간주)
```

### 스타일 캡 재분배 (`optimization._equal_weight_allocation`)
```
repeat (최대 10회):
    for 각 스타일 s:
        if style_weight(s) > cap:  w[s 소속 팩터] *= cap / style_weight(s)   # 위반 스타일 비례 축소
    w /= w.sum()                                                             # 전체 재정규화 (미달 스타일로 자연 재분배)
    if 전 스타일 <= cap: break
# 10회 내 미수렴 시 위반 스타일 경고 로그만 남기고 진행
# n_styles x cap < 100% 면 제약 자체가 infeasible -> 경고 후 위반 상태로 진행
```

---

## 6. Walk-Forward 백테스트 레이어

### 6.1 설계 원칙

기존 파이프라인([1]~[7])의 내부 코드를 **한 줄도 수정하지 않고**, 외부에서 감싸는(wrapper) 방식으로 구현한다.

- **Factor-Level Backtest**: 종목(stock-level) MP까지 내려가지 않고, 팩터 수익률(net-of-cost) × 팩터 가중치로 포트폴리오 수익률을 산출
- **거래비용 (중요)**: `calculate_vectorized_return()`이 팩터 내부 매매(연속 보유 종목 비중 변화 + 편입 매수/편출 매도, 2026-07 수정)를 전액 차감한다. 팩터 간 비중변경(inter-factor) 매매는 '팩터수익 × 비중' 구조상 미계상(과소 방향), 반대로 실거래는 MP 합산 후 1회 매매라 교차 팩터 netting 을 무시하는 팩터별 전액 계상은 과대 방향 — MP-level 실측 netting ratio 근거로 `backtest_cost_multiplier` 기본 **0.6** (10bp×0.6=6bp — 선정 입력용 근사; 구 2.0 은 편입/편출 누락 보정치, 1.0 은 netting 무시 과대). 적용 지점: `walk_forward_engine._resolve_backtest_cost_bps()` → `run()`의 `pp`. **mp(운영)는 multiplier 영향 없음**(10bp 전액; 단 턴오버 수정은 운영 factor return/랭킹에도 적용됨)
- **MP-level 실비용 (결정판)**: `research/mp_level_cost_backtest.py`가 종목단 MP 비중을 합산해 실제 매매 턴오버(연 ~2.8x one-way)에 종목비용(transaction_cost_bps)을 부과한 수치를 제공 — **netting ratio 0.574** (실비용 = 팩터별 전액 계상의 ~57%; scale-invariant, bp 수준 무관). 상세: [docs/experiments/mp_level_cost_20260703.md](docs/experiments/mp_level_cost_20260703.md)
- **가중 모드**: 백테스트는 config `optimization_mode`(기본 erc)를 그대로 사용 (`weight_kwargs_from()` 단일 출처). hardcoded 지정 시에만 equal_weight 로 자동 변환

### 6.2 계층적 리밸런싱 (Tiered Rebalancing)

```
Tier 1 (6개월마다): 규칙 학습 + IS 규칙을 전체 데이터에 적용
  - IS 데이터로 [2]~[3] 수행 -> rule_bundle 생성 (dropped_sectors, label_rules)
  - 전체 데이터에 [2] 5분위 랭킹 수행 (횡단면, 시계열 오염 없음)
  - IS에서 학습한 규칙(섹터 제거, L/N/S 라벨)을 전체 데이터에 직접 매핑 (재학습 아님!)
  - aggregate_factor_returns 1회 실행
  - 산출물: precomputed_ret_df (전기간 x 유효 팩터 수익률 행렬)

Tier 2 (1개월마다 — MXWO 월간 채택; MXCN1A는 3개월): 팩터 선정 + 가중치 재분배
  - precomputed_ret_df에서 IS 구간만 슬라이스 (aggregate 재실행 불필요; 롤링 48M)
  - rank_score(t-stat) -> Top-50 선정 (dedup off) -> [6] ERC+틸트 실행
  - 산출물: cached_weights, cached_meta

Tier 3 (매월): OOS 수익률 조회
  - precomputed_ret_df.loc[oos_date, selected_factors] (밀리초)
  - portfolio_return = sum(weight[f] x oos_factor_return[f])
```

**OOS look-ahead bias 방지 (핵심):**

`_apply_rules_and_aggregate()`에서 `filter_and_label_factors()`를 전체 데이터로 재실행하면 섹터 제거와 L/N/S 라벨이 OOS 수익률에 오염된다. 반드시 `rule_bundle`의 IS 전용 규칙을 직접 적용해야 한다.

| 항목 | 안전 (횡단면) | 오염 위험 (시간 평균) |
|------|-------------|---------------------|
| 5분위 랭킹 (rank within sector-date) | O | - |
| 섹터 제거 (Q1-Q5 스프레드 기반) | - | O -> rule_bundle["dropped_sectors"] 사용 |
| L/N/S 라벨 (분위별 평균 수익률 기반) | - | O -> rule_bundle["label_rules"] 사용 |

### 6.2.1 성능 최적화 (2026-07-01): 38.8분 -> 15.6분 (60%, 출력 byte-identical)

세 가지 무손실 최적화로 전체 walk-forward 실행시간을 60% 단축했다. 모두 **출력 CSV byte-identical**(md5 검증)이며, 검증 방식은 [[backtest-cli-dates-ignored]] 참고(백테스트는 항상 전체 기간을 돌므로 축소범위 불가; 유닛테스트 + before/after md5 로 검증).

1. **전체 데이터 5분위 통계 1회 캐시** (`run()` 진입부): `_apply_rules_and_aggregate()`가 Tier 1(약 27회)마다 **동일한 `raw_data`로 재계산**하던 `_prepare_metadata` + `calculate_factor_stats_batch`를 루프 밖에서 1회만 계산해 재사용. 분위 랭킹은 횡단면(날짜·섹터 내)이라 윈도우 불변이므로 결과 동일. (-42%)
2. **`aggregate_factor_returns` 병렬화** (`factor_returns.py`): 팩터별 독립 루프(`_compute_factor_net_return`)를 joblib(loky)로 코어 분산. joblib이 제출 순서대로 반환하므로 concat 컬럼 순서 보존 -> byte-identical. `n_jobs` 파라미터(기본 -1), 팩터 `_PARALLEL_MIN_FACTORS`(8) 이하면 직렬. mp/report 경로도 공유해 함께 빨라짐. (-11%p)
3. **IS merge 캐시** (`_run_rule_learning(prepared=...)`): Tier 1마다 `_prepare_metadata(is_raw)`로 재-merge하던 것을, 캐시된 전체 merged 를 날짜 슬라이스(`merged_full[ddt<=cutoff]`)해 전달. inner merge 키에 `ddt`가 있어 슬라이스 == 재-merge -> byte-identical. (-7%p)

**남은 병목(무손실로는 추가 단축 어려움):** IS rank/cut(윈도우별 필수), Tier 2 가중치 최적화/레코드 조립. 클러스터링(corr+linkage)은 호출당 ~1-2초로 병목 아님. 검토했으나 기각: rule-application 루프 병렬화(순이득 0 — 루프가 병목 아니었고 full fdf pickle 비용이 상쇄), Tier2 클러스터링 근사 캐시(<1분 이득 + 결과 변경이라 byte-identical 위배).

### 6.2.2 Tier 1 계산 구간 절단 + zero-filter 기준점 제외 (2026-09-16)

4. **Tier 1 horizon 절단** (`_apply_rules_and_aggregate(horizon=...)`): 각 Tier 1 은 OOS `i .. i+factor_rebal_months-1` 만 소비하는데 구 코드는 매번 parquet **전 기간**의 팩터 L/S 수익률을 계산했다. 라벨 프레임을 `ddt <= all_dates[min_is + i + factor_rebal_months]` (턴오버 lookahead `w.shift(-1)` 분 +1) 로 잘라 미래 구간 계산을 생략한다. 이것은 성능만의 변화가 아니다 — `aggregate_factor_returns` 가 **어느 한 달이라도 NaN 인 팩터 열을 통째로 버리므로**(`dropna(axis=1)`), 구 코드는 "미래 월에 롱 또는 숏 분위가 비는 팩터"를 현재 시점 선정에서 제외하는 look-ahead 를 갖고 있었다. 절단 후에는 그 시점까지의 데이터로만 판단한다. 마지막 Tier 1 의 horizon 은 데이터 끝이라 `factor_returns_matrix_*.csv`(viz 용) 는 전 기간 그대로다.
   byte-identical 이 아닌 이유 두 가지: (a) 위 look-ahead 제거 (선정 집합이 달라지는 달이 있을 수 있음), (b) numpy pairwise 합산의 ULP 차이(배열 길이가 바뀌면 덧셈 묶음이 달라져 1e-16 수준). 회귀 검증은 byte-diff 대신 허용오차 비교(`scratchpad/compare_ab.py`, 1e-12) 로 한다.
5. **zero-filter 기준점 제외** — §4.2.7.

A/B 실측 (실데이터, 2026-08-31 기준일, 허용오차 비교):

| 구성 | MXWO factor-level CEW Sharpe | MXWO 정본(stock-level) Sharpe / MDD / Calmar | 선정 집합 변경 월 |
|---|---|---|---|
| 구 코드 | 0.862 | 0.706 / -1.80% / 0.401 | - |
| zero-filter 교정만 (horizon 절단 off, 격리 실행) | 0.862 (동일) | 동일 | 0 / 99 |
| 교정 + horizon 절단 (현행) | 0.826 | 0.661 / -1.99% / 0.342 | 23 / 99 |

- **zero-filter 교정은 MXWO 에서 효과 0** (경계 팩터 없음). 차이는 전부 horizon 절단 = look-ahead 제거분이다. 구 코드에서 전 기간 제외되던 IO_NAT 가 새로 선정 후보에 들어오고, 23개월의 선정 집합이 바뀐다. **구 정본 0.706 은 "미래에 롱/숏이 비는 팩터" 를 미리 빼는 look-ahead 로 소폭 부풀려져 있었다** — 현행이 정직한 수치이며 **채택 유지** (look-ahead 가 있는 구 수치로 되돌릴 이유 없음). ULP 수준 차이(1e-12 이하)는 관측되지 않았다 (모든 차이가 선정 변화에서 옴).
- **새 정본 (배포 기준, 2018-06~2026-08 99개월)**: MXWO Sharpe 0.661 / IR 0.657 / CAGR +0.68% / MDD -1.99% / Calmar 0.342 / TE 1.03% / 턴오버 1.6x. MXCN1A (2012-12~2026-08 165개월, 롱숏 ±7%) Sharpe 0.730 / MDD -0.76% / Calmar 0.488 / TE 0.51%.
- MXCN1A: 정본 Sharpe 0.729 -> 0.730 (중립). expanding IS + winner_median + 히스테리시스가 경로 의존적이라 165/165 개월의 선정 집합이 달라지지만 성과는 같다 (팩터 교체: AdjEPSNumRevFY1C·Alpha12M6MPChg 퇴장 / 8WChgFwd12MEPS·AssetTurn·EPSSurpC·PAdjChg1YCF·RONA 진입).
- 실행 시간: MXWO 백테스트 15.4분 (구 방식 격리 실행 16.0분, 동일 머신) — 절감은 약 1분으로 예상(30%)보다 작다. 병목이 aggregate 가 아니라 IS rank/cut 과 Tier 2 라는 §6.2.1 진단과 일치.
- mp(운용) 산출물은 horizon 개념이 없어 영향 없음 — 2026-08-31 4종 + history byte-identical.

### 6.3 과적합 위험 지점

| 단계 | 과적합 위험 | 이유 |
|------|------------|------|
| [4] 팩터 선정 (Top-50) | **높음** | 롤링 IS 48M t-stat 순위 Top-50 (dedup off). 평균 회귀 위험 |
| [6] 가중치 계산 (ERC) | **중간** | cov 추정 기반 (수축 0.2로 노이즈 완화, 학습 파라미터 없음) |
| [3] 섹터 제거 + L/N/S 라벨 | **낮음 (수정 완료)** | IS 전용 rule_bundle 적용으로 OOS 오염 제거됨 |
| [2] 5분위 분석 | 낮음 | 횡단면 정렬이라 시계열 과적합 아님 |

### 6.4 과적합 진단 3단계 테스트

파이프라인의 축소 funnel(200+ 유효 팩터 → 선정 팩터)이 진짜 가치를 창출했는지 해부한다.

> **EW_Top50 곡선 정의 (2026-07-05 복원)**: `ew_top50_return` = 클러스터 dedup/히스테리시스
> **이전**의 순수 rank_score 상위 `top_factor_count`(50) 동일가중. 커밋 `8dfb64e`(최적화 제거)
> 이후 한동안 선정 집합이 그대로 들어가 `ew_return`(선정 EW)과 동일 곡선이 중복 기록됐었고
> (equal_weight 에선 선정 전원이 weight>0), 2026-07-05 에 pre-dedup Top-N 으로 복원했다.
> 복원 후 funnel 은 3단계 분해: A→B = t-stat 랭킹 가치, B→선정EW(`ew_return`) =
> dedup+히스테리시스 가치, 선정EW→C = style_cap 효과.
> 이때 funnel 패턴 라벨이 CONSTRAINT_DRAG → NORMAL 로 바뀐 것은 B 재정의 때문(전략 무변경).
> 상세: [proposal_experiments_20260705.md](docs/experiments/proposal_experiments_20260705.md)

**1순위: Funnel Value-Add Test (구간별 가치 창출 검증)**

OOS 구간에서 3개 포트폴리오의 성과(CAGR, MDD)를 동시 비교:
- A. EW_All: 전체 유효 팩터 동일가중 (시장/팩터 베타)
- B. EW_Top50: Top-50 후보군 동일가중 (1차 필터링 실력)
- C. Constrained EW (= 본전략 MP): 선정 팩터에 config 가중(ERC + TS 틸트) + style_cap(25%) 재분배. 라벨 "EW" 는 1/N 시절의 관례

| 패턴 | 의미 |
|------|------|
| C > B > A | 정상 -- 필터링과 style_cap 제약 모두 가치 창출 |
| B > C > A | CONSTRAINT_DRAG -- style_cap 제약이 OOS CAGR을 깎음. 과적합 아님(학습 가중치 없는 결정론적 제약): 수익 일부를 내주고 변동성/MDD를 낮추는 트레이드오프. 구 라벨 `OPTIMIZATION_OVERFIT` |
| A > B | FILTER_OVERFIT -- rank_score 기반 Top-50 선정 자체가 과거 우연 |

> C 의 가중은 IS cov 기반 ERC(학습 파라미터 없음, 대각 수축 0.2) + 결정론적 style_cap 재분배다
> (종목단 공분산/MC 최적화는 커밋 `8dfb64e`에서 제거됨).

**2순위: OOS Percentile Tracking (최종 팩터 생존율)**

각 Tier 2 구간에서 weight>0 팩터들의 OOS 실현 수익률 백분위를 계산.
- 상위 40% 이내 → 견고한 팩터 선정
- 40~60% → 보통 (랜덤과 차이 미미)
- 60% 이상 → 과적합 의심 (IS 상위 팩터가 OOS에서 추락)

**3순위: Strict Jaccard Index (weight>0 팩터 안정성)**

Top-50이 아닌, **실제로 비중이 할당된 최종 팩터**에만 적용.
집합 크기가 작아 Jaccard가 예민하게 반응 → 기준값을 Top-50 Jaccard보다 낮게 설정:
- \> 0.5 → 안정적
- 0.3~0.5 → 보통
- < 0.3 → 불안정 (과적합 의심)

**보조 지표:**
4. IS-OOS Rank Correlation: IS 선정 점수(rank_score=t-stat) 순위와 OOS 실현 수익률 순위의 Spearman 상관
5. Deflation Ratio: OOS CAGR / IS CAGR. OOS 기간이 짧으면 단독 판단 금지

### 6.5 방어 로직

- **MIN_REQUIRED_FACTORS = 5**: 유효 팩터가 5개 미만이면 Tier 2 스킵, 이전 가중치 유지
- **배포 = 목표 비중 (스무딩 제거)**: 매 회차 optimizer 목표 비중(ERC/틸트 가중 + style_cap, 합 1.0)을 그대로 배포한다 (`deploy_step=1.0`; <1 이면 `blend_deploy_weights` 부분 조정). factor 키를 정렬한 뒤 합 1.0 으로 재정규화(결정적·byte 안정), 탈락 factor 는 즉시 제거. production `mp`·백테스트 동일. (과거 turnover 스무딩(절대스텝 밴드형)은 월간 turnover 를 ~32% 줄였으나 무비용 가정 OOS 에서 Sharpe 개선이 없어 **제거**됨 — 실험 기록: [`smoothing_cost_experiment_20260612.md`](docs/experiments/smoothing_cost_experiment_20260612.md), [`absolute_step_attribution_20260607.md`](docs/experiments/absolute_step_attribution_20260607.md). turnover 절감은 아래 선정 히스테리시스로 달성.)
- **선정 히스테리시스 (production 적용, MXWO `selection_hysteresis=0.25`; MXCN1A는 0.5)**: 챌린저가 직전 보유 factor 를 rank_score 격차 margin 이상 이겨야 교체 (`factor.selection.select_factors` 단일 진입점 — 절단/dedup/히스테리시스를 mp·백테스트가 같은 함수로 수행, 2026-09-16 통합; 집합이 실제로 바뀐 경우에만 반영해 바뀌지 않으면 원래 순서 유지). 팩터단 전환비용 6-way 비교에서 히스테리시스는 **턴오버 -64% + gross CAGR +0.6~0.7%p 동시 개선** — [`smoothing_cost_experiment_20260612.md`](docs/experiments/smoothing_cost_experiment_20260612.md). `evaluate_universe` 가 `weight_history.load_prev_selection()` (직전 `factor_styles_*.csv` 의 raw_weight>0 = 직전 선정 집합) 으로 incumbents 를 로딩해 동일 함수 적용. test 모드는 prod history 오염 방지 skip. backtest CLI 는 `--selection-hysteresis` (미지정 시 config 값). 엔진 Tier 1 이 선정 incumbency 를 carry (기존 6개월 리셋 -> production 불일치 해소, Tier 2 는 규칙 갱신 시 강제 재실행).

### 6.5.1 mp 가중치 history (`output/{BENCHMARK}/mp_weight_history/`)

`mp` 명령 실행 시 회차별 factor 가중치와 style 요약을 저장한다. `service/pipeline/weight_history.py` 의 저장 함수 3 종 + 공유 헬퍼 1 종이 담당.

| 파일 | 함수 | 저장 조건 | 역할 |
|------|------|----------|------|
| `factor_weights_{date}.csv` | `save_factor_weights` | `not test_file` | factor / weight 2 컬럼 = **배포 가중치**. 다음 회차 prev 입력용 (`load_prev_factor_weights` 가 strict `< current_end_date` 비교로 직전 최근 파일 + 날짜 튜플 반환). |
| `factor_styles_{date}.csv` | `save_factor_styles` | `not test_file` | factor × style + raw(목표)/prev/new(=배포) + weight_within_style. factor union 기준. style 매핑 실패 시 "(unmapped)". |
| `style_totals_{date}.csv` | `save_style_totals` | `not test_file` | style 단위 raw/prev/new 합계 + delta + factor_count + factors (`;` 구분 문자열). |

**raw / prev / new(=배포) 의미** (스무딩 제거, 메모리 구분 없음)
- `raw` : 이번 회차 optimizer 산출 = **목표** (ERC/틸트 가중 + style_cap, 합 1.0)
- `prev`: 직전 회차 **배포** 가중치 (`factor_weights_{prev_date}.csv` 로딩)
- `new` (= 배포): `raw` 를 factor 키 정렬 후 합 1.0 으로 재정규화한 값 = **목표 그대로 배포**(스무딩 제거). `factor_weights_{date}.csv` 로 저장돼 다음 회차 prev(전월대비 delta 리포트용). 탈락 factor 는 배포에서 즉시 제거. Bloomberg 입력은 이 `new`.

**공유 헬퍼**: `_build_factor_style_df` 가 factor union, style 매핑, weight_within_style 정규화 (스타일 합 0 이면 0) 의 공통 로직을 담당. `save_factor_styles` 와 `save_style_totals` 가 모두 이 헬퍼를 사용. (`deployed_weights` optional 인자는 함수에 남아있으나 `new`=배포(=raw 정규화)라 미사용.)

**style_map 출처**: `model_portfolio.py` 가 `data/factor_info.csv` 전체 (587 factor) 를 사용해 dict 구성. `self.meta` (38 kept factor) 를 안 쓰는 이유는 prev 에만 있는 factor (이번 회차 탈락) 도 매핑해야 하기 때문.

**test_file 모드 정책**: `python main.py mp test tests/fixtures/test_data.csv` 실행 시 history 디렉토리에 어떤 파일도 저장하지 않음 (test 데이터로 prev history 오염 방지).

### 6.6 CLI 커맨드

```
python main.py backtest <start> <end> [옵션]
  --min-is-months        최소 IS 기간 (기본: 36)
  --factor-rebal-months  Tier 1 리밸런싱 주기 (기본: 6)
  --weight-rebal-months  Tier 2 리밸런싱 주기 (기본: 3)
  --top-factors          상위 팩터 수 (미지정 시 config top_factor_count; dedup off 또는 topn 에서 유효)
  --selection-hysteresis 선정 히스테리시스 margin (미지정 시 config 값)
  --cluster-method       topn / winner_median (미지정 시 config 값)
  --style-cap            스타일 합계 상한 (미지정 시 config 0.25; 1.0=캡 해제)
  --is-window-months     롤링 IS 윈도우 개월 (미지정 시 config is_window_months; 0=expanding)
```

### 6.7 실제 실행 결과

최신 결과는 `backtest` 산출물(`output/{BENCHMARK}/<date>/dashboard_<date>.html`, `overfit_diagnostics_<date>.csv`, `stock_level_series_<date>.csv`)로 본다.
구 정적 보고서(MXCN1A, 2026-03 기준)는 [`docs/archive/backtest_results_2009_2026.md`](docs/archive/backtest_results_2009_2026.md).

**현재 기본 설정 (config.py):**
- `optimization_mode = "erc"` (cov 48M + 대각수축 0.2 + Spinu CCD; 수축 0.2는 2026-08-07 전구간 스윕 채택 — mxwo_sharpe_ladder_20260729.md 7차)
- `ts_mom_window = 3`, `ts_mom_scale = 0.2` (TS 모멘텀 틸트; 창 4→3 2026-08-07, 감쇠 0.5→0.2 2026-08-10 전구간 스윕 — mxwo_sharpe_ladder_20260729.md 8차)
- **배포 기준 성과 회계** (2026-08-19): `backtest` 가 종목단 재구성을 함께 수행해 `stock_level_series_{기준일}.csv` 산출 (월별 book_gross_before / multiplier / long·short_exposure / gross·cost·tax·net_return / turnover). 구현은 `service/backtest/stock_level.py` — `stock_weights_at`(구 mp_level 전용에서 공용화) + `build_stock_series` + `series_metrics`. 엔진은 `_apply_rules_and_aggregate(out_frames=...)` 로 라벨 프레임을 받아 **프레임이 살아있는 루프 안에서** 월별 종목 순비중만 뽑아 축적한다 (프레임 자체는 무거워 미보관). 통합 덕에 mp_level 별도 실행(20분, 워크포워드 중복)이 불필요 — 백테스트 15.6분 -> 약 21분.
  - **목표 노출 고정**: `mp_target_gross` 설정 시 매월 `배수=target/gross`. 실측 book gross 는 **70.3%~111.5%** 로 넓게 변동(2시점 스냅샷만 보면 85~90%로 좁아 보이나 전 구간은 다름) -> 고정 배수는 노출·TE 를 그만큼 흔든다. 적용 배수 범위 0.359~0.569 (중앙 0.448).
  - ⚠ **월별 목표 고정은 상수배가 아니다**: netting 이 심한 달을 키우고 덜한 달을 줄이므로 수익 경로가 바뀐다 -> Sharpe 도 이동(0.739 -> 0.734). 고정 배수였다면 불변. 테스트로 이 구분을 고정(`test_stock_level.py`).
  - **TE**: 시장중립 오버레이 -> 액티브수익=오버레이수익 -> `TE = 월 net_return std x sqrt(12)`. 벤치마크 비중 데이터 불필요 (파케이에 `MXWO_WGT` 없음). 실현(ex-post) 기준.
- **MP 배포 배수** (2026-08-19): 최종 MP 북(=AGG/style"MP" 행, 종목 netting 후)은 롱/숏이 정확히 대칭(순노출 ~1e-15)이나 절대 gross 가 시점마다 다르다 — 팩터 비중 합 1.0 이 롱·숏 양쪽에 각각 전개돼 netting 전 gross 2.0, 종목 상쇄로 56.9% 흡수되어 0.862(2026-06)/0.898(2025-06) 잔존. `mp_target_gross`(기본 0.40) 설정 시 `multiplier_for_target()` 이 `target/gross` 배수를 산출해 노출을 고정한다 (롱 +20%/숏 -20%). 적용 대상은 `mp_ls_weight`/`ls_weight`/`style_ls_weight` 뿐 — `factor_weight` 는 피벗 컬럼 키이자 팩터 배분(합=1)이라 스케일 금지. style 집계·피벗 **이전**에 적용해 세 산출물이 동일 배수를 반영. 기록은 `mp_weight_history/deploy_multiplier_*.csv`. 수동 모드는 `data/mp_multiplier.csv`(effective_date, multiplier — 다음 변경 전까지 유효한 계단식). 배포 스케일링이므로 walk-forward/mp_level 성과 회계에는 영향 없음
- **산출물 파일명 기준일** (2026-08-19): 모든 생성물 이름에 데이터 기준일이 붙는다 — `walk_forward_results_{YYYY-MM-DD}.csv`, `walk_forward_weight_history_*`, `overfit_diagnostics_*`, `factor_returns_matrix_*`, `meta_data_*`, `별첨0N_{BM}_{Name}_*`. 기준일 출처는 CLI 인자가 아니라 **실제 산출 데이터의 마지막 월** (백테스트 CLI 날짜는 엔진이 무시하므로). 헬퍼는 `service/paths.py`: 쓸 때 `dated(path, as_of)`, 읽을 때 `latest(path)` (가장 최근 기준일본 선택, 없으면 구 무날짜 경로 폴백). **2026-09-09 기준일 폴더**: `dated()` 는 `output/{BM}/{YYYY-MM-DD}/{stem}_{date}{ext}` 로 쓰고(폴더 생성 포함), `latest()` 는 기준일 폴더만 글롭한다; mp test 산출물은 `output/{BM}/test/`, 회차 간 이력은 `mp_weight_history/`(불변), 실험은 `experiments/`(불변). `latest()` 글롭은 날짜 패턴으로 한정 — `meta_data_test_test_data.csv` 같은 동일 stem 파생 파일 혼입 방지. 별첨 커버 우하단도 생성일이 아니라 기준일(`AS OF 30 JUN 2026`) 표기. 부수 교정: mp_level parity 기본 비교 경로가 루트 `output/` 고정이던 오류를 `OUTPUT_DIR` 최신본으로 수정 (MXWO 오표기 해소)
- **성과 원천(팩터 기여도) 상설화** (2026-08-28): `_assemble_oos_record` 가 매 OOS 월 `contributions = {f: 비중 x 당월수익}` (합 = oos_return) 을 기록하고, stitcher 가 `contribution_history` 로 직렬화 → `factor_contrib_{기준일}.csv` (weight_history 와 동일 결정적 포맷). 대시보드 '연도별 성과 원천' 섹션: 연도x스타일 히트맵(`contrib_style_heatmap_fig`) + 연도별 상/하위 기여 팩터 표. **핵심 함정: 사후 재구성 금지** — 저장된 `factor_returns_matrix`(최종 윈도우 규칙) x 비중이력 근사는 look-ahead 로 왜곡됨 (corr 0.676, 2025년 기여 2배 과대 실측). 반드시 엔진이 당시 규칙으로 기록한 값 사용. 팩터단(배포 배수 전) 기준.
- **별첨 북 = 운용 규칙 기준** (2026-08-27): `mp --report` 가 48M 롤링 IS 창으로 운용 규칙(섹터 제거·L/S 라벨)을 별도 fit 해 `live_rules` 번들로 `generate_report()` 에 전달하고, 북은 그 규칙을 **전체 이력 5분위 통계에 직접 매핑**해 차트를 그린다 (재학습 금지 — walk-forward `_apply_rules_and_aggregate` 와 동일 원칙). 결과: 별첨 팩터 집합 = 운용 유효 팩터 집합 (구 방식은 전체 이력으로 재-fit 한 통과 집합 ∩ meta_data 교집합만 실려, LogMktCap 등 운용 유효 팩터 3개가 북에서 빠지는 불일치 존재). 차트의 수익 측정 기간만 전체 이력(참고용)이고 규칙·정렬(③ L-S CAGR)은 모두 운용 48M. 별첨01 Factor_Info 시트도 북과 동일 집합으로 필터 — **별첨 4종 = 271개·동일 순서 통일** (운용 273 중 stdRR36M·PM1M 은 전 기간 커버리지<10% 로 별첨 전체 미수록). `live_rules=None` 이면 구 방식 폴백. 부수 교정: 구 방식은 `filter_and_label_factors` 를 기본 인자(threshold 0.10, tstat None)로 호출해 운용 파라미터(0.05 등)와도 어긋났었음
- `COUNTRY_TAX_BPS` (국가별 증권거래세, 2026-08-12 도입): 수수료 10bp 와 **별도**로 법정 거래세를 매수/매도 방향별 부과. GBR 50/0, IRL 100/0, FRA 40/0, ESP 20/0, ITA 20/0, HKG 10/10, ZAF 25/0, USA 0/0.206 (bp), 그 외 면세. 적용 지점은 `mp_level_cost_backtest` **실측 전용** — factor-level 선정 입력에는 미반영 (선정 규칙 불변, 성과 회계만 정직화). 구현: `service/pipeline/transaction_tax.py`. 핵심은 턴오버의 **부호 있는 델타 보존** (Δw>0=매수/숏커버, Δw<0=매도/숏진입) — 영국 SDRT 같은 매수 편측 세목이 공매도 진입엔 안 붙고 커버에만 붙는 구조가 자동 처리됨. `cost_stock`(수수료)/`tax_stock`(세금) 분리 계상으로 netting ratio 진단 유지. 영향: net 0.840→0.739 (연 세금 23.5bp, 편도 6.19bp)
- `factor_ranking_method = "tstat"` (기본; `"shrunk_tstat"` / `"cagr"` 는 실험 옵션)
- `use_cluster_dedup = False` (**MXWO: dedup off + 순수 Top-50** — 2026-07-28 A/B; MXCN1A는 True/winner_median)
- `is_window_months = 48` (롤링 IS; MXCN1A는 expanding)
- `selection_hysteresis = 0.25` / `weight_rebal_months = 1` / `transaction_cost_bps = 10.0`
- `min_coverage_pct = 0.10` / `sector_short_cap = 0.15` / `spread_threshold_pct = 0.05`
- 데이터 시작 2015-06-30 (MXWO parquet 커버리지)

**선정 유틸리티 (`service/factor/selection.py`; 클러스터 dedup 은 MXCN1A production 적용, shrunk_tstat / Newey-West 는 실험·진단용):**
- **`shrunk_tstat`** — `compute_shrunk_tstat()`
  James-Stein 계열 shrinkage로 팩터 t-stat을 스타일 그룹 평균 쪽으로 `lambda` 만큼 축소.
  `lambda = var_within_style / (var_within_style + var_between_styles)` — 데이터 주도 결정.
- **1-B `use_cluster_dedup`** — IS 구간 팩터 L-S 수익률 상관으로 `1 - |corr|` distance,
  `scipy.cluster.hierarchy.linkage(method="average")`, `fcluster(maxclust=n_clusters)`. IS 전용(OOS look-ahead 방지).
  `cluster_method`로 압축 규칙 선택:
  - ⚠ **MXWO 는 dedup 자체가 off** (`use_cluster_dedup=False`) — 아래는 MXCN1A 기본 및 옵션 설명.
  - **`winner_median`(MXCN1A production 기본) — `cluster_winner_median_dedup()`**: 클러스터당 rank_score 상위 `per_cluster_keep` 후보 중 **클러스터 1등 무조건 통과**(분산 보장, 최소 n_clusters개) + 나머지는 **전역 rank_score 중위값 이상**만 통과. 고정 Top-N 없음(~20~40개 가변). A/B 백테스트: topn 대비 OOS Sharpe 0.73→0.79, Calmar 0.43→0.57, MDD -5.1→-4.0%, 보유 ~41→~26개. ([2026-06-30 A/B](#))
  - `topn` — `cluster_and_dedup_top_n()`: 클러스터별 상위 `per_cluster_keep` 통과 후 rank_score 상위 `top_factor_count`(50) 절단.
- **1-C Newey-West 진단** — `compute_newey_west_tstat()`
  Bartlett kernel, lag=3 기본. `meta_data.csv`의 `newey_west_tstat` 컬럼으로만 노출, 랭킹 교체 X.

**산출 파일:**
- `output/{BENCHMARK}/<date>/walk_forward_results_<date>.csv` -- OOS 월별 Constrained EW / EW / EW_All / EW_Top50 수익률 + 누적 수익률 (컬럼 prefix `cew_*`)
- `output/{BENCHMARK}/<date>/overfit_diagnostics_<date>.csv` -- 과적합 진단 5개 지표 요약
- `output/{BENCHMARK}/<date>/walk_forward_weight_history_<date>.csv` -- 월별 팩터 가중치 이력 (date x factor; viz 비중 추이/회전율용)
- `docs/archive/backtest_results_2009_2026.md` -- 구 136개월 OOS 분석 보고서 (MXCN1A, 2026-03 기준 정적)

**재현성 (결정적 출력, 2026-06-24):** 위 두 CSV(`walk_forward_results.csv`,
`walk_forward_weight_history.csv`)는 동일 코드라면 실행마다 byte-identical 하다.
과거에는 선정/배포 경로가 `set` 반복에 의존해 `PYTHONHASHSEED`(프로세스별 문자열
해시 랜덤화)에 따라 ① 가중치 컬럼 순서, ② 합산 순서 차이로 인한 수익률 float 말단자릿수,
③ 점수 동점 경계의 선정 팩터 집합이 흔들렸다. 다음 4곳을 결정화해 제거:
- 배포 가중치 키 정렬 (`model_portfolio.py`·`walk_forward_engine.py`: `sorted(...)` 후 합 1.0
  재정규화). 옛 `step_smooth` 의 `sorted(union)` 이 **근본원인**이었고, 스무딩 제거 후에도 이
  정렬·재정규화를 보존해 배포 dict 키 순서/합산 순서를 고정 (출력 byte-identical 유지).
- `factor/selection.py:apply_selection_hysteresis` -- 점수 동점 시 팩터명 오름차순 타이브레이크
  (`exits`/`entries`/반환 정렬에서 `set` 반복 순서 의존 제거).
- `result_stitcher.py` -- `weight_history` 컬럼 알파벳 정렬.
- `walk_forward_engine.py` -- `available_factors = sorted(...)` 로 OOS 수익률/정규화 합산 순서 고정.

계약은 `tests/test_unit/test_determinism.py` 가 핀으로 고정한다(서로 다른 `PYTHONHASHSEED`
하위 프로세스 출력 동일성). 단일 프로세스 내에서는 해시 시드가 고정되어 비결정성이 드러나지
않으므로 반드시 별도 프로세스로 검증한다.

**2026-09-02 추가 수정:** `overfit_diagnostics.calc_oos_percentile_tracking` 이 `active_factors`(set)를 그대로 순회해 평균을 내 합산 순서가 PYTHONHASHSEED 에 따라 달라졌고, 테스트 데이터에서 평균 백분위가 정확히 0.60 경계라 해석 문구(보통/과적합 의심)가 실행마다 뒤집혔다 -> `sorted(active_set)` 로 고정 (표시값 4자리·실데이터 판정엔 영향 없음).

---

## 7. 시각화 대시보드 레이어 (viz)

백테스트와 현재 포트를 단일 인터랙티브 HTML로 보여주는 **read-only** 레이어.
기존 `output/{BENCHMARK}/<date>/*.csv`만 소비하고 파이프라인 코드/출력 스키마를 일절 수정하지 않으므로,
CLAUDE.md 의 코드 변경 검증 절차(aggregated_weights before/after diff)를 발동시키지 않는다.

### 7.1 모듈 구조 (관심사 분리)

| 파일 | 책임 |
|------|------|
| `service/report/dashboard_data.py` | CSV -> DataFrame + 파생지표(낙폭/KPI/스타일집계/상위롱숏/팩터틸트/선정셋/진단파싱). plotly 의존 없음 -> 단위 테스트 용이 |
| `service/report/dashboard_charts.py` | DataFrame -> plotly Figure. 스타일 색은 `service/report/style_colors.py` 단일 출처(STYLE_COLORS, 다크 팔레트 2026-08-25 통일)를 report_generator(PDF)와 공유. 본전략 라벨은 `<benchmark>전략` (구 CEW 표기 대체) |
| `service/report/dashboard.py` | 조립 + HTML 출력(얇음). `build_dashboard(end_date=None) -> Path`. `_diagnostics_table(output_dir, curves)`이 overfit_diagnostics.csv 를 분류별 표로 렌더(단일값 colspan, 전 셀 `html.escape`) + `_oos_rows()`로 곡선 기반 OOS 성과(EW/Top50/CEW)를 통합(§7.4) |
| `main.py` `viz` 서브커맨드 | `python main.py viz [end_date] [--open]` -> `_run_viz()`. **`_run_backtest()`도 종료 시 `build_dashboard()` 자동 호출**(try/except 격리 — viz 실패가 백테스트 산출물을 무효화하지 않음) |
| `tests/test_unit/test_dashboard_data.py` | 순수 함수 단위 + HTML 스모크 + 진단 표 피벗/escape |

### 7.2 스냅샷 파일 선택 규칙

`find_latest_weights_file()`: `weights_factor_*.csv` 중 `_style` 변형 제외,
파일명에서 `(\d{4}-\d{2}-\d{2})` 파싱 -> 최대 날짜(또는 인자 `end_date`). 동일 날짜 복수 시
mtime 최신 우선. 운영(무접미사)/`_test`/`_test_test_data` 모두 포함 — 접미사 무관.

### 7.3 KPI 일치 정책 (중요)

KPI 카드(CAGR/MDD/Sharpe/Calmar/승률/초과CAGR)는 `build_kpis()`에서 **곡선 계산값을
구한 뒤 `overfit_diagnostics.csv` 값이 있으면 그것으로 덮어쓴다.** 진단 파일이 사용자의
기존 리포트 기준값이므로 일치시켜 혼선을 막는다. 진단 파일이 없으면(test 모드) 곡선 계산값 사용.

- 곡선 계산만으로 CAGR/MDD/Sharpe/Calmar/초과CAGR는 진단과 정확히 일치(검증됨, 2026-05-31 기준).
- **승률 caveat:** `overfit_diagnostics.csv`의 "Win Rate"는 CSV 카테고리 라벨이
  "Constrained EW vs EW_Top50"이지만, 실제 계산은 `result_stitcher.compare_cew_vs_ew_oos()`에서
  `excess = oos_returns - oos_ew_returns` 즉 **CEW vs 선정 EW(`ew_*`)** 기준이다 (Top50 아님).
  `compute_kpis()`도 이를 맞춰 `cew_return > ew_return` 으로 계산한다. (라벨/계산 불일치는 기존 코드의 것)

### 7.4 출력 / 제약

- `output/{BENCHMARK}/<date>/dashboard_<date>.html`, plotly.js **인라인** 임베드 -> 오프라인 단독 열림(약 4.7MB).
- 백테스트 섹션 하단 "과적합 진단 상세" 표 = `overfit_diagnostics.csv` 전체를 순서대로 렌더(컬럼: 분류/지표/EW/Top50/CEW/해석). 단일값 행은 3열 colspan, Interpretation 의 `<`/`>`(예: 'A < B < C')는 escape. `parse_diagnostics()`는 KPI용이라 Interpretation/행순서를 버리므로 표는 CSV를 직접 읽는다.
- **OOS 성과는 곡선에서 계산해 이 표에 통합**: `_oos_rows()`/`compute_series_perf()`가 walk_forward_results.csv 곡선에서 EW_All/EW_Top50/CEW 의 CAGR/MDD/Sharpe/Calmar 를 산출해 단일 "OOS 성과 (EW/Top50/CEW)" 블록으로 삽입(CSV/백테스트 스키마 무수정; 곡선 계산 = 진단값 §7.3; CSV 엔 없는 EW_All/Top50 의 Sharpe/Calmar 까지 메움). **중복 방지**: funnel 이 이미 OOS CAGR/MDD 를 EW/Top50/CEW 로 보여주므로, 통합 시 funnel 의 EW_All/EW_Top50/Constrained EW 변형행과 CSV OOS 섹션(CEW·선정EW)은 숨기고 funnel 패턴 판정 행만 남긴다. 곡선이 없으면(test 모드) funnel 변형행을 그대로 3열 피벗하는 폴백.
- **낙폭 구간 분석**: `compute_drawdown_episodes(cum, min_depth=0.01)`(dashboard_data)가 곡선에서 underwater episode(고점->저점->회복)를 추출 — 깊이(%) + peak/trough/recovery 시점 + 하락(peak->trough)·회복(trough->recovery)·전체 기간(개월), 미회복 시 ONGOING. `_drawdown_episodes_section()`이 EW_All/EW_Top50/CEW 곡선별 표(깊은 순, 1% 이상)로 렌더. 정적 산출물 `docs/experiments/drawdown_analysis.md`(특정 설정/기간)와 달리 **현재 OOS 곡선에서 실시간 계산**이라 숫자는 다름.
- **QC 결과페이지 참고 컴포넌트(곡선 기반, read-only)**: ① `monthly_returns_table()`→`monthly_returns_heatmap_fig()` 연×월 수익률 히트맵(+연간 복리 'Year' 열, 상승=green/하락=red zmid=0), ② `extended_stats()` 확장 통계(Sortino/연변동성/최고·최저월/상승월%/왜도/최장연속손실)+`relative_metrics(bench="ew_return")` 벤치(선정 EW) 대비 Beta/Alpha(연)/추적오차/정보비율 → `_backtest_stats_card()` KPI 그리드, ③ `rolling_sharpe(window=12)`→`rolling_sharpe_fig()`. 전부 `walk_forward_results.csv` 월별 수익률만 소비 (전략/스키마 무수정).
- 한글 차트 타이틀은 plotly JSON 내에서 `\uXXXX` 이스케이프로 저장됨(렌더링 정상). raw grep 시 주의.
- Bloomberg 핸드오프 파일 `weights_pivot_*.csv`는 읽지도 수정하지도 않음.
### 7.5 섹터 분해 (read-only parquet join)

`weights_factor_*.csv`에는 `sec` 컬럼이 없다. `load_sector_map()`이 소스
`data/{benchmark}/factor_<연도>.parquet`을 `load_factor_parquet()`로 읽어(스냅샷 연도만),
해당 `ddt` 행에서 `gvkeyiid -> sec` 매핑을 만들고, `sector_net_weights()`가 종목 순비중
(`mp_ls_weight`)을 섹터로 묶는다. 파이프라인/출력 스키마는 건드리지 않는다(read-only).
parquet 없거나 날짜 불일치 시 빈 dict -> 섹터 차트 자동 생략. 매핑 없는 종목은 'Unknown' 버킷.

### 7.6 백테스트 비중 추이 / 회전율

`WalkForwardResult.weight_history`(date x factor)는 메모리에만 있었으나, `_run_backtest`에서
`result.weight_history.to_csv(output/{BENCHMARK}/walk_forward_weight_history.csv)` **한 줄로 직렬화**한다
(기존 `walk_forward_results.csv` 기록 *다음에* 별도 파일을 쓰므로 기존 출력은 불변).
viz는 이 파일을 읽어 `compute_turnover()`(one-way = 0.5*sum|dw|)로 회전율을,
`style_weight_history()`(factor->style via `factor_info.csv`)로 스타일 비중 스택 영역을 그린다.
파일이 없으면(백테스트 미실행) 두 차트는 자동 생략. **백테스트를 한 번 돌려야 생성된다.**
