# MP Turnover Smoothing: 배포 정규화 + 메모리 Pruning 설계

- **날짜:** 2026-06-04
- **상태:** 설계 승인됨 (구현 계획 대기)
- **작성 배경:** 2026-05-31 데이터 업데이트 + mp 실행 중, EMA turnover smoothing이 production에서 처음 작동한 달의 산출물을 검증하다 발견된 두 가지 문제.

---

## 1. 배경 / 문제

### 1.1 발견 경위
2026-04-30이 production에서 EMA smoothing이 적용된 **첫 회차**(prev 없음 → raw 배포)였고, **2026-05-31이 EMA 블렌딩이 실제로 처음 작동한 회차**다. 5월 산출물 검증 중 다음을 확인했다.

- `output/mp_weight_history/factor_weights_2026-05-31.csv`: 47 factor, 합 = **1.000000** (메모리는 정상)
- 그러나 **실제 배포되는 MP factor 가중치 합 = 0.7632** (37개 현재 선정분), 나머지 **0.2368은 4월에만 있던 10개 "decay 중" factor**가 차지 → 이들은 메모리(history)에만 남고 배포(bbg 입력물)에서는 빠짐.

### 1.2 두 가지 문제
1. **배포 gross 축소 (버그):** `model_portfolio.py`의 `[6.5]` 단계에서 블렌딩 결과(union, 합 1.0)를 현재 선정 factor에만 매핑한 뒤 **renormalize를 하지 않아** 배포 합이 1.0 미만(5월 0.76)이 된다. bbg Optimizer 입력물의 그로스가 의도보다 ~24% 작아진다.
2. **메모리 누적 (creep):** `blend_ema`는 union 기준으로 블렌딩하고 `save_factor_weights`는 그 union을 그대로 저장한다. 빠진 factor는 0.9배씩 감소할 뿐 0이 되지 않아, 회차가 지날수록 history의 factor 수가 단조 증가한다. 거의 배포되지 않는 유령 factor가 쌓여 관리·직관성이 떨어진다.

### 1.3 근본 원인: 백테스트와 production의 구현 분리(divergence)
turnover smoothing은 **백테스트(`walk_forward_engine.py`)에서 먼저 검증**되었고(`combo_18_0.1`: n_clusters=18, α=0.1 → OOS Sharpe ~0.99), production `mp`는 이를 나중에 **별도 구현**(`weight_history.py` + `model_portfolio.py [6.5]`)으로 미러링했다. 두 구현이 어긋나 위 문제가 생겼다.

**검증된 백테스트는 이미 올바르게 동작한다** (`walk_forward_engine.py:477~515`):
```python
# (1) 메모리 상태: union 블렌딩 후 renormalize (합 = 1.0)
all_factors = set(raw_new_weights) | set(cached_weights)
blended = {f: raw_new_weights.get(f,0)*alpha + cached_weights.get(f,0)*(1-alpha) for f in all_factors}
total = sum(blended.values())
cached_weights = {f: w/total for f, w in blended.items()}      # 메모리 (carry forward)
cached_selected_factors = list(raw_new_weights.keys())          # 현재 선정분만

# (2) 배포: 현재 선정(가용) factor로 제한 후 다시 renormalize (합 = 1.0)
avail_weights = {f: cached_weights[f] for f in available_factors if f in cached_weights}
total_w = sum(avail_weights.values())
avail_weights = {f: w/total_w for f, w in avail_weights.items()}  # 실제 배포
oos_return = sum(oos_factor_returns[f] * avail_weights.get(f, 0) for f in available_factors)
```
즉 백테스트는 **(1) 메모리는 union+renorm으로 carry**, **(2) 배포는 현재 선정분만 renorm**하여 gross 1.0 + 배포 factor 수 고정을 이미 달성하고 있다. **production이 (2)의 renorm을 누락**한 것이 1.1의 버그다.

단, 백테스트도 메모리(`cached_weights`)는 union을 그대로 carry하므로 **메모리 creep(문제 2)은 동일하게 존재**한다(다만 내부 상태일 뿐 배포물엔 무해).

---

## 2. 목표 / 비목표

### 목표
- production 배포 가중치 합 = **1.0** (백테스트와 동일하게 현재 선정분 renorm).
- 메모리(history) factor 수를 **유한하게 유지**: 사용자 확정 규칙 — "**비중 < 1% AND 현재 선정에 없음**"이면 메모리에서 제거 후 100% 재정규화.
- 백테스트와 production이 **동일한 smoothing 로직을 공유**하여 재발(drift) 방지.
- 백테스트 재검증으로 `combo_18_0.1` 성과(OOS Sharpe ~0.99)가 **열화되지 않음** 확인.

### 비목표
- **배포 factor 개수 축소(예 3~9개)는 하지 않는다.** 그것은 선정 단계(`n_clusters`/`per_cluster_keep`) 노브이며 분산/변동성 성과 영향이 커 별도 주제다. 본 설계는 배포 개수를 현행(~37)으로 유지한다.
- smoothing 알고리즘 자체(α 값, EMA 방식) 변경 없음. α=0.1 유지.
- 선정 로직(cluster_dedup), style_cap, 5분위 분석 등 다른 단계 변경 없음.

