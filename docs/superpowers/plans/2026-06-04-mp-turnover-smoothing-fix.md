# MP Turnover Smoothing 배포 정규화 + 메모리 Pruning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** production `mp`의 EMA smoothing 배포 가중치를 합=1.0으로 정규화하고, EMA 메모리에서 "1% 미만 AND 현재 미선정" factor를 제거(pruning)하며, 백테스트와 production이 동일한 smoothing 로직을 공유하게 한다.

**Architecture:** smoothing/deploy 로직을 신규 `service/pipeline/smoothing.py`로 통합(`update_smoothing_memory` = blend→prune→renorm 메모리, `deploy_weights` = 현재 선정분 renorm 배포). `model_portfolio`(production)와 `walk_forward_engine`(백테스트)이 둘 다 호출하여 divergence 재발을 막는다. blend의 저수준 primitive는 기존 `weight_history.blend_ema`를 재사용한다.

**Tech Stack:** Python 3.13, pandas, pytest. 기존 BOK 파이프라인 모듈 패턴 준수.

**Spec:** [docs/superpowers/specs/2026-06-04-mp-turnover-smoothing-fix-design.md](../specs/2026-06-04-mp-turnover-smoothing-fix-design.md)

**Branch:** `fix/turnover-smoothing` (이미 생성됨, spec 커밋 `b4da4ba` 포함). 작업 디렉토리에 2026-05-31 데이터/산출물(미커밋)이 존재 — Task 6에서 재생성 후 커밋.

---

## File Structure

| 파일 | 책임 | 변경 |
|---|---|---|
| `service/pipeline/smoothing.py` | turnover smoothing 순수 계산 (blend/prune/renorm/deploy) | **신규** |
| `tests/test_unit/test_smoothing.py` | smoothing.py 단위 테스트 | **신규** |
| `service/pipeline/weight_history.py` | factor weight 영속화(load/save) + factor×style 요약. `blend_ema`는 smoothing의 primitive로 유지 | save 함수에 optional `deployed_weights` |
| `service/pipeline/model_portfolio.py` | 파이프라인 오케스트레이션 `[6.5]` | smoothing 호출 + 배포 renorm |
| `service/backtest/walk_forward_engine.py` | walk-forward 백테스트 | 인라인 blend → 공통 함수, prune 추가, `min_weight` 파라미터 |
| `config.py` | 비즈니스 파라미터 | `turnover_min_weight` 추가 |
| `main.py` | CLI | backtest `--turnover-min-weight` |

---

## Task 1: 백테스트 사전 baseline 캡처 (코드 변경 전)

백테스트 로직을 바꾸기 **전에** `combo_18_0.1` 성과를 기록한다 (Task 6에서 비교). 코드 변경 없음.

**Files:** 없음 (실행만)

- [ ] **Step 1: 현재(prune 없는) 백테스트 실행**

Run:
```bash
python main.py backtest 2009-12-31 2026-05-31 --turnover-alpha 0.1
```
Expected: 정상 종료. 콘솔에 OOS 성과(CAGR/MDD/Sharpe) 출력. 수 분 소요(전체 walk-forward). `output/walk_forward_results.csv`, `output/overfit_diagnostics.csv` 생성.

- [ ] **Step 2: baseline 사본 보관**

Run (PowerShell):
```powershell
Copy-Item output/overfit_diagnostics.csv output/overfit_diagnostics_baseline.csv
Copy-Item output/walk_forward_results.csv output/walk_forward_results_baseline.csv
```
Expected: 두 baseline 파일 생성. (Task 6 비교 후 삭제 — 커밋하지 않음.)

- [ ] **Step 3: 핵심 지표 기록**

`output/overfit_diagnostics_baseline.csv`에서 "OOS 성과 - Constrained EW"의 CAGR/MDD/Sharpe 값을 plan 실행 노트에 적어둔다 (예: Sharpe ≈ 0.99). Task 6에서 이 값과 비교한다.

커밋 없음 (baseline은 임시 검증 산출물).

---

## Task 2: smoothing.py 공통 모듈 + 단위 테스트

**Files:**
- Create: `service/pipeline/smoothing.py`
- Test: `tests/test_unit/test_smoothing.py`

- [ ] **Step 1: 실패하는 테스트 작성**

