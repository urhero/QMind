# CLAUDE.MD - QMind

## Project Overview

팩터 기반 포트폴리오 구성 파이프라인 (구 이름 BOK — 원래 MXCN1A 전용으로 만들었다가 MXWO 로 확장, 2026-09-09 QMind 로 개명). 200+ 금융 팩터를 SQL Server에서 가져와 5분위 분석, 팩터 랭킹, style_cap 재분배를 거쳐 Constrained EW(CEW) 종목별 비중을 산출한다. 공분산/리스크 모델 기반 최적화는 포함하지 않는다 (커밋 `8dfb64e`에서 제거).

## 외부 맥락 (코드에 없는 것)

- **Bloomberg 연동:** 최종 output CSV는 Bloomberg Optimizer에 직접 입력하여 실행


## 컨벤션

- **코드:** 영문 (변수명, 함수명)
- **문서/주석:** 한글 (투자/금융 도메인)
- **Git (2026-09-09):** 유니버스별 개발 브랜치에서 작업 → `main` 머지. `main_mxwo`(MXWO, 워크트리 `C:\Users\IKM\QMind_mxwo`) / `main_mxcn1a`(MXCN1A, 워크트리 `C:\Users\IKM\QMind_mxcn1a`) / `main`(통합본, 기본 MXWO, `C:\Users\IKM\QMind`). `main` 은 파라미터(BENCHMARK)만 바꾸면 두 유니버스 모두 실행돼야 하고, 머지가 끝난 시점에는 세 브랜치가 같은 커밋이다. 워크트리마다 자기 `.env`(BENCHMARK 고정)를 둔다 — `load_dotenv` 가 상위 폴더 `.env` 를 주워 오므로 워크트리를 리포 안(`.claude/worktrees/`)에 만들지 말 것
- **HTML 생성:** HTML/리포트/페이지를 만들 때는 `supanova-design-engine` 스킬을 사용한다 (프리미엄 standalone HTML — Tailwind CDN + Pretendard + Iconify Solar, 다크 기본, 이모지/금지폰트 배제). 프로젝트 브랜드 컬러가 필요하면 `docs/design/DESIGN-binance.md`의 토큰을 supanova에 입력으로 넣는다 (supanova = 생성 엔진, DESIGN-binance = 선택적 브랜드 팔레트).

## 환경 설정

- `config.py`가 `.env`에서 `python-dotenv`로 로드 (DB 비밀번호 등)
- **유니버스 전환:** `.env`의 `BENCHMARK`(MXCN1A/MXWO, 없으면 `config.py` 상수 = MXWO). 이 값이 `PIPELINE_PARAMS`(공통+유니버스별), `output/{BENCHMARK}/`, `data/{BENCHMARK}/` 를 전부 결정. 일회성 덮어쓰기는 `BENCHMARK=MXWO python main.py ...` (환경변수가 `.env` 보다 우선). **로컬 `.env` 는 사용자 관리 — Claude 가 수정하지 않는다**
- 설치: `pipenv install`
- 테스트: `python -m pytest tests/test_unit/ -v`

## 코드 변경 검증 프로세스 (CRITICAL)

코드 수정 시 **반드시** 다음을 수행. 공통 코드(`service/`, `config.py` 구조)를 건드렸으면 **`BENCHMARK=MXCN1A` 와 `BENCHMARK=MXWO` 양쪽** 모두 검증한다 (산출물은 `output/{BENCHMARK}/`):

### A. 테스트 검증
1. **변경 전** 베이스라인: `python main.py mp test tests/fixtures/test_data.csv`
2. **코드 변경 적용**
3. **변경 후** 결과 생성 및 diff 비교 (같은 명령어)
4. **pytest 통과**: `python -m pytest tests/test_unit/ -v`
5. **테스트 추가/수정** (변경된 함수에 해당)