---

## 3. 설계

### 3.1 메모리 정책 (확정 알고리즘)
prev가 존재하고 0 < α < 1.0일 때, 매 회차:
```
1) blend  = α·raw + (1-α)·prev                         (union 기준, 합 = 1.0)
2) prune  : blend에서 factor f 제거 ⇔ (blend[f] < min_weight) AND (f ∉ 현재선정 raw)
3) memory = prune 생존분을 100% 재정규화                  → factor_weights_{date}.csv 저장(다음 회차 prev)
4) deploy = memory에서 현재선정 factor만 추출 → 100% 재정규화 → bbg 입력물
```
- **현재 선정된 factor는 비중이 작아도 절대 prune되지 않는다** (조건의 AND). 새로 진입해 α 때문에 작게 시작한 factor(예: 0.1×raw)도 "현재 선정에 있음"으로 보호된다.
- **유지 = NOT(prune)** = `(blend[f] >= min_weight) OR (f ∈ 현재선정)`.
- 현재 선정 factor는 모두 메모리에 보존되므로 4)의 deploy 집합이 비는 일은 없다.

**임계값:** `min_weight = 0.01` (1%). decay 예시: 2.6%에서 시작한 빠진 factor는 약 10개월 후 1% 미만으로 떨어져 제거된다(0.9^10×2.6% ≈ 0.91%). 임계값을 키우면 잔류 기간이 짧아진다(예 2% → ~4개월). 설정값으로 노출한다.

### 3.2 코드 구조 (공통 함수 통합)
중복 구현을 제거하기 위해 smoothing/deploy 로직을 **단일 모듈**(`service/pipeline/smoothing.py`, 신규)로 추출하고 백테스트·production이 공유한다.

```python
def update_smoothing_memory(
    raw: dict[str, float],          # 이번 회차 optimizer 산출 (현재 선정, 합 1.0)
    prev: dict[str, float] | None,  # 직전 회차 메모리 (합 1.0), 첫 회차면 None
    alpha: float,                   # EMA 비율 (0 < α <= 1.0)
    min_weight: float,              # prune 임계값 (예 0.01)
) -> dict[str, float]:
    """blend -> prune -> renorm. 반환: 다음 회차 prev로 carry/저장할 메모리 (합 1.0).

    - prev is None 또는 alpha >= 1.0: raw를 그대로 반환 (no-op).
    - prune: (w < min_weight) AND (f not in raw) 인 factor 제거.
    """

def deploy_weights(
    memory: dict[str, float],       # update_smoothing_memory 결과
    factors: list[str] | set[str],  # 배포 대상 factor (production: 현재 선정 / 백테스트: 현재 선정 ∩ OOS 가용)
) -> dict[str, float]:
    """memory를 factors로 제한 후 100% 재정규화. 반환: 실제 배포 가중치 (합 1.0)."""
```
- `deploy`를 분리한 이유: 배포 대상 집합이 호출처마다 다르다(production은 현재 선정분, 백테스트는 현재 선정 ∩ OOS 가용 factor). 메모리 갱신은 동일, 배포 집합만 호출처가 결정한다.
- 기존 `weight_history.blend_ema`는 `update_smoothing_memory`로 대체/흡수한다(중복 제거).

### 3.3 production 적용 (`model_portfolio.py [6.5]`)
- `blend_ema(raw_weights, prev_weights, alpha)` → `update_smoothing_memory(raw_weights, prev_weights, alpha, min_weight)`로 교체.
- 배포: 현재 `weights_tbl["factor"].map(new_weights).fillna(0.0)` 직후 **`deploy_weights(memory, current_factors)` 적용** → `fitted_weight`가 합 1.0이 되도록 수정 (현 버그 수정).
- 저장: `save_factor_weights`에는 **memory**(pruned, 합 1.0)를 저장(다음 회차 prev). `min_weight`는 `pipeline_params`에서 읽는다.

### 3.4 백테스트 적용 (`walk_forward_engine.py:477~515`)
- 인라인 blend/renorm 블록을 `update_smoothing_memory(...)` 호출로 교체(**prune 추가**).
- `avail_weights` 계산을 `deploy_weights(cached_weights, available_factors)`로 교체(기존 동작과 동일, 단 메모리가 pruned된 것이 입력).
- 생성자에 `min_weight`(또는 `turnover_min_weight`) 파라미터 추가.

### 3.5 설정 (`config.py`)
```python
PIPELINE_PARAMS = {
    ...
    "turnover_smoothing_alpha": 0.1,
    "turnover_min_weight": 0.01,   # 신규: 메모리 prune 임계값 (현재 미선정 factor 한정)
}
```
- `main.py` backtest 서브커맨드에 `--turnover-min-weight`(기본 0.01) 추가, `WalkForwardEngine`에 전달.