Create `tests/test_unit/test_smoothing.py`:
```python
# -*- coding: utf-8 -*-
"""service/pipeline/smoothing.py 단위 테스트."""
from __future__ import annotations

from service.pipeline.smoothing import deploy_weights, update_smoothing_memory


# ── update_smoothing_memory ───────────────────────────────────────────────

def test_memory_first_run_returns_raw():
    """prev=None 이면 raw 그대로 (첫 회차)."""
    raw = {"A": 0.5, "B": 0.5}
    assert update_smoothing_memory(raw, None, alpha=0.1, min_weight=0.01) == raw


def test_memory_alpha_one_noop():
    """alpha>=1.0 이면 prev 무시, raw 그대로."""
    raw = {"A": 0.6, "B": 0.4}
    prev = {"A": 0.2, "C": 0.8}
    assert update_smoothing_memory(raw, prev, alpha=1.0, min_weight=0.01) == raw


def test_memory_blend_renormalizes_to_one():
    """blend 후 합=1.0 (renormalize)."""
    raw = {"A": 1.0}            # current = {A}
    prev = {"B": 1.0}           # B 는 prev 에만
    mem = update_smoothing_memory(raw, prev, alpha=0.5, min_weight=0.0)
    # blend: A=0.5, B=0.5 -> 이미 합 1.0
    assert abs(sum(mem.values()) - 1.0) < 1e-9
    assert abs(mem["A"] - 0.5) < 1e-9
    assert abs(mem["B"] - 0.5) < 1e-9


def test_memory_prune_removes_small_unselected():
    """1% 미만 AND 현재 미선정 factor 제거 후 재정규화."""
    raw = {"A": 1.0}                       # current = {A}
    prev = {"A": 0.95, "C": 0.05}          # C: 현재 미선정
    # blend(a=0.1): A=0.1*1+0.9*0.95=0.955, C=0.9*0.05=0.045
    mem = update_smoothing_memory(raw, prev, alpha=0.1, min_weight=0.05)
    # C=0.045 < 0.05 AND C 미선정 -> 제거. A 만 남아 renorm -> {A:1.0}
    assert "C" not in mem
    assert abs(mem["A"] - 1.0) < 1e-9


def test_memory_current_selection_protected():
    """현재 선정 factor 는 1% 미만이어도 제거 안 됨."""
    raw = {"A": 0.5, "B": 0.5}             # current = {A, B}
    prev = {"A": 1.0}                      # B 는 prev 없음
    # blend(a=0.1): A=0.1*0.5+0.9*1.0=0.95, B=0.1*0.5=0.05
    mem = update_smoothing_memory(raw, prev, alpha=0.1, min_weight=0.10)
    # B=0.05 < 0.10 이지만 B 는 현재 선정 -> 보존
    assert "B" in mem
    assert abs(sum(mem.values()) - 1.0) < 1e-9


def test_memory_count_converges_over_rounds():
    """탈락 factor 가 결국 prune 되어 메모리 크기가 선정 크기로 수렴."""
    selection = {"A": 0.25, "B": 0.25, "C": 0.25, "D": 0.25}
    mem = {"A": 0.2, "B": 0.2, "C": 0.2, "D": 0.2, "X": 0.2}  # X: 레거시
    for _ in range(40):
        mem = update_smoothing_memory(selection, mem, alpha=0.1, min_weight=0.01)
    assert "X" not in mem            # X 는 decay 후 제거됨
    assert set(mem) == set(selection)


# ── deploy_weights ────────────────────────────────────────────────────────

def test_deploy_renormalizes_to_one():
    """현재 선정분만 추출 후 합=1.0."""
    memory = {"A": 0.5, "B": 0.3, "C": 0.2}   # C: 메모리 전용 (레거시)
    dep = deploy_weights(memory, ["A", "B"])
    assert "C" not in dep
    assert abs(sum(dep.values()) - 1.0) < 1e-9
    assert abs(dep["A"] - 0.625) < 1e-9       # 0.5 / 0.8
    assert abs(dep["B"] - 0.375) < 1e-9       # 0.3 / 0.8


def test_deploy_empty_inputs():
    """대상 없음 / 빈 메모리 -> 빈 dict."""
    assert deploy_weights({"A": 1.0}, []) == {}
    assert deploy_weights({}, ["A"]) == {}
```

- [ ] **Step 2: 테스트 실패 확인**

Run:
```bash
python -m pytest tests/test_unit/test_smoothing.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'service.pipeline.smoothing'`

- [ ] **Step 3: smoothing.py 구현**