### B. 실제 데이터 검증
1. **변경 전** 베이스라인: `python main.py mp {PIPELINE_PARAMS["backtest_start"]} {PIPELINE_PARAMS["backtest_end"]}`
   - 날짜는 `config.py`의 `PIPELINE_PARAMS`에서 확인
2. **변경 후** 결과 생성 및 diff 비교 (같은 명령어)

### C. 마무리
- **문서 업데이트** (`README.md`, `research.md`, 해당 시 이 파일도)
- **실험(A/B) 문서는 `docs/experiments/_TEMPLATE.md` 기반으로 작성** — 채택 전 검증 체크리스트(mp_level 실측, RC 분포, byte-diff) 필수 경유
- 비교 대상: `output/{BENCHMARK}/test/`(mp test) 및 `output/{BENCHMARK}/<date>/`(실데이터) 의 `aggregated_weights_*`, `total_aggregated_weights_*`, `pivoted_total_agg_wgt_*`, `meta_data_*`, `walk_forward_results_*`
- A, B **모두** 변경 전후 일치해야 함 (의도된 변경 제외)

## 문서 체계

| 문서 | 역할 | 대상 독자 |
|------|------|-----------|
| `README.md` | **Quick Start + 구조 요약 + 사용법.** 처음 보는 사람이 5분 안에 전체 파이프라인을 파악할 수 있도록. Funnel 구조(200+ -> 클러스터 dedup 선정 ~30 -> weight>0), 각 단계 요약, CLI 사용법, 실행 결과 | 신규 팀원, 매니저, 외부 이해관계자 |
| `research.md` | **Deep Dive.** 코드를 건드려야 하는 사람을 위한 내부 로직, 코드 수준 구현 세부사항, 엣지케이스, 수식, 의존성, 제약사항, 기술 부채 | 개발자, 퀀트 리서처 |
| `CLAUDE.md` | Claude Code 전용 지침. 검증 프로세스, 컨벤션 | AI 어시스턴트 |

- README.md에 함수별 I/O Reference를 넣지 말 것 (research.md에 이미 상세 존재)
- 두 문서에 동일 내용이 중복되지 않도록 유지

## Notes for Claude Code

- **맥락 파악:** 전체 구조는 `README.md`, 코드 수준 세부사항은 `research.md`를 읽을 것
- **Entry Point:** `main.py` -> `service/pipeline/model_portfolio.py:run_model_portfolio_pipeline()`
- **Backtest Entry:** `main.py` -> `service/backtest/walk_forward_engine.py:WalkForwardEngine.run()`
- **Data I/O:** `service/download/parquet_io.py`의 `load_factor_parquet()`가 연도별 분할 parquet 자동 병합
- **Test Mode:** `python main.py mp test tests/fixtures/test_data.csv` (소량 데이터 검증용, `_test` suffix 출력)
- **산출물 경로:** 항상 `service.paths.OUTPUT_DIR`(= `output/{BENCHMARK}/`) 사용. 루트 `output/` 하드코딩 금지. 유니버스 종속 데이터 파일은 `service.paths.UNIVERSE_DATA_DIR`(= `data/{BENCHMARK}/`) 로 조회, 공통 파일(`factor_info.csv`)만 `DATA_DIR`
- **Flow 참조:** `docs/VARIABLE_FLOW.md`
- **성능:** vectorized 연산 유지, 루프 도입 금지 선호
- **OOS 오염 주의:** Walk-Forward 엔진에서 IS 규칙(dropped_sectors, label_rules)을 전체 데이터에 적용할 때 `filter_and_label_factors()`를 재실행하면 OOS look-ahead bias 발생. 반드시 `rule_bundle`의 IS 전용 규칙을 직접 매핑할 것
- **cp949 호환:** Windows Rich 콘솔 출력에서 em dash, 화살표 등 유니코드 특수문자 사용 금지. ASCII 대체 사용 (`->`, `-`, `~=`)
