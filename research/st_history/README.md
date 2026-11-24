# ST·*ST 이력 (CNINFO 공시 기반, 2026-11-24)

결과: `data/MXCN1A/risk/st_intervals.csv` — `code, grade(ST|*ST), start, end, start_src, end_src, repair`.
보조 파일 (git 추적, `.gitignore` 예외 `!data/*/st_history/`): `data/MXCN1A/st_history/` — `st_intervals_raw.csv`(가격 보정 전), `st_events_audit.csv`(공시별 판정), `cninfo_raw.jsonl.gz`(수집 원본 11,098건).
설계·품질 수치: `docs/superpowers/specs/2026-11-24-mxcn1a-risk-model-design.md` §3.4.

## 순서

| 단계 | 파일 | 하는 일 |
|---|---|---|
| 1 | `collect.py` (+ `q.py`) | CNINFO `hisAnnouncement/query` 를 월 단위·키워드("风险警示", "特别处理")로 전수 수집 → `raw.jsonl` (이어서 실행 가능) |
| 2 | `classify.py`, `mkev.py` | 제목 → 지정/해제/유지 판정 → `events.parquet` |
| 3 | `pdfdates.py events.parquet` | 이벤트 공시 PDF 텍스트 저장(`texts/`) → 시행일·새 약칭 → `pdf_meta.jsonl`. `--parse-only` 는 다운로드 없이 재파싱 |
| 4 | `build.py`, `curcheck.py` | 종목별 구간 생성 → `st_intervals.csv`, `st_events_audit.csv`; CNINFO 현재 약칭(`szse_stock.json`)과 대조 |
| 5 | `st_repair.py raw.csv out.csv` | 주판 가격제한 패턴으로 시작·종료일 보정 (2026-07-06 이전만) |
| 6 | `st_validate.py iv.csv` | 주판 ±5% 고정 상하한가 / 구간 안 대폭 변동으로 누락·오탐 점검 |

## 그대로는 안 도는 것 (이관 시 고칠 것)

- 1~4 는 Linux 클라우드 환경에서 실행했다. 회사 PC 에서 CNINFO 접속 여부 미확인.
- 5·6 은 임시 캐시(`px/*.parquet` = Compustat `sec_dprc` 연도별 추출, `code_map.parquet` = `ciqGvkeyIID`→6자리 코드)를 읽는다. 리포에서는 `db/daily_read.daily_prices()`(main 머지 후)와 설계 §2 "종목 코드" 규칙으로 바꾼다.
- `prcstd=5` 에 휴장일이 섞여 있다는 사실(설계 §3.1)을 반영하기 전에 만든 코드다 — 휴장 다음 날을 "재개일"로 제외하므로 판정 표본이 줄 뿐 방향은 같지만, 이관 때 거래일 달력 기준으로 고친다.
- `collect.py` 는 중국어 키워드를 콘솔에 출력한다 → Windows cp949 콘솔 규칙(CLAUDE.md)에 맞게 출력부를 ASCII 로 바꿀 것.