Create `service/pipeline/smoothing.py`:
```python
# -*- coding: utf-8 -*-
"""Turnover smoothing 공통 모듈.

production mp 와 walk-forward 백테스트가 공유하는 EMA smoothing + 배포 로직.
두 곳에서 따로 구현되어 어긋났던 것을 단일 진실 공급원으로 통합한다.

- update_smoothing_memory: blend(union) -> prune(<min AND 미선정) -> renorm.
  다음 회차로 carry / history 저장되는 "메모리" (합 1.0).
- deploy_weights: 메모리를 배포 대상 factor 로 제한 후 100% renorm. 실제 배포물.
"""
from __future__ import annotations

from service.pipeline.weight_history import blend_ema


def update_smoothing_memory(
    raw: dict[str, float],
    prev: dict[str, float] | None,
    alpha: float,
    min_weight: float,
) -> dict[str, float]:
    """EMA 블렌딩 + pruning 후 재정규화된 메모리를 반환한다 (합 1.0).

    Args:
        raw: 이번 회차 optimizer 산출 가중치 (현재 선정 factor, 합 1.0 가정).
        prev: 직전 회차 메모리 (합 1.0), 첫 회차면 None.
        alpha: EMA 비율 (0 < alpha <= 1.0). raw 반영 비율.
        min_weight: prune 임계값. 현재 미선정이고 blend 비중이 이 값 미만이면 제거.

    Returns:
        다음 회차 prev 로 carry/저장할 메모리 dict (합 1.0).
        prev=None 또는 alpha>=1.0 이면 raw 사본을 그대로 반환 (no-op).
    """
    blended = blend_ema(raw, prev, alpha)
    if prev is None or alpha >= 1.0:
        return blended

    current = set(raw)
    # 유지 조건 = NOT(prune). prune = (w < min_weight) AND (현재 미선정).
    kept = {f: w for f, w in blended.items() if w >= min_weight or f in current}
    total = sum(kept.values())
    if total <= 0:
        return dict(raw)
    return {f: w / total for f, w in kept.items()}


def deploy_weights(
    memory: dict[str, float],
    factors: list[str] | set[str],
) -> dict[str, float]:
    """메모리를 배포 대상 factor 로 제한한 뒤 100% 재정규화한다 (합 1.0).

    Args:
        memory: update_smoothing_memory 결과.
        factors: 배포 대상 factor (production: 현재 선정 / 백테스트: 현재선정 ∩ OOS 가용).

    Returns:
        실제 배포 가중치 dict (합 1.0). 대상이 없거나 합 0이면 빈 dict.
    """
    sub = {f: memory[f] for f in factors if f in memory}
    total = sum(sub.values())
    if total <= 0:
        return {}
    return {f: w / total for f, w in sub.items()}
```

- [ ] **Step 4: 테스트 통과 확인**

Run:
```bash
python -m pytest tests/test_unit/test_smoothing.py -v
```
Expected: PASS (8 passed)

- [ ] **Step 5: 커밋**

```bash
git add service/pipeline/smoothing.py tests/test_unit/test_smoothing.py
git commit -m "feat(smoothing): blend+prune+renorm 메모리 / deploy 공통 모듈 추가

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: weight_history save 함수에 deployed_weight 컬럼 추가 (optional)

기존 호출/테스트를 깨지 않도록 `deployed_weights`를 **optional 키워드 인자**로 추가한다. 제공되지 않으면 `deployed_weight` 컬럼은 출력하지 않는다(기존 동작 유지).

**Files:**
- Modify: `service/pipeline/weight_history.py`
- Test: `tests/test_unit/test_weight_history.py`

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_unit/test_weight_history.py` 의 `# ── _build_factor_style_df ──` 섹션 끝(line 181, `test_build_df_within_style_zero_total` 다음)에 추가:
```python
def test_build_df_deployed_weight_column():
    """deployed_weights 제공 시 deployed_weight 컬럼이 new_weight 뒤에 추가."""
    raw = {"A": 0.5, "B": 0.5}
    prev = {"A": 0.5, "C": 0.5}
    new = {"A": 0.5, "B": 0.05, "C": 0.45}       # 메모리 (C 는 레거시)
    deployed = {"A": 0.9091, "B": 0.0909}        # 현재선정(A,B) renorm, 합 1.0
    style_map = {"A": "Value", "B": "Value", "C": "Momentum"}

    df = _build_factor_style_df(raw, prev, new, style_map, deployed_weights=deployed)

    assert list(df.columns) == [
        "factor", "style", "raw_weight", "prev_weight", "new_weight",
        "deployed_weight", "weight_within_style",
    ]
    # C 는 배포 안 됨 -> deployed_weight 0
    assert df.loc[df["factor"] == "C", "deployed_weight"].iloc[0] == 0.0
    assert abs(df.loc[df["factor"] == "A", "deployed_weight"].iloc[0] - 0.9091) < 1e-9


def test_build_df_no_deployed_keeps_legacy_columns():
    """deployed_weights 미제공 시 컬럼 구성 불변 (하위호환)."""
    raw = {"A": 0.6, "B": 0.4}
    new = {"A": 0.6, "B": 0.4}
    style_map = {"A": "Value", "B": "Momentum"}

    df = _build_factor_style_df(raw, None, new, style_map)

    assert "deployed_weight" not in df.columns
```

