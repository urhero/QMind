# 📘 엔드투엔드 팩터 파이프라인 요약 — MXCN1A / MXWO 유니버스
[[pytest](https://github.com/urhero/QMind/actions/workflows/test.yml/badge.svg)](https://github.com/urhero/QMind/actions/workflows/test.yml)

*(Code → Investment Process 매핑)*

> **QMind(구 BOK) — 하나의 코드베이스가 MXCN1A(중국 A주)와 MXWO(MSCI World) 두 유니버스를 지원합니다.**
> 원래 MXCN1A 전용(BOK)으로 만들었다가 2026-09-02 `mxwo_sharpe1` 브랜치를 통합해 MXWO 로 확장했고,
> 2026-09-09 QMind 로 개명하면서 기본 유니버스를 MXWO 로 바꿨다. 선정·가중 방법론은 유니버스별로 따로 튜닝돼 있고,
> `BENCHMARK` 값 하나로 파라미터·DB·출력 폴더·데이터 파일이 전부 결정됩니다 —
> [유니버스 전환](#유니버스-전환) 및 문서 끝의 [유니버스별 파라미터](#유니버스별-파라미터-mxcn1a-vs-mxwo) 참조.
> 본문 각 단계의 수치 예시(롤링 IS 48개월, 순수 Top-50, 섹터 숏캡 등)는 **MXWO 기준**이며,
> MXCN1A 는 표의 값(expanding IS, winner_median 클러스터, 숏캡 없음)으로 읽으면 됩니다.
>
> 각 섹션의 `[N]` 번호는 `model_portfolio.py:run()` 코드의 단계 주석과 동일합니다.
> 함수별 Input/Output 상세, 코드 수준 구현 세부사항은 [`research.md`](research.md) 참조.

---

## 유니버스 전환

```bash
# .env — 두 블록 중 하나만 활성 (안 쓰는 쪽은 주석). BENCHMARK 가 없으면 config.py 의 BENCHMARK 상수(기본 MXWO)
BENCHMARK=MXWO            # 또는 MXCN1A
UNIVERSE=clarifi_mxwo_afl
SERVER_NAME=10.206.101.14
DB_NAME=kb_global

# 일회성으로 덮어쓰기 (환경변수가 .env 보다 우선)
BENCHMARK=MXCN1A python main.py backtest 2009-12-31 2026-08-31
```

`BENCHMARK` 하나로 `config.py`가 `PARAM`(DB/universe), `PIPELINE_PARAMS`(공통 + 유니버스별 오버라이드),
`service/paths.py`의 `OUTPUT_DIR = output/{BENCHMARK}/`, 유니버스 종속 데이터(`data/{BENCHMARK}_*`)를 전부 결정한다.
미등록 값은 `KeyError`로 즉시 실패한다. 두 유니버스의 파라미터 차이는 [유니버스별 파라미터](#유니버스별-파라미터-mxcn1a-vs-mxwo).

**개발 브랜치/워크트리 (2026-09-09):** 유니버스별 개발은 전용 브랜치 + 형제 폴더 워크트리에서 하고 `main` 으로 머지한다.
머지가 끝난 시점에는 세 브랜치가 같은 커밋이다. 워크트리마다 자기 `.env`(BENCHMARK 고정)를 둔다.

| 브랜치 | 역할 | 폴더 | `.env` BENCHMARK |
|---|---|---|---|
| `main` | 통합본 (기본 MXWO, 파라미터만 바꾸면 양 유니버스 실행) | `C:\Users\IKM\QMind` | 자유 |
| `main_mxwo` | MXWO 개발 → `main` 머지 | `C:\Users\IKM\QMind_mxwo` | MXWO |
| `main_mxcn1a` | MXCN1A 개발 → `main` 머지 | `C:\Users\IKM\QMind_mxcn1a` | MXCN1A |

---

## 파이프라인 핵심 구조 (Funnel)

```
200+ 유효 팩터                      [1]~[3] 데이터 로딩 + 5분위 + 섹터 필터
       │                                 (min_coverage 10%, 롤링 IS 48개월 기준 학습)
       ▼
   선정 팩터 (고정 Top 50)           [4] t-stat 랭킹 → 순수 Top-50 절단 (클러스터 dedup off)
       │
       ▼
   weight>0 팩터                     [6] ERC 가중 + TS모멘텀 틸트 + 스타일 캡
       │
       ▼
   종목별 MP 비중 산출               [7] 섹터 숏캡 적용 + CSV 출력 → Bloomberg Optimizer
```

> **롤링 IS 48개월** (`is_window_months=48`): 규칙 학습·랭킹·가중 모두 매 시점
> "최근 4년" 데이터만 사용한다 (expanding 아님 — 레짐 적응 목적, 2026-07-28 채택).

---

## [1] 데이터 로딩

### 개요
- 종목 단위 **Point-in-Time(PIT)** 팩터 데이터베이스를 입력으로 사용
- 학술·실무 근거에 기반한 다수(200+) 팩터를 사전에 정의 및 축적
- 각 팩터는 **스타일 단위(Valuation, Momentum, Quality, Growth 등)**로 분류

### 입력 데이터
- `data/{benchmark}_factor_{YYYY}.parquet` — 연도별 분할 팩터 데이터
- `data/{benchmark}_mreturn.parquet` — 월간 수익률
- `data/factor_info.csv` — 팩터 메타 정보
- `data/hardcoded_weights.csv` — 프로덕션 고정 가중치 (hardcoded 모드용)

### 다운로드
- `python main.py download 2015-06-30 2026-06-30` — 전체 다운로드 (MXWO 데이터는 2015-06~)
- `python main.py download 2015-06-30 2026-07-31 --incremental` — 증분 다운로드 (end_date 월만 append)
- 로드 시 자동 무결성 검증 수행

> 연도별 분할 구조, 검증 항목 상세, fallback 경로는 [research.md §2.3, §4.3](research.md) 참조.

---

## [2] 5분위(Quintile) 포트폴리오 구성

### 핵심 함수
`factor_analysis.calculate_factor_stats_batch()`

### 절차
1. 종목별 팩터 값에 **1개월 래그 적용** (전월 값으로 당월 투자)
2. 동일 날짜·동일 섹터 내에서 팩터 값 순위 산정
3. 순위를 **백분위(0~100)**로 변환
4. 백분위를 **5분위(Q1~Q5)**로 구간화

### 결과
- 각 팩터별 Q1~Q5 분위 포트폴리오 월간 수익률 산출

---

## [3] 섹터 필터링 + L/N/S 라벨링

### 핵심 함수
`factor_analysis.filter_and_label_factors()`

### (a) 비투자 섹터 결정
- 섹터별 팩터 스프레드 계산: `팩터 스프레드 = Q1 – Q5`
- **팩터 스프레드가 음(-)인 섹터는 해당 팩터에서 제외**
- 목적: 구조적으로 팩터 수익률을 훼손하는 섹터 제거

### (b) 투자 대상 분위(롱/숏) 결정
- 섹터 제거 후 분위별 평균 수익률 재산출
- Q1–Q5 평균 스프레드를 기준으로 임계값 설정 (`spread_threshold_pct=0.05` — 스프레드의 5%. 0.025~0.05 고원 확인, 구 0.10 대비 분산 확대로 채택, 2026-07-29)
- 각 분위를 **롱(+1) / 중립(0) / 숏(-1)**으로 재분류
- 단순히 Q1=롱, Q5=숏이 아닌 **성과 기반으로 투자 대상 분위 선택**
- 유효성 가드 (2026-07): 섹터 제거 후 재계산한 전체 Q1–Q5 스프레드가 양수가 아니면 팩터 탈락 (섹터별 체크만으로는 합산 역전을 못 잡음). 스프레드>0이면 Q1=롱/Q5=숏이 보장되므로 한쪽만 있는 팩터(시장 방향 노출)가 랭킹을 오염시키는 것도 함께 차단됨

---

## [4] 롱-숏 수익률 + 팩터 유니버스 선정

### (a) 롱-숏 수익률
- 각 팩터별 롱/숏 포트폴리오 구성 → 거래비용(10bp) 차감 → 월간 L-S 수익률 행렬 생성
- 핵심 함수: `factor_returns.aggregate_factor_returns()`

### (b) 팩터 유니버스 최종 선정 (200+ -> 순수 Top-50)
- 랭킹 방식: **t-stat 기반** (기본), `shrunk_tstat` / `cagr` 선택 가능 (`factor_ranking_method`). 랭킹의 입력은 롤링 IS 48개월 수익률
- production `mp`와 walk-forward 백테스트가 `factor.selection.compute_rank_score()`를 공유 — 검증된 config과 배포 전략이 항상 일치
- **클러스터 dedup 없음** (`use_cluster_dedup=False`, MXWO 채택): rank_score **순수 Top-50 절단** (`top_factor_count=50`, 개수 고정). MXCN1A(main)와 정반대 결정 — 롤링 48개월의 짧은 창에서는 클러스터 구성이 불안정해 dedup이 좋은 팩터를 날림 (2026-07-28 A/B: dedup on -0.12 / off +0.41 Sharpe). **유사 팩터 간 중복 관리는 선정이 아니라 [6]의 ERC 가중이 담당** (겹치는 무리의 비중을 낮추는 방식)
- **선정 히스테리시스** (`selection_hysteresis=0.25`): 직전 회차 보유 팩터는 챌린저가 rank_score 격차 0.25 이상 이길 때만 교체 — 월간 리밸의 노이즈성 교체 차단 (MXWO 스윕 채택, 2026-07-29; MXCN1A는 0.5)
- 최종 비중 할당은 [6]에서 결정

---

## [6] 스타일 캡 하 비중 결정

### 핵심 함수
`optimization.optimize_constrained_weights()`

### 가중치 결정 모드 (4가지, config 키 `optimization_mode`)
- `optimization_mode="erc"` **(기본값, 2026-07-29 채택)**: 상관 인지 Equal Risk Contribution (cov 48M, 대각수축 0.2, Spinu CCD) — 팩터별 리스크 기여 w×(Σw) 균등화. [채택 근거](docs/experiments/mxwo_sharpe_ladder_20260729.md)
- `optimization_mode="equal_risk_weight"`: IS 변동성 반비례(1/σ) 가중 (상관 무시 특수 케이스, 구 기본)
- `optimization_mode="equal_weight"`: 1/N 동일가중 + 스타일 캡 재분배 (구 기본)
- `optimization_mode="hardcoded"`: `data/hardcoded_weights.csv`에서 고정 비중 로드

> 백테스트(`python main.py backtest`)는 config의 `optimization_mode`를 그대로 사용한다 (`hardcoded`만 `equal_weight`로 자동 변환).

### 절차 (erc 모드, MXWO 채택 스택)
1. **ERC base 가중**: 선정 Top-50의 롤링 48개월 수익률 cov(대각 수축 `erc_shrinkage=0.2`)에서 팩터별 리스크 기여 w×(Σw)를 균등화하는 비중 산출 (Spinu CCD — 음상관 팩터도 양수 비중 보장). 선정에서 dedup을 안 하는 대신, 상관 높은 팩터 무리의 비중을 여기서 낮춘다
2. **TS 모멘텀 틸트** (`ts_mom_window=3`, base 단계): trailing 3개월 자기 누적수익이 음수인 팩터의 비중을 `ts_mom_scale=0.2`배 감쇠 (창 4→3 재채택 2026-08-07, 감쇠 0.5→0.2 채택 2026-08-10 — 전구간 0~1 스윕 + 실측 게이트). 캡 재분배 **이전** 적용 — 2026-08-06 순서 교정 (구 캡-후-틸트는 스타일 합이 캡 초과, 2026-06-30 EQ 26.1%)
3. 스타일별 명목비중 합계가 **스타일 캡(25%)**을 넘지 않도록 비례 재분배 — 교정 후 캡 25% 준수
4. `deploy_step=1.0`: 전량 조정 배포 (10bp 비용에선 부분 조정(0.5)이 열위로 역전, 2026-07-30 실측)
5. **MP 배포 배수** (`mp_target_gross=0.40`, 2026-08-19 채택): 최종 MP 북을 실제 포트폴리오 규모로 스케일해 산출물에 미리 반영한다. 팩터 50개를 종목 단위로 netting 하면 gross 가 매 시점 달라지므로(2025-06 89.8% / 2026-06 85.6%), 고정 배수 대신 **목표 총 gross 에 맞추는 배수를 자동 산출** — 결과는 항상 롱 +20% / 숏 -20%. ex-ante TE 의 주 동인인 노출을 고정해 netting 변동이 포트 크기를 좌우하지 않게 한다. 적용 배수는 `mp_weight_history/deploy_multiplier_{기준일}.csv` 에 기록. `mp_target_gross=None` 이면 `data/mp_multiplier.csv` 의 시점별 수동 배수(계단식) 사용. **성과 백테스트·실측은 팩터 비중에서 재구성하므로 이 배수의 영향을 받지 않는다**

---

## [7] MP 구성 + CSV 출력

### (a) 종목별 최종 비중 산출
- 각 팩터 비중을 종목 수준으로 전개
- 롱/숏 종목군 내 동일가중 → 팩터 비중만큼 스케일링
- 종목별 오버웨이트 / 언더웨이트 비중 산출
- **섹터 숏 캡** (`sector_short_cap=0.15`): 한 섹터의 숏 gross가 전체 숏 gross의 15%를 넘지 않도록 상한 — 2020-11형 숏 crowding 완화 (MDD -6.72→-6.36%, 2026-07-30 실측 채택). 종목 레벨 제약이라 factor-level 백테스트에는 미반영, MP-level 실측으로 평가

### (b) Model Portfolio(MP) 구성
- 여러 팩터에서 계산된 종목 비중을 합산
- MP = **팩터 집합의 가중 평균** (단일 스타일이 아님)

### (c) 결과물 산출
- 종목 × 팩터 × 스타일 구조의 최종 가중치 패널 → CSV 출력
  - `total_aggregated_weights_{end_date}_mp.csv` — 종목×팩터 가중치 (test 모드는 `_mp_{stem}` 접미사)
  - `total_aggregated_weights_style_{end_date}_mp.csv` — 스타일별 집계 (종목 단위); `..._style_mponly_{end_date}.csv` 는 style=MP 행만 (종목별 최종 비중)
  - `pivoted_total_agg_wgt_{end_date}.csv` — 피벗 형태 (Optimizer 연동용)
  - `meta_data.csv` — 팩터 성과 요약 (test 모드에서만 `meta_data_test_*.csv`로 바뀜)
- factor 가중치 + style 요약 → `output/{BENCHMARK}/mp_weight_history/` (production 실행 시 항상 저장, test 모드는 3종 모두 미저장)
  - `factor_weights_{end_date}.csv` — factor 단위 배포 가중치 (다음 회차 전월대비 delta 입력용)
  - `factor_styles_{end_date}.csv` — factor × style + raw/prev/new 가중치 분해
  - `style_totals_{end_date}.csv` — style 단위 raw/prev/new 합계 + delta + factor 목록

### 프로덕션 활용
- 본 코드는 **Model Portfolio(MP) 산출까지 담당**
- 이후: Benchmark 대비 Tracking Error 점검 → Bloomberg Optimizer를 통한 프로덕션 매매 집행

---

## [8] Walk-Forward 백테스트 (OOS 과적합 진단)

기존 파이프라인([1]~[7])을 감싸 **롤링 48개월 윈도우**로 실행 (`is_window_months=48`; MXCN1A는 expanding). IS 데이터만으로 팩터 선정·가중치를 결정하고 OOS 1개월 수익률을 기록한다.

- **계층적 리밸런싱**: Tier 1(6개월, 규칙 학습) / Tier 2(**1개월**, 팩터 선정+가중 — 월간 채택 2026-07-29) / Tier 3(매월, OOS 조회)
- ⚠ **고회전 구성의 비용 회계**: factor-level 백테스트의 `backtest_cost_multiplier=0.6`은 저회전 전용 근사 — 월간 리밸에서는 실비용이 과소계상된다 (실측 netting 최대 1.8배). **정본 성과 판단은 배포 기준(종목단 재구성 + 목표 노출 고정) 실측**: `backtest` 실행이 `stock_level_series_{기준일}.csv` 로 함께 산출한다. **현 채택 스택 (롱 +20% / 숏 -20%, 수수료 10bp + 국가별 거래세): Sharpe 0.734 / IR 0.732 / CAGR +0.76% / MDD -1.80% / Calmar 0.423 / 실현 TE 1.04% / 턴오버 1.7x** (OOS 2018-06~2026-06, 97개월). TE 는 시장중립 오버레이라 액티브수익=오버레이수익이므로 월 순수익 표준편차의 연환산 (실현/ex-post — Bloomberg ex-ante 추정치와는 다름)
- **과적합 진단 5지표**: Funnel Value-Add, OOS Percentile Tracking, Strict Jaccard, IS-OOS Rank Correlation, Deflation Ratio

> **상세**: 각 Tier의 look-ahead bias 방지 규칙, 5지표 해석 임계값, 판정 패턴(CONSTRAINT_DRAG/FILTER_OVERFIT) 설명은 [research.md §6](research.md) 참조.

### 용어: MP vs Constrained EW
- **MP (Model Portfolio)** — 프로덕션 산출물 (Bloomberg Optimizer 입력 CSV). 역할 이름.
- **Constrained EW** — MP를 만드는 **구성 방식**의 관례적 라벨 (선정 팩터 가중 + `style_cap=25%` 재분배). MXWO에선 Top-50 고정 + **ERC(수축 0.2) + TS모멘텀 틸트** 가중 (EW 1/N → ERW 1/σ → ERC로 진화)이나, 백테스트 리포트/CSV 컬럼의 "CEW" 라벨은 호환을 위해 유지.
- 백테스트 진단 리포트는 구성 방식을 명시하기 위해 "Constrained EW" 라벨을 사용. 프로덕션 CLI/파일명/CSV 컬럼은 "MP" 유지.
- 과거 MP는 Monte Carlo 최적화로 구성됐으나 커밋 `8dfb64e`에서 제거됨.

---

## ✅ 전체 프로세스 요약

| 단계 | 목적 | 핵심 함수 |
|------|------|-----------|
| `[1]` 데이터 로딩 | PIT 기반 종목·팩터 데이터 확보 | `_load_data`, `_prepare_metadata` |
| `[2]` 5분위 분석 | 팩터별 분위 포트폴리오 구성 | `calculate_factor_stats_batch` |
| `[3]` 섹터 필터 + 라벨링 | 비효과 섹터 제거, L/N/S 분류 | `filter_and_label_factors` |
| `[4]` 팩터 유니버스 선정 | 롱-숏 수익률 + rank_score 랭킹 (기본 t-stat) | `evaluate_universe` |
| `[6]` 비중 결정 | 스타일 캡 하 가중치 계산 | `optimize_constrained_weights` |
| `[7]` MP 구성 + 출력 | 종목별 최종 비중, CSV 저장 | `_construct_and_export` |
| `[8]` Walk-Forward 백테스트 | OOS 과적합 진단 | `WalkForwardEngine.run` |

---

## 📊 Visualization
- [Variable Flow Graph](docs/VARIABLE_FLOW.md): `mp` 함수 내 변수 흐름 상세 시각화

---

## 📁 모듈 구조

```
service/
├── paths.py                    # 경로 상수 단일 출처 (PROJECT_ROOT / DATA_DIR / OUTPUT_DIR / HISTORY_DIR)
│
├── factor/
│   ├── selection.py            # rank_score 랭킹, 클러스터 dedup, 선정 히스테리시스 (production mp + 백테스트 공유 도메인)
│   └── factor_returns.py       # aggregate_factor_returns (팩터 롱-숏 수익률 행렬, mp + 백테스트 공유)
│
├── download/
│   ├── download_factors.py      # SQL → 연도별 parquet 다운로드
│   ├── download_validation.py   # 다운로드 후 parquet 커버리지 검증 (validate_parquet_coverage)
│   └── parquet_io.py            # 연도별 분할 저장/로드/검증 유틸리티
│
├── pipeline/
│   ├── model_portfolio.py      # Pipeline 오케스트레이터 (ModelPortfolioPipeline 클래스)
│   ├── universe.py             # evaluate_universe: 팩터 유니버스 평가 + rank_score 상위 N 선정
│   ├── factor_analysis.py      # calculate_factor_stats_batch, filter_and_label_factors
│   ├── optimization.py         # optimize_constrained_weights (erc/equal_risk_weight/equal_weight/hardcoded)
│   ├── weight_construction.py  # build_factor_weight_frames, aggregate_mp_weights, calculate_style_weights, construct_long_short_df, calculate_vectorized_return
│   └── weight_history.py       # mp_weight_history CSV 3종 저장 (factor_weights / factor_styles / style_totals)
│
├── backtest/
│   ├── walk_forward_engine.py  # Walk-Forward 오케스트레이터 (롤링/expanding IS)
│   ├── data_slicer.py          # IS/OOS 경계(<= inclusive) 계약 + OOS 시작월 계산
│   ├── result_stitcher.py      # OOS 결과 접합 + 성과 계산 (WalkForwardResult)
│   └── overfit_diagnostics.py  # 과적합 진단 (Funnel Value-Add, Percentile, Strict Jaccard + 보조)
│
└── report/                     # 시각화/리포트 (output CSV read-only 레이어)
    ├── dashboard.py            # 대시보드 조립: CSV -> plotly 차트 -> 단일 자체완결 HTML
    ├── dashboard_data.py       # 대시보드 데이터 레이어 (KPI/스타일 집계/진단 파싱, read-only)
    ├── dashboard_charts.py     # DataFrame -> plotly Figure
    ├── report_generator.py     # 성과 리포트 생성
    ├── reporting.py            # Rich 콘솔 리포트 출력
    ├── diagnostics_keys.py     # overfit_diagnostics.csv 행 키 상수 (생산자/소비자 공유 계약)
    └── style_colors.py         # 스타일 -> 색상 매핑 단일 출처
```

### Pipeline 사용법
```python
from service.pipeline.model_portfolio import ModelPortfolioPipeline

pipeline = ModelPortfolioPipeline(config=PARAM, factor_info_path="data/factor_info.csv")
pipeline.run(start_date="2023-01-01", end_date="2023-12-31")

# 중간 결과 접근
pipeline.meta           # 팩터 성과/랭크 테이블
pipeline.weights        # 최적 가중치
pipeline.return_matrix  # 월간 수익률 행렬
```

### Walk-Forward 백테스트 사용법
```bash
# 기본 실행 (MXWO: 롤링 IS 48개월 + 월간 가중 리밸, OOS 매월)
python main.py backtest 2015-06-30 2026-06-30

# 파라미터 조정
python main.py backtest 2009-12-31 2026-03-31 \
  --min-is-months 60 \
  --factor-rebal-months 6 \
  --weight-rebal-months 3 \
  --top-factors 50

# 테스트 모드
python main.py backtest test test_data.csv --min-is-months 4
```

```python
# 프로그래밍 방식
from service.backtest.walk_forward_engine import WalkForwardEngine

engine = WalkForwardEngine(min_is_months=36, factor_rebal_months=6, weight_rebal_months=1,
                           is_window_months=48)   # MXWO 채택: 월간 가중 리밸 + 롤링 48M
result = engine.run("2015-06-30", "2026-06-30")

# OOS 성과 확인
result.calc_performance()           # CAGR, MDD, Sharpe, Calmar
result.compare_cew_vs_ew_oos()     # Constrained EW vs. EW 비교
result.to_csv("output/{BENCHMARK}/wf.csv")      # 결과 저장
```

### 실행 결과
백테스트 결과 및 과적합 진단 상세는 [`docs/backtest_results_2009_2026.md`](docs/backtest_results_2009_2026.md) 참조.

**산출 파일:**
- `output/{BENCHMARK}/walk_forward_results.csv` — OOS 월별 Constrained EW / EW(선정) / EW_All / EW_Top50(dedup 이전 랭킹 Top-50) 수익률 + 누적 수익률 ([research.md §6.4](research.md) 곡선 정의 참조)
- `output/{BENCHMARK}/overfit_diagnostics.csv` — 과적합 진단 5개 지표 요약
- `output/{BENCHMARK}/walk_forward_weight_history.csv` — 월별 팩터 가중치 이력 (대시보드 비중 추이/회전율용)
- `output/{BENCHMARK}/dashboard_<date>.html` — **백테스트 실행 시 자동 생성**되는 인터랙티브 리포트 (KPI + 과적합 진단 전체 표 + 차트). `viz`로 재생성 가능

### 시각화 대시보드 사용법 (viz)
백테스트 내역과 현재 포트(배팅)를 단일 인터랙티브 HTML 리포트로 본다.
기존 `output/{BENCHMARK}/*.csv`만 읽는 read-only 레이어라 파이프라인을 건드리지 않는다 (plotly 사용, 새 의존성 없음).
`backtest` 실행 시 자동 생성되며, 아래 `viz`로 언제든 최신 CSV 기준 재생성한다.

```bash
# 최신 스냅샷으로 대시보드 생성 -> output/{BENCHMARK}/dashboard_<date>.html
python main.py viz

# 특정 스냅샷 날짜 지정
python main.py viz 2026-05-31

# 생성 후 기본 브라우저로 바로 열기
python main.py viz --open
```

포함 차트:
- (백테스트) KPI 카드(CAGR/MDD/Sharpe/Calmar/승률/Funnel - `overfit_diagnostics.csv` 값 우선),
  **상세 성과 통계**(Sortino/연변동성/최고·최저월/상승월%/왜도/최장연속손실 + 벤치마크(선정 EW) 대비 Beta/Alpha/정보비율/추적오차),
  누적수익 4선 비교, **월별 수익률 히트맵(연×월)**, 낙폭, 월별수익 분포, **롤링 12개월 Sharpe**, **스타일 비중 추이(스택 영역)**, **팩터 회전율**,
  **과적합 진단 상세 표**(Funnel 패턴 + **OOS 성과**(CAGR/MDD/Sharpe/Calmar)를 **EW/Top50/CEW 3열**로 비교 + Jaccard/Deflation/Rank Corr 등),
  **낙폭 구간 분석 표**(곡선별 DD episode: 깊이 + peak/trough/recovery + 하락·회복 기간(개월), 1% 이상)
- (현재 포트) 스타일 배분(25% cap 라인), **섹터별 순비중(롱-숏 순노출)**, 종목별 순비중 상위 롱/숏,
  팩터 틸트, 팩터 리더보드(tstat vs CAGR), 전월 대비 스타일 변화(운영모드만)

HTML은 plotly.js 인라인이라 오프라인에서 단독으로 열린다.

> **스타일 비중 추이/회전율**은 `output/{BENCHMARK}/walk_forward_weight_history.csv`가 있어야 표시된다.
> 이 파일은 `python main.py backtest ...` 실행 시 생성되므로, 백테스트를 한 번 돌려야 한다.
> **섹터별 순비중**은 `data/{benchmark}_factor_<연도>.parquet`을 read-only 로 읽어 `gvkeyiid`로 join한다
> (파이프라인/출력 스키마 무수정).

---

## 파이프라인 비즈니스 파라미터 (`PIPELINE_PARAMS`)

`config.py`의 `PIPELINE_PARAMS`에서 중앙 관리. Pipeline 클래스 생성자에서 주입되며, 각 모듈 함수에 파라미터로 전달됨. **아래 값은 MXWO 기준**이며, 유니버스별로 다른 10개 키는 [유니버스별 파라미터](#유니버스별-파라미터-mxcn1a-vs-mxwo) 표 참조.

| 파라미터 | 값 | 설명 | 사용 모듈 |
|---------|-----|------|-----------|
| `style_cap` | 0.25 | 스타일 캡 (프로덕션 규제 요건) | `optimization.py` |
| `optimization_mode` "erc" | - | 상관 인지 Equal Risk Contribution 가중 (2026-07-29 채택, **07-30 Spinu CCD 솔버로 정정** — RC 균등·음의 상관 헤지 팩터 우대 보장; cov 48M) | `optimization.py` |
| `erc_shrinkage` | 0.2 | ERC cov 대각 수축 비율 (0~1 전구간 스윕 단조 하강, 0.2 채택 2026-08-07 — 0은 특이 cov 회피) | `optimization.py` |
| `deploy_step` | 1.0 | 부분 조정 배포 (1.0=전량). 20bp 시절 0.5, 10bp 전환 후 역전으로 1.0 (실측 0.672 vs 0.604) | `optimization.py`, `model_portfolio.py`, `walk_forward_engine.py` |
| `ts_mom_window` / `ts_mom_scale` | 3 / 0.2 | 팩터 TS 모멘텀 틸트 — trailing 3M 자기수익 음수 팩터 비중 x0.2. 창 4→3 재채택 (2026-08-07), 감쇠 0.5→0.2 채택 (2026-08-10 전구간 스윕: 0 방향 단조, 실측 net 0.782→0.840; 0은 벼랑 규칙+회전 4.4x 회피) | `optimization.py` |
| `sector_short_cap` | 0.15 | 섹터별 숏 gross 상한 (전체 숏 gross 대비) — 2020-11형 숏 crowding 완화. 종목 레벨이라 factor-level 백테스트 미반영 (실측으로 평가) | `weight_construction.py` |
| `weight_rebal_months` | 1 | Tier 2 가중 리밸 주기 (월간 채택) | `walk_forward_engine.py` |
| `transaction_cost_bps` | 10.0 | 거래비용 (bp). MXWO 선진국 대형주 실집행 기준 (2026-07-30 사용자 지정; MXCN1A는 20) | `weight_construction.py`, `model_portfolio.py` |
| `backtest_cost_multiplier` | 0.6 | **선정 입력용** 비용 배수 (비용 인지 선정 최적). factor-level 성과 회계는 고회전 구성에서 과소계상 — **정본 성과 판단은 `research/mp_level_cost_backtest.py` 실측 기준** (정식 ERC 실측 net Sharpe 0.368, 2026-07-30 정정) | `walk_forward_engine.py` |
| `top_factor_count` | 50 | rank_score 상위 절단 수 (**`cluster_method=topn`일 때만** 적용; winner_median은 미사용) | `model_portfolio.py` |
| `factor_ranking_method` | "tstat" | 팩터 랭킹 방식 (`shrunk_tstat` / `tstat` / `cagr`) | `universe.py`, `walk_forward_engine.py` (`selection.compute_rank_score` 공유) |
| `use_cluster_dedup` | False | Hierarchical Clustering 중복 제거. **MXWO: off** — 롤링 IS와 winner_median 궁합 문제 (2026-07-28 절단 실험: on -0.12 / off +0.41 Sharpe). MXCN1A(main)는 True | `model_portfolio.py`, `walk_forward_engine.py` |
| `is_window_months` | 48 | 롤링 IS 윈도우 (개월, None=expanding). 규칙 학습·선정·가중을 최근 N개월로 제한 — 레짐 적응 (2026-07-28 w36~72 스윕, 내부 고원점 채택) | `model_portfolio.py`, `walk_forward_engine.py` |
| `cluster_method` | "winner_median" | 클러스터 압축 규칙 (**`winner_median`(기본)**: 1등보호+중위값바닥 / `topn`: 상위3→Top-N) | `factor/selection.py` |
| `n_clusters` | 18 | 클러스터 수 (`use_cluster_dedup=True`일 때) | `factor/selection.py` |
| `per_cluster_keep` | 3 | 클러스터당 유지 팩터 수 | `factor/selection.py` |
| `newey_west_lag` | 3 | Newey-West 보정 lag (meta_data 진단 컬럼) | `factor/selection.py` |
| `spread_threshold_pct` | 0.05 | L/N/S 라벨링 임계값 (2026-07-29 0.05 채택, 0.025~0.05 고원) | `factor_analysis.py` |
| `min_sector_stocks` | 10 | 섹터-날짜 최소 종목 수 | `factor_analysis.py` |
| `min_coverage_pct` | 0.10 | 팩터 최소 단면 커버리지 (유니버스 대비 유효 관측 비율, IS 기준). 은행 전용 등 초저커버리지 팩터 제외 (2026-07-27 MXWO A/B 채택) | `factor_analysis.py` |
| `max_zero_return_months` | 10 | 0 수익률 허용 최대 월 수 | `model_portfolio.py` |
| `backtest_start` | "2009-12-31" | 백테스트 시작일 | `weight_construction.py`, `model_portfolio.py` |
| `backtest_end` | "2026-03-31" | 백테스트 종료일 (실험 스크립트 참조용) | `research/*.py` |
| `selection_hysteresis` | 0.25 | 선정 히스테리시스 margin (rank_score 단위, 0=off). 직전 선정 팩터는 챌린저가 이 격차 이상 이겨야 교체 | `model_portfolio.py`, `walk_forward_engine.py` (`apply_selection_hysteresis` 공유) |

> **실험 결과:** [docs/experiments/cluster_turnover_20260425.md](docs/experiments/cluster_turnover_20260425.md) 참조 (43 케이스 광역 sweep). 1장 요약은 [executive_summary.md](docs/experiments/executive_summary.md). 핵심 발견: ① `OPTIMIZATION_OVERFIT` 실체 = style_cap 의 OOS 비용, ② n_clusters sweet spot 18~30, ③ Clustering 후 style_cap 효과 거의 없음, ④ smoothing α 0.1 saturation, ⑤ ranking method 는 t-stat 이 베스트, ⑥ min_is_months 는 모델에 영향 없음, ⑦ **baseline 은 2023~ Sharpe 0.27 / 21개월째 -6% 미회복 — 위험**, ⑧ **combo_18_0.1 은 같은 기간 Sharpe 0.99 / 회복 완료** (3.7배 차이). 당시 권장이던 `combo_18_0.1` 중 **clustering(n=18)은 적용 유지**, smoothing α=0.1(EMA)은 이후 절대스텝 -> 무스무딩으로 대체되었고, 2026-06 비용-인지 실험으로 **선정 히스테리시스(0.5)가 최종 적용**됨 — [smoothing_cost_experiment_20260612.md](docs/experiments/smoothing_cost_experiment_20260612.md) 참조. 2026-07-05 선정/필터 개선안 5종(섹터 유의성 게이트, half-life t-stat, IQR margin, 비례 zero-filter, 기하평균 스프레드)은 **A/B 전부 기각**(현행 국소 최적 재확인), EW_Top50 진단 곡선 pre-dedup 복원만 채택 — [proposal_experiments_20260705.md](docs/experiments/proposal_experiments_20260705.md) 참조.

## 보안 설정

- **`.env`**: DB 비밀번호, 서버 주소 등 민감 정보 (git 미추적)
- **`.env.example`**: `.env` 템플릿 (값 예시)
- **`pre-commit hook`**: `detect-secrets`로 비밀번호/토큰 커밋 자동 차단
- **SQL allowlist**: `factor_query.py`에서 허용된 테이블명만 통과
- **path traversal 검증**: `test_file` CLI 인자가 프로젝트 디렉토리 내부인지 검사

---

## 유니버스별 파라미터 (MXCN1A vs MXWO)

방법론은 유니버스별로 따로 튜닝됐다 (2026-08 교차 이식 실험: 어느 쪽 스택도 상대 유니버스에 통째로
이식하면 대폭 열위 — 컴포넌트 단위로만 이전 가능). 2026-09-02 부터 두 유니버스가 같은 `main`에서
`BENCHMARK` 로 분기된다.

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
| `mp_target_gross` | 0.14 (롱 +7% / 숏 -7%, 2026-08-31 스냅샷부터; 이전은 배수 1.0) | 0.40 (롱 +20% / 숏 -20%) |
| `apply_country_tax` | False (A주는 등록지 무관하게 본토 인지세 대상 — 등록지 세율표 부적합) | True (COUNTRY_TAX_BPS, 실측 회계 전용) |
| 출력 경로 | `output/MXCN1A/` | `output/MXWO/` |
| 유니버스 종속 데이터 | `data/MXCN1A_*` (+ `_mp_target_gross.csv`) | `data/MXWO_*` (+ `_mp_target_gross.csv`, `_mp_multiplier.csv`, `_bm_returns.csv`, `_bmwgt.parquet`, `_country_map.parquet`) |
| 정본 실측 (배포 기준) | net Sharpe 0.703 / MDD -4.87% (미스케일, 거래세 미반영) | Sharpe 0.734 / MDD -1.80% / TE 1.04% (롱숏 ±20%, 거래세 반영) |

공통 항목(style_cap 0.25, spread 0.05, ERC 모드, ts_mom_window 3, deploy_step 1.0 등)은 `config.py`의 `_COMMON_PARAMS`,
유니버스별 항목은 `_UNIVERSE_PARAMS[BENCHMARK]` — `PIPELINE_PARAMS = {**_COMMON_PARAMS, **_UNIVERSE_PARAMS[BENCHMARK]}`.
채택 근거(실험 문서·날짜)는 각 값 옆 주석 참조. MXCN1A: [`mxcn1a_component_ablation_20260805.md`](docs/experiments/mxcn1a_component_ablation_20260805.md),
MXWO: [`mxwo_sharpe_ladder_20260729.md`](docs/experiments/mxwo_sharpe_ladder_20260729.md).
