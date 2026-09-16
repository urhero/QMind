# {실험명} A/B 실험 ({YYYY-MM-DD})

<!-- 파일명: {slug}_{YYYYMMDD}.md. 이 템플릿은 형식 강제가 아니라 채택 전
     검증 누락 방지용 — 섹션은 자유롭게 조정하되 체크리스트는 반드시 거칠 것. -->

- 브랜치: `{branch}`
- 러너: `{research/xxx.py 또는 CLI 명령}`
- 판정: **채택 / 기각 / 보류** — 한 줄 근거

## 아이디어

가설(왜 될 것 같은가) + 구현 위치(파일/config 키) + off 시 기존과 byte-identical 여부.

## 결과 (walk-forward OOS, 동일 조건 A/B)

| 케이스 | CAGR | MDD | Sharpe | Calmar |
|--------|------|-----|--------|--------|
| baseline (현행) | | | | |
| 변경안 | | | | |

하위구간 분해(전반/후반 또는 연도별)도 확인 — 전체 지표만으로 판정하지 말 것.

## 채택 전 검증 체크리스트 (교훈 박제 — 건너뛰면 재발)

- [ ] **mp_level 실측** (`research/mp_level_cost_backtest.py`): factor-level 성과 회계는
      고회전 구성에서 실비용을 과소계상한다. 정본 성과 판단은 항상 실측 기준.
      (근거: mp_level_cost_20260703.md)
- [ ] **가중 알고리즘 변경 시 RC/비중 분포 검증**: 비중 상위 집중도, ~0 비중 팩터 수,
      리스크 기여 분포 확인. 좋아 보이는 숫자가 붕괴 아티팩트일 수 있다.
      (근거: mxwo_sharpe_ladder_20260729.md "0.564 철회")
- [ ] **byte-diff**: 스위치 off 시 기존 산출물과 byte-identical 확인
      (`aggregated_weights_*`, `weights_factor_*`, `meta_data.csv`)
- [ ] **pytest 통과**: `python -m pytest tests/test_unit/ -v`
- [ ] **독립 재실행 재현**: 채택 근거 수치는 1회 실행으로 확정하지 말 것
      (근거: ts_mom_window 재검증 2026-08-07)

## 채택 시 후속

- [ ] config.py 기본값 변경 + 키 주석에 채택 근거·날짜 기입
- [ ] README.md / research.md 해당 섹션 갱신
- [ ] Claude 메모리 갱신 (채택이든 기각이든 — 기각 사유가 더 비싸다)