`# ── save_factor_styles ──` 섹션에 추가:
```python
def test_save_factor_styles_with_deployed():
    """deployed_weights 제공 시 CSV 에 deployed_weight 컬럼 포함."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        history = Path(tmp)
        save_factor_styles(
            history, "2026-05-31",
            raw_weights={"A": 0.5, "B": 0.5},
            prev_weights={"A": 0.5, "C": 0.5},
            new_weights={"A": 0.5, "B": 0.05, "C": 0.45},
            style_map={"A": "Value", "B": "Value", "C": "Momentum"},
            deployed_weights={"A": 0.9091, "B": 0.0909},
        )
        df = pd.read_csv(history / "factor_styles_2026-05-31.csv")
        assert "deployed_weight" in df.columns
```

- [ ] **Step 2: 테스트 실패 확인**

Run:
```bash
python -m pytest tests/test_unit/test_weight_history.py::test_build_df_deployed_weight_column tests/test_unit/test_weight_history.py::test_save_factor_styles_with_deployed -v
```
Expected: FAIL — `_build_factor_style_df() got an unexpected keyword argument 'deployed_weights'`

- [ ] **Step 3: `_build_factor_style_df` 수정**

`service/pipeline/weight_history.py` 의 `_build_factor_style_df` (line 92~129) 시그니처와 본문 수정. 현재:
```python
def _build_factor_style_df(
    raw_weights: dict[str, float],
    prev_weights: dict[str, float] | None,
    new_weights: dict[str, float],
    style_map: dict[str, str],
) -> pd.DataFrame:
```
→ 다음으로 변경 (시그니처에 `deployed_weights` 추가):
```python
def _build_factor_style_df(
    raw_weights: dict[str, float],
    prev_weights: dict[str, float] | None,
    new_weights: dict[str, float],
    style_map: dict[str, str],
    deployed_weights: dict[str, float] | None = None,
) -> pd.DataFrame:
```
그리고 `df = pd.DataFrame({...})` 블록(line 112~122)에서 `"new_weight"` 항목 **다음에** deployed 컬럼을 조건부로 넣기 위해, DataFrame 생성을 다음으로 교체:
```python
    data = {
        "factor": factors,
        "style": [style_map.get(f, "(unmapped)") for f in factors],
        "raw_weight": [float(raw_weights.get(f, 0.0)) for f in factors],
        "prev_weight": (
            [float(prev_weights.get(f, 0.0)) for f in factors]
            if prev_weights is not None
            else [float("nan")] * len(factors)
        ),
        "new_weight": [float(new_weights.get(f, 0.0)) for f in factors],
    }
    if deployed_weights is not None:
        data["deployed_weight"] = [float(deployed_weights.get(f, 0.0)) for f in factors]

    df = pd.DataFrame(data)
```
(이후 `weight_within_style` 계산과 정렬 로직은 그대로 둔다. dict 삽입 순서상 `deployed_weight` 는 `new_weight` 뒤, `weight_within_style` 앞에 위치.)

- [ ] **Step 4: `save_factor_styles` 시그니처에 deployed_weights 전달**

`save_factor_styles` (line 132~162) 시그니처에 `deployed_weights: dict[str, float] | None = None` 추가(`style_map` 다음), 본문의 `_build_factor_style_df(raw_weights, prev_weights, new_weights, style_map)` 호출을 다음으로 교체:
```python
    df = _build_factor_style_df(raw_weights, prev_weights, new_weights, style_map, deployed_weights)
```

- [ ] **Step 5: `save_style_totals` 에 deployed_weight 합계 컬럼 추가**

`save_style_totals` (line 165~212) 시그니처에 `deployed_weights: dict[str, float] | None = None` 추가(`style_map` 다음). 본문에서 `factor_df = _build_factor_style_df(...)` 호출에 `deployed_weights` 전달:
```python
    factor_df = _build_factor_style_df(raw_weights, prev_weights, new_weights, style_map, deployed_weights)
```
그리고 style별 row 생성 루프(line 190~205)에서 `rows.append({...})` 직전에 deployed 합을 계산하고 dict 에 조건부로 추가:
```python
    for style, sub in grouped:
        active = sub[sub["new_weight"] > 0]
        row = {
            "style": style,
            "raw_weight": sub["raw_weight"].sum(),
            "prev_weight": (
                sub["prev_weight"].sum() if not sub["prev_weight"].isna().all() else float("nan")
            ),
            "new_weight": sub["new_weight"].sum(),
            "delta": (
                sub["new_weight"].sum() - sub["prev_weight"].sum()
                if not sub["prev_weight"].isna().all() else float("nan")
            ),
            "factor_count": int(len(active)),
            "factors": ";".join(active["factor"].tolist()),
        }
        if deployed_weights is not None:
            row["deployed_weight"] = float(sub["deployed_weight"].sum())
        rows.append(row)
```
(`sub["deployed_weight"]` 는 Step 3에서 `_build_factor_style_df` 가 deployed 제공 시 추가한 컬럼.)