### 3.6 출력 CSV 변경 (관리·직관성)
`factor_styles_{date}.csv`, `style_totals_{date}.csv`에 **`deployed_weight`** 컬럼을 추가하여 "장부(memory)"와 "실제 배포(deploy, 합 1.0)"를 분리 표시한다.
- 기존 `raw_weight`(이번 회차 optimizer), `prev_weight`(직전 메모리), `new_weight`(= memory, blend→prune→renorm 결과)는 유지.
- 신규 `deployed_weight`: 현재 선정분만 renorm한 실제 배포 가중치(메모리 전용 factor는 0).
- `_build_factor_style_df`에 `deployed` dict 인자를 추가하여 컬럼 생성.

---

## 4. 엣지케이스

| 상황 | 동작 |
|---|---|
| 첫 회차 (prev=None) | blend/prune 없음. memory = deploy = raw (이미 합 1.0). 4월(2026-04-30)이 이 경우 — 변경 없음. |
| α >= 1.0 (smoothing off) | prev 무시. memory = raw, deploy = raw. |
| 현재 선정 factor의 blend 비중 < 1% | **prune 안 됨**(현재 선정 보호). deploy에서 renorm으로 스케일업. |
| 신규 진입 factor (α로 작게 시작) | "현재 선정"이라 보호. deploy 포함. |
| 백테스트 OOS에 현재 선정 factor 누락 | `deploy_weights(memory, available_factors)`가 가용분만 renorm(기존 동작 보존). |
| prune로 다수 제거 후 renorm | 잔존분 합 1.0로 정규화. 제거분(<1% 유령들)의 소량 질량만 재분배 — 영향 미미. |

---

## 5. 검증 계획 (CLAUDE.md 프로세스)

### A. 단위 테스트 (`tests/test_unit/`, 신규)
- `update_smoothing_memory`: blend 정확성 / prune 조건 (1% AND not-current) / renorm 합=1.0 / prev=None·α>=1.0 no-op / 현재 선정 보호(작아도 유지).
- `deploy_weights`: 합=1.0 / 빈 입력 / 부분 가용.
- 회귀: 다회차 시뮬레이션에서 메모리 factor 수가 유한 수렴(무한 증가 안 함) 확인.

### B. test 모드 diff (`mp test test_data.csv`)
- **의도된 변경**이므로 전후 동일이 아님. 기대 diff를 문서화: 배포 가중치 합 → 1.0, 메모리 컬럼 구조(+deployed_weight).

### C. production 재실행 (`mp 2026-05-31`)
- 배포 합 = **1.0** 확인(현 0.76 → 1.0).
- 메모리에서 1% 미만 & 미선정 factor 제거 확인(decay 중 10개 중 해당분).
- 산출물(`pivoted_*`, `total_aggregated_weights_*`, `factor_*`) 재생성하여 기존 5월(버그) 산출물 교체.

### D. 백테스트 재검증 (parity, 필수)
- `combo_18_0.1` 설정으로 walk-forward 전/후 실행, OOS **Sharpe / CAGR / MDD** 비교.
- **수용 기준:** OOS Sharpe가 ~0.99에서 유의미하게 하락하지 않음(소폭 변동 허용). 만약 유의미 하락 시 `min_weight` 재검토.
- prune 대상이 어차피 배포되지 않는 <1% factor이므로 영향은 미미할 것으로 예상.

### E. 회귀
- `python -m pytest tests/test_unit/ -v` 전체 통과.

---

## 6. 변경 파일 요약

| 파일 | 변경 |
|---|---|
| `service/pipeline/smoothing.py` | **신규** — `update_smoothing_memory`, `deploy_weights` |
| `service/pipeline/weight_history.py` | `blend_ema` 대체/제거, `_build_factor_style_df`·save 함수에 `deployed_weight` 추가 |
| `service/pipeline/model_portfolio.py` | `[6.5]` 블렌딩 호출 교체 + **배포 renorm 추가**, `min_weight` 주입 |
| `service/backtest/walk_forward_engine.py` | 인라인 blend → 공통 함수, **prune 추가**, `min_weight` 파라미터 |
| `config.py` | `turnover_min_weight: 0.01` 추가 |
| `main.py` | backtest `--turnover-min-weight` 옵션 |
| `tests/test_unit/` | smoothing 단위 테스트 신규 |
| `output/` (재생성) | 2026-05-31 산출물 교체 |
| 문서 | `research.md`(§6 smoothing), 필요 시 `README.md` 갱신 |

---

## 7. 리스크 / 열린 질문
- **백테스트 수치 변화:** prune 추가로 `combo_18_0.1` 수치가 미세 변동할 수 있음. 변경 후 백테스트가 **새 기준선**이 되며, 5.D에서 열화 없음을 확인한다.
- **min_weight 튜닝:** 1%는 잔류 ~10개월. 너무 길다고 판단되면 후속에서 상향 조정(설정값으로 노출되어 코드 변경 불필요).
- **하위호환:** 기존 `factor_weights_2026-04-30.csv`(prev 입력)는 그대로 사용. 4월 산출물은 재생성 불필요(첫 회차, prune 무관).
