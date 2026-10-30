# 포트폴리오 시각화 대시보드 설계

작성일: 2026-06-16
상태: 승인됨 (구현 진행)

## 목표

백테스트 내역과 현재 포트폴리오/배팅을 단일 인터랙티브 HTML 리포트로 시각화한다.
기존 `output/*.csv`만 읽는 **read-only** 레이어 -> 파이프라인 코드 무수정 ->
CLAUDE.md 검증 절차(before/after diff) 비발동.

## 결정 사항

- **전달 형태:** plotly 단독 HTML 1개 파일 (plotly.js 인라인 -> 오프라인 단독 열림).
  새 의존성 0 (프로젝트 venv `bok-ZkEKkwfv`에 plotly 6.5.0 설치 확인).
- **범위:** read-only 8개 차트. 섹터 분해 / 백테스트 비중-회전율 추이는 제외(파이프라인 수정 필요, 추후).
- **실행:** 새 CLI `python main.py viz [end_date]` (생략 시 최신 스냅샷 자동 탐색).

## 아키텍처 (관심사 분리)

| 파일 | 책임 | 테스트 |
|------|------|--------|
| `service/report/dashboard_data.py` | CSV -> 정돈 DataFrame + 계산(낙폭/KPI/스타일집계/상위롱숏/팩터틸트/선정셋/진단파싱). plotly 의존 없음 | 단위 테스트 대상 |
| `service/report/dashboard_charts.py` | DataFrame -> plotly Figure. 로컬 STYLE_COLORS(Volatility 포함) | 스모크 |
| `service/report/dashboard.py` | 조립 + HTML 출력(얇음). `build_dashboard(end_date=None) -> Path` | 스모크 |
| `main.py` | `viz` 서브커맨드 + `_run_viz()` 핸들러 | - |
| `tests/test_unit/test_dashboard_data.py` | 순수 함수 단위 테스트 + HTML 스모크 | - |

## 데이터 소스 (검증된 실제 스키마, 2026-06-16)

- 백테스트 곡선: `output/walk_forward_results.csv`
  컬럼: `date, cew_return, ew_return, ew_all_return, ew_top50_return, cew_cumulative, ew_cumulative, ew_all_cumulative, ew_top50_cumulative` (cumulative는 이미 (1+r).cumprod()).
- 백테스트 KPI: `output/overfit_diagnostics.csv` (utf-8-sig, 세로 Category/Metric/Value/Interpretation).
  KPI 카드는 진단 파일 우선 파싱, 없으면 곡선에서 계산(fallback).
- 현재 포트: `output/total_aggregated_weights_<date>[suffix].csv` (tidy long).
  컬럼: `(idx), ddt, ticker, isin, gvkeyiid, mp_ls_weight, ls_weight, factor_weight, factor, style, name, count, style_ls_weight`.
  - 종목 순비중 = groupby ticker sum `mp_ls_weight`.
  - 스타일 배분 = 고유 (factor->factor_weight) dedup 후 groupby style sum (합 ~= 1).
  - 팩터 틸트 = 고유 factor의 factor_weight.
  - 선정 팩터 = factor_weight > 0 인 factor 집합.
- 팩터 리더보드: `output/meta_data.csv` (tstat vs cagr, 선정여부 색).
- 전월대비 변화: `output/mp_weight_history/style_totals_<date>.csv` (delta 컬럼). 운영모드만 -> 없으면 차트 생략.

### 파일 선택 규칙

`total_aggregated_weights_*.csv` 중 `_style` 변형 제외, 파일명에서
`(\d{4}-\d{2}-\d{2})` 파싱 -> 최대 날짜(또는 인자 end_date) 선택.
동일 날짜 복수 시 mtime 최신 우선. (현 디렉토리 최신 = 2026-05-31_test, 무접미사 운영본 없음.)

## 차트 목록 (8)

백테스트: (1) KPI 카드(CAGR/MDD/Sharpe/Calmar/승률/Funnel) (2) 누적수익 4선 비교
(3) 낙폭 곡선 (4) 월별수익 분포.
현재 포트: (5) 스타일 배분(25% cap 라인) (6) 종목 상위 롱/숏 (7) 팩터 틸트
(8) 팩터 리더보드 산점도. + (조건부) 전월대비 스타일 변화.

## 출력 / 검증

- `output/dashboard_<end_date>.html`.
- 테스트: 순수 함수 단위 테스트(인라인 DF) + 파일선택/진단파싱(tmp_path) + HTML 생성 스모크.
- cp949: 콘솔 출력은 ASCII만 (`->`, `-`). 차트 내부 텍스트(plotly)는 무관.
- bbg `pivoted_total_agg_wgt_*.csv`는 읽지도/건드리지도 않음(별개 파일 출력).