- [ ] **Step 6: 테스트 통과 확인 (신규 + 기존 전부)**

Run:
```bash
python -m pytest tests/test_unit/test_weight_history.py -v
```
Expected: PASS (기존 테스트 전부 + 신규 3개). 기존 테스트는 `deployed_weights` 미전달이라 컬럼 불변 → 영향 없음.

- [ ] **Step 7: 커밋**

```bash
git add service/pipeline/weight_history.py tests/test_unit/test_weight_history.py
git commit -m "feat(weight_history): save 함수에 deployed_weight 컬럼(optional) 추가

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: production model_portfolio [6.5] 배선

`[6.5]` 단계를 공통 함수로 교체하고 **배포 가중치를 renormalize**한다(현 버그 수정). 메모리(pruned)를 prev로 저장하고, save 함수에 deployed 전달.

**Files:**
- Modify: `service/pipeline/model_portfolio.py:45-52` (import)
- Modify: `service/pipeline/model_portfolio.py:178-209` (`[6.5]` 블록)

- [ ] **Step 1: import 교체**

`service/pipeline/model_portfolio.py` line 45~52 의 import 블록:
```python
from service.pipeline.weight_history import (
    blend_ema,
    load_prev_factor_weights,
    save_factor_styles,
    save_factor_weights,
    save_style_totals,
)
```
→ 다음으로 교체:
```python
from service.pipeline.smoothing import deploy_weights, update_smoothing_memory
from service.pipeline.weight_history import (
    load_prev_factor_weights,
    save_factor_styles,
    save_factor_weights,
    save_style_totals,
)
```

- [ ] **Step 2: `[6.5]` 블록 교체**

`service/pipeline/model_portfolio.py` line 178~209 (`weights_tbl = sim_result[1]` 부터 `self.weights = sim_result[1]` 직전까지):
```python
        weights_tbl = sim_result[1]
        raw_weights = dict(zip(weights_tbl["factor"], weights_tbl["fitted_weight"]))
        alpha = float(self.pipeline_params.get("turnover_smoothing_alpha", 1.0))

        if test_file:
            new_weights = raw_weights
            prev_weights = None
        else:
            prev_weights = load_prev_factor_weights(HISTORY_DIR, end_date) if alpha < 1.0 else None
            new_weights = blend_ema(raw_weights, prev_weights, alpha)

            if alpha >= 1.0:
                logger.info("EMA smoothing off (alpha=1.0)")
            elif prev_weights is None:
                logger.info("EMA blending skipped (no prev weights - first run)")
            else:
                logger.info("EMA blending applied (alpha=%.2f)", alpha)

            weights_tbl["fitted_weight"] = weights_tbl["factor"].map(new_weights).fillna(0.0)
            sim_result = (sim_result[0], weights_tbl)

            # full_style_map: factor_info.csv 전체 (587) 사용 -> prev 에만 있는 factor 도 매핑 가능.
            # outer style_map (line 162) 은 self.meta 기반 38 factor Series 라 별도 이름 사용.
            factor_info = pd.read_csv(self.factor_info_path)
            full_style_map = dict(zip(factor_info["factorAbbreviation"], factor_info["styleName"]))

            if alpha < 1.0:
                save_factor_weights(HISTORY_DIR, end_date, new_weights)  # EMA prev 입력용
            save_factor_styles(HISTORY_DIR, end_date, raw_weights, prev_weights, new_weights, full_style_map)
            save_style_totals(HISTORY_DIR, end_date, raw_weights, prev_weights, new_weights, full_style_map)
