# 유니버스별 개발 브랜치 워크플로 + 기본 유니버스 MXWO 전환 + QMind 개명 (2026-09-09)

## 배경

- 2026-09-02 통합 이후 `main` 하나가 `BENCHMARK` 스위치로 MXCN1A/MXWO 를 모두 실행한다.
- 개발 흐름은 여전히 `main-ikm` -> `main` 단일 라인이라 두 유니버스 작업이 한 브랜치에서 섞였다.
- 프로젝트는 원래 MXCN1A 전용(BOK)으로 만들어 MXWO 로 확장한 것이라, 이름과 기본값이 실제 주 사용 유니버스(MXWO)와 어긋나 있었다.

## 결정 (사용자 확정)

| 항목 | 결정 |
|---|---|
| 저장소 | GitHub 저장소는 하나(`urhero/bok` -> `urhero/QMind` 개명). 별도 저장소 3개는 기각 — 데이터/CI/.env 3벌 + 수동 동기화 |
| 브랜치 | `main`(통합본) / `main_mxwo`(MXWO 개발) / `main_mxcn1a`(MXCN1A 개발). 개발 브랜치 -> `main` 머지. 머지 완료 시점에는 셋이 같은 커밋 |
| `718708` | 브랜치/코드 어디에도 넣지 않음 (사용자: "그냥 MXCN1A 로") |
| 기본 유니버스 | `config.py` 폴백 상수 MXCN1A -> **MXWO**. `.env` 가 없는 CI 가 이 경로를 탄다 |
| 워크트리 | 형제 폴더 `C:\Users\IKM\QMind_mxwo`, `C:\Users\IKM\QMind_mxcn1a`. 각자 `.env` 에 BENCHMARK 고정. 리포 안(`.claude/worktrees/`)에 두면 `load_dotenv` 가 상위 `.env` 를 주워 오므로 금지 |
| 루트 `.env` | BENCHMARK 줄만 MXCN1A -> MXWO 토글 (사용자 명시 지시) |
| `main-ikm` | 로컬+원격 삭제 (main 과 동일 커밋, 새 워크플로에서 역할 없음) |
| 구 브랜치 12개 | 이번엔 손대지 않음 |
| BOK 문구 | 프로젝트명 문구 7곳 QMind 로 치환, "구 BOK, 원래 MXCN1A 전용" 유래 병기. 코드 식별자/데이터 접두어/출력 폴더는 MXCN1A 그대로 |
| 로컬 루트 폴더 | `C:\Users\IKM\bok` -> `QMind` 는 세션 종료 후 사용자가 직접 (절차는 아래) |

## 변경 파일

- `config.py`: 폴백 `"MXWO"` + 주석
- `.env.example`: MXWO 블록 활성, MXCN1A 주석
- `README.md`: 뱃지 URL, 유래 문단, 유니버스 전환 예시 MXWO, 브랜치/워크트리 표
- `CLAUDE.md`: 제목, 유래, Git 컨벤션(브랜치 3개 + 워크트리 `.env` 규칙)
- `research.md`: 제목, 유래, §1.1 폴백 설명
- `main.py`, `tests/__init__.py`, `tests/conftest.py`, `utils/__init__.py`, `research/build_playground.py`: BOK -> QMind 문구
- `tests/test_unit/test_config_universe.py`: `test_default_benchmark_is_mxwo` 추가 (BENCHMARK="" 로 미설정 재현; `load_dotenv` 는 기존 키를 덮지 않음)

## 검증

- pytest `BENCHMARK=MXCN1A` / `MXWO` 각 330 통과
- `mp test test_data.csv` 양쪽 산출물 10개 변경 전후 byte-identical (환경변수 명시 -> 폴백 상수 무관, 예상대로)
- 전체 백테스트는 생략: 로직 변경 없음(폴백 상수 + 문자열)

## 로컬 루트 폴더 rename 절차 (사용자 수행)

1. `C:\Users\IKM\bok` 을 연 Claude Code 세션/IDE/탐색기 전부 종료
2. PowerShell: `Rename-Item C:\Users\IKM\bok C:\Users\IKM\QMind`
3. `cd C:\Users\IKM\QMind; git worktree repair; git worktree repair .claude\worktrees\mxcn-aug-2026-performance-ba3e50 .claude\worktrees\mxwo-august-2026-performance-3ebd3f` (형제 워크트리 2개는 첫 명령이, 리포 안 클로드 워크트리 2개는 둘째 명령이 재연결)
4. Claude 메모리/세션 폴더 복사: `Copy-Item -Recurse C:\Users\IKM\.claude\projects\C--Users-IKM-bok C:\Users\IKM\.claude\projects\C--Users-IKM-QMind`
5. pipenv 가상환경은 `C:\Users\IKM\.virtualenvs\bok-ZkEKkwfv` 절대경로로 계속 실행 (이름은 그대로여도 무방)
6. 확인: `git status`, `git worktree list`, `python main.py mp test test_data.csv`