```
→ 다음으로 교체:
```python
        weights_tbl = sim_result[1]
        raw_weights = dict(zip(weights_tbl["factor"], weights_tbl["fitted_weight"]))
        alpha = float(self.pipeline_params.get("turnover_smoothing_alpha", 1.0))
        min_weight = float(self.pipeline_params.get("turnover_min_weight", 0.01))

        if test_file:
            # 테스트 모드: smoothing/history 저장 skip. raw 가 곧 배포 (이미 합 1.0).
            prev_weights = None
        else:
            prev_weights = load_prev_factor_weights(HISTORY_DIR, end_date) if alpha < 1.0 else None
            memory = update_smoothing_memory(raw_weights, prev_weights, alpha, min_weight)
            deployed = deploy_weights(memory, list(raw_weights.keys()))

            if alpha >= 1.0:
                logger.info("EMA smoothing off (alpha=1.0)")
            elif prev_weights is None:
                logger.info("EMA blending skipped (no prev weights - first run)")
            else:
                logger.info("EMA blending applied (alpha=%.2f), memory=%d / deployed=%d factors",
                            alpha, len(memory), len(deployed))

            # 배포: 현재 선정분만 100% 정규화 -> bbg 입력물 (gross 1.0)
            weights_tbl["fitted_weight"] = weights_tbl["factor"].map(deployed).fillna(0.0)
            sim_result = (sim_result[0], weights_tbl)

            # full_style_map: factor_info.csv 전체 사용 -> prev 에만 있는 factor 도 매핑 가능.
            factor_info = pd.read_csv(self.factor_info_path)
            full_style_map = dict(zip(factor_info["factorAbbreviation"], factor_info["styleName"]))

            if alpha < 1.0:
                save_factor_weights(HISTORY_DIR, end_date, memory)  # 다음 회차 prev (pruned memory)
            save_factor_styles(HISTORY_DIR, end_date, raw_weights, prev_weights, memory, full_style_map,
                               deployed_weights=deployed)
            save_style_totals(HISTORY_DIR, end_date, raw_weights, prev_weights, memory, full_style_map,
                              deployed_weights=deployed)
```

- [ ] **Step 3: test 모드 회귀 확인 (전후 동일해야 함)**

Run:
```bash
python main.py mp test test_data.csv
python -m pytest tests/test_unit/ -v
```
Expected: mp test 정상 종료. test 모드는 smoothing 미적용(raw 배포)이라 산출물 불변. pytest 전부 PASS.

- [ ] **Step 4: 커밋**

```bash
git add service/pipeline/model_portfolio.py
git commit -m "fix(model_portfolio): EMA 배포 가중치 renormalize + pruned 메모리 저장

배포 합 0.76 -> 1.0 (현재 선정분 renorm). smoothing 공통 모듈 사용.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: 백테스트 배선 (walk_forward_engine + config + main)

백테스트가 production과 **동일한** smoothing/deploy 로직을 쓰도록 교체(+ prune). `min_weight` 파라미터/CLI 추가.

**Files:**
- Modify: `service/backtest/walk_forward_engine.py` (import, 생성자, blend/deploy 블록)
- Modify: `config.py:35-53` (PIPELINE_PARAMS)
- Modify: `main.py` (backtest argparse + `_run_backtest`)

- [ ] **Step 1: config.py 에 turnover_min_weight 추가**

`config.py` 의 `PIPELINE_PARAMS` 에서 `"turnover_smoothing_alpha": 0.1,` 줄 **다음**에 추가:
```python
    "turnover_min_weight": 0.01,       # mp/backtest EMA 메모리 prune 임계값.
                                       # blend 비중 < 이 값 AND 현재 미선정 factor 제거 후 renorm.
```

- [ ] **Step 2: walk_forward_engine import 추가**

`service/backtest/walk_forward_engine.py` 상단 import 부에 추가:
```python
from service.pipeline.smoothing import deploy_weights, update_smoothing_memory
```

- [ ] **Step 3: 생성자에 turnover_min_weight 추가**

`WalkForwardEngine.__init__` (line 259~273) 의 시그니처에 `turnover_smoothing_alpha` 다음 인자 추가 및 속성 저장:
```python
    def __init__(
        self,
        min_is_months: int = 36,
        factor_rebal_months: int = 6,
        weight_rebal_months: int = 3,
        turnover_smoothing_alpha: float = 1.0,
        turnover_min_weight: float = 0.01,
        top_factors: int = 50,
        pipeline_params_override: dict | None = None,
    ):
        self.min_is_months = min_is_months
        self.factor_rebal_months = factor_rebal_months
        self.weight_rebal_months = weight_rebal_months
        self.turnover_smoothing_alpha = turnover_smoothing_alpha
        self.turnover_min_weight = turnover_min_weight
        self.top_factors = top_factors
        self.pipeline_params_override = pipeline_params_override
```
docstring(line 255 근처)에 `turnover_min_weight: 메모리 prune 임계값 (기본 0.01).` 한 줄 추가.

- [ ] **Step 4: blend 블록 교체**

`walk_forward_engine.py` line 477~488 (`# EMA 가중치 블렌딩` 부터 `cached_weights = {f: w / total ...}` 까지):
```python
                            # EMA 가중치 블렌딩
                            if self.turnover_smoothing_alpha >= 1.0 or cached_weights is None:
                                cached_weights = raw_new_weights
                            else:
                                alpha = self.turnover_smoothing_alpha
                                all_factors = set(raw_new_weights) | set(cached_weights)
                                blended = {
                                    f: raw_new_weights.get(f, 0) * alpha + cached_weights.get(f, 0) * (1 - alpha)
                                    for f in all_factors
                                }
                                total = sum(blended.values())
                                cached_weights = {f: w / total for f, w in blended.items()} if total > 0 else raw_new_weights
```
→ 다음으로 교체 (no-op/첫회차 처리는 update_smoothing_memory 내부가 담당):
```python
                            # EMA 블렌딩 + pruning (production mp 와 공유 로직)
                            cached_weights = update_smoothing_memory(
                                raw_new_weights, cached_weights,
                                self.turnover_smoothing_alpha, self.turnover_min_weight,
                            )
```
(바로 다음 줄 `cached_selected_factors = list(raw_new_weights.keys())` 는 그대로 유지.)

- [ ] **Step 5: deploy 블록 교체**

`walk_forward_engine.py` line 509~513 (`# 가용 팩터에 맞춰 가중치 정규화` 부터 renorm 까지):
```python
            # 가용 팩터에 맞춰 가중치 정규화
            avail_weights = {f: cached_weights[f] for f in available_factors if f in cached_weights}
            total_w = sum(avail_weights.values())
            if total_w > 0:
                avail_weights = {f: w / total_w for f, w in avail_weights.items()}
```
→ 다음으로 교체:
```python
            # 가용 팩터에 맞춰 가중치 정규화 (production mp 와 공유 로직)
            avail_weights = deploy_weights(cached_weights, available_factors)
```
(다음 줄 `oos_return = sum(oos_factor_returns[f] * avail_weights.get(f, 0) ...)` 는 그대로 유지.)

- [ ] **Step 6: main.py CLI 옵션 + 전달**

`main.py` 의 backtest argparse(line 62 `--turnover-alpha` 정의 다음)에 추가:
```python
    parser_backtest.add_argument("--turnover-min-weight", type=float, default=0.01,
                                  help="EMA memory prune threshold (default: 0.01)")
```
`_run_backtest`(line 176~182)의 `WalkForwardEngine(...)` 생성자 호출에 인자 추가:
```python
    engine = WalkForwardEngine(
        min_is_months=args.min_is_months,
        factor_rebal_months=args.factor_rebal_months,
        weight_rebal_months=args.weight_rebal_months,
        turnover_smoothing_alpha=args.turnover_alpha,
        turnover_min_weight=args.turnover_min_weight,
        top_factors=args.top_factors,
    )
```

- [ ] **Step 7: 백테스트 test 모드 회귀 확인**

Run:
```bash
python main.py backtest test test_data.csv --min-is-months 4 --turnover-alpha 0.1
python -m pytest tests/test_unit/ -v
```
Expected: 백테스트 test 모드 정상 종료(에러 없음). pytest 전부 PASS.

- [ ] **Step 8: 커밋**

```bash
git add service/backtest/walk_forward_engine.py config.py main.py
git commit -m "refactor(backtest): smoothing 공통 모듈 사용 + memory prune (production parity)

config: turnover_min_weight=0.01 추가. backtest --turnover-min-weight CLI.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: 통합 검증 + 2026-05-31 산출물 재생성 + 데이터 커밋

**Files:** 없음 (실행/검증) + `output/`, `data/` 재생성물 커밋

- [ ] **Step 1: 전체 단위 테스트**

Run:
```bash
python -m pytest tests/test_unit/ -v
```
Expected: 전부 PASS.

- [ ] **Step 2: production mp 재실행 (2026-05-31)**

Run:
```bash
python main.py mp 2009-12-31 2026-05-31
```
Expected: 정상 종료. 콘솔 로그에 `EMA blending applied (alpha=0.10), memory=N / deployed=37 factors`. `output/`의 2026-05-31 산출물 갱신.

- [ ] **Step 3: 배포 가중치 합 = 1.0 검증**

Create 임시 `_verify_deploy.py`:
```python
import pandas as pd
fs = pd.read_csv("output/mp_weight_history/factor_styles_2026-05-31.csv")
deployed = fs["deployed_weight"]
print(f"deployed 합 = {deployed.sum():.6f}  (기대 1.0)")
print(f"deployed > 0 factor 수 = {(deployed > 0).sum()}  (기대 ~37)")
print(f"메모리(new_weight>0) factor 수 = {(fs['new_weight'] > 0).sum()}")
assert abs(deployed.sum() - 1.0) < 1e-6, "배포 합이 1.0 이 아님"
print("OK: 배포 gross = 1.0")
```
Run:
```bash
python _verify_deploy.py
```
Expected: `deployed 합 = 1.000000`, assert 통과, "OK". 그 후 `Remove-Item _verify_deploy.py`.

> 참고: 이번 회차는 4월 탈락 factor들이 아직 1% 위(blend 1회로 ~2.4%)라 메모리는 여전히 ~47개일 수 있다. **prune 효과는 이후 회차(~10개월)에 나타나며**, 이번 회차의 핵심 검증은 **배포 합 = 1.0** 이다(단위테스트가 prune 수렴을 별도 보장).

- [ ] **Step 4: 백테스트 재실행 + baseline 비교**

Run:
```bash
python main.py backtest 2009-12-31 2026-05-31 --turnover-alpha 0.1 --turnover-min-weight 0.01
```
Expected: 정상 종료.

Create 임시 `_cmp_backtest.py`:
```python
import pandas as pd
base = pd.read_csv("output/overfit_diagnostics_baseline.csv")
new = pd.read_csv("output/overfit_diagnostics.csv")

def metric(df, cat, m):
    row = df[(df["Category"] == cat) & (df["Metric"] == m)]
    return row["Value"].iloc[0] if len(row) else None

for cat, m in [("OOS 성과 - Constrained EW", "Sharpe"),
               ("OOS 성과 - Constrained EW", "CAGR"),
               ("OOS 성과 - Constrained EW", "MDD")]:
    print(f"{m:8} baseline={metric(base, cat, m)}  ->  new={metric(new, cat, m)}")
```
Run:
```bash
python _cmp_backtest.py
```
Expected: Sharpe/CAGR/MDD가 baseline과 **유의미한 차이 없음**(prune 영향 미미). Sharpe가 크게 하락(예: 0.99 -> 0.7 수준)하면 중단하고 `turnover_min_weight` 재검토. 확인 후 `Remove-Item _cmp_backtest.py, output/overfit_diagnostics_baseline.csv, output/walk_forward_results_baseline.csv`.

- [ ] **Step 5: 문서 갱신**

`research.md` 의 turnover smoothing/§6 관련 서술에 다음 반영: (a) 배포는 현재 선정분 renorm(합 1.0), (b) 메모리는 blend->prune(<turnover_min_weight AND 미선정)->renorm, (c) production·backtest 공통 `service/pipeline/smoothing.py` 사용. README.md 의 PIPELINE_PARAMS 표에 `turnover_min_weight` 행 추가.

- [ ] **Step 6: 코드/문서 커밋**

```bash
git add research.md README.md
git commit -m "docs: turnover smoothing 배포 renorm + 메모리 prune 반영

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 7: 데이터 + 재생성 산출물 커밋**

```bash
git add data/MXCN1A_factor_2026.parquet data/MXCN1A_mreturn.parquet output/
git commit -m "chore: 2026-05-31 데이터 + mp 산출물 (배포 renorm 적용)

incremental 다운로드(285 factors/561 stocks) + 수정된 smoothing 으로 재생성.
배포 가중치 합 = 1.0.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```
Expected: `git status` 깨끗(임시 검증 파일/baseline 삭제됨).

---

## Self-Review

**1. Spec coverage:**
- §2 목표 "배포 합 1.0" → Task 4 Step 2 + Task 6 Step 3 ✓
- §2 목표 "메모리 유한 유지(prune)" → Task 2 (update_smoothing_memory) + 수렴 테스트 ✓
- §2 목표 "공통 로직 공유" → Task 2 모듈 + Task 4(production)/Task 5(backtest) 호출 ✓
- §2 목표 "백테스트 재검증" → Task 1(baseline) + Task 6 Step 4 ✓
- §3.1 알고리즘(blend/prune/renorm/deploy) → Task 2 구현 + 테스트 ✓
- §3.5 config turnover_min_weight → Task 5 Step 1 ✓
- §3.6 deployed_weight 컬럼 → Task 3 ✓
- §5 검증(단위/test diff/mp재실행/backtest/pytest) → Task 3·4·5·6 ✓
- §2 비목표(배포 개수 축소 안 함) → 계획에 선정 로직 변경 없음 ✓

**2. Placeholder scan:** "적절히", "TODO", "추후" 없음. 모든 코드 step에 실제 코드 포함. 문서 갱신(Task 6 Step 5)은 구체 항목(a/b/c) 명시. ✓

**3. Type consistency:** `update_smoothing_memory(raw, prev, alpha, min_weight)` / `deploy_weights(memory, factors)` 시그니처가 Task 2 정의와 Task 4·5 호출에서 일치. `deployed_weights` 키워드가 `_build_factor_style_df`/`save_factor_styles`/`save_style_totals`에서 일관. `turnover_min_weight` 이름이 config/engine/CLI에서 일관(CLI 플래그는 `--turnover-min-weight`). ✓

**4. 의존성 순서:** Task 2(모듈) → Task 3(save 시그니처) → Task 4(production, 새 시그니처 사용) → Task 5(backtest). Task 1(baseline)은 backtest 변경(Task 5) 전. ✓
