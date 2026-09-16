"""QMind (구 BOK) CLI 진입점.

사용법:
    python main.py download 2023-01-01 2023-12-31          # 전체 다운로드
    python main.py download 2023-01-01 2023-12-31 --incremental  # 증분 다운로드
    python main.py mp 2023-01-01 2023-12-31                # Model Portfolio 생성
    python main.py mp test tests/fixtures/test_data.csv                   # 테스트 모드
    python main.py mp 2023-01-01 2023-12-31 --report       # 별첨 리포트 북만 생성
    python main.py backtest 2017-12-31 2026-03-31          # Walk-Forward 백테스트
    python main.py backtest test tests/fixtures/test_data.csv             # 백테스트 테스트 모드
    python main.py viz [YYYY-MM-DD] [--open]               # 기존 산출물 -> HTML 대시보드

README.md [1]~[7] 파이프라인 단계를 라우팅한다.
"""

import argparse
import logging
import sys

from rich.logging import RichHandler
from service.download.download_factors import run_download_pipeline
from service.pipeline.model_portfolio import run_model_portfolio_pipeline


def _split_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """mp/backtest 공통 위치 인자 해석: 'test <file>' 또는 '<start_date> <end_date>'.

    테스트: python main.py mp test tests/fixtures/test_data.csv  (소량 검증, _test 접미사)
    일반:   python main.py mp 2023-01-01 2023-12-31  (프로덕션)
    """
    if len(args.args) != 2:
        parser.error(f"{args.command} requires 'test <filename>' or '<start_date> <end_date>'")
    if args.args[0] == "test":
        args.test_file, args.start_date, args.end_date = args.args[1], None, None
    else:
        args.test_file, (args.start_date, args.end_date) = None, args.args


def main(argv: list[str] | None = None) -> int:
    """CLI 명령어를 파싱하고 적절한 파이프라인을 실행한다."""
    parser = argparse.ArgumentParser(description="Factor analysis pipeline.")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # download: SQL Server → pipeline-ready parquet — README [1]
    parser_download = subparsers.add_parser("download", help="Download raw factor data.")
    parser_download.add_argument("start_date", type=str, help="Start date in YYYY-MM-DD format.")
    parser_download.add_argument("end_date", type=str, help="End date in YYYY-MM-DD format.")
    parser_download.add_argument("--incremental", action="store_true",
                                  help="Incremental mode: download only end_date month and append to existing parquet.")
    parser_download.add_argument("--no-validate", action="store_true",
                                  help="Skip post-download validation checks.")

    # mp: pipeline-ready parquet → Model Portfolio → CSV — README [1]~[7]
    parser_report = subparsers.add_parser("mp", help="Generate Model Portfolio from downloaded data.")
    parser_report.add_argument("args", nargs="+", help="'test <filename>' or '<start_date> <end_date>'")
    parser_report.add_argument("--report", action="store_true", help="Generate report and exit.")

    # backtest: Walk-Forward 백테스트
    parser_backtest = subparsers.add_parser("backtest", help="Walk-Forward (Expanding Window) backtest.")
    parser_backtest.add_argument("args", nargs="+", help="'test <filename>' or '<start_date> <end_date>'")
    parser_backtest.add_argument("--min-is-months", type=int, default=36,
                                  help="Minimum IS period in months (default: 36)")
    parser_backtest.add_argument("--factor-rebal-months", type=int, default=6,
                                  help="Tier 1 rebalancing frequency (default: 6)")
    parser_backtest.add_argument("--weight-rebal-months", type=int, default=None,
                                  help="Tier 2 rebalancing frequency (default: config weight_rebal_months)")
    parser_backtest.add_argument("--top-factors", type=int, default=50,
                                  help="Number of top factors to select (default: 50)")
    parser_backtest.add_argument("--selection-hysteresis", type=float, default=None,
                                  help="Selection hysteresis margin in rank_score units "
                                       "(default: config PIPELINE_PARAMS['selection_hysteresis'])")
    parser_backtest.add_argument("--cluster-method", choices=["topn", "winner_median"], default=None,
                                  help="Cluster dedup method (default: config; topn=상위3->Top-N, "
                                       "winner_median=1등보호+중위값바닥)")
    parser_backtest.add_argument("--style-cap", type=float, default=None,
                                  help="Style 합계 상한 (default: config 0.25; 1.0=캡 해제)")
    parser_backtest.add_argument("--is-window-months", type=int, default=None,
                                  help="롤링 IS 윈도우 개월 수 (default: config is_window_months; 0=expanding)")

    # viz: 기존 output CSV -> 인터랙티브 HTML 대시보드 (read-only, 파이프라인 미수정)
    parser_viz = subparsers.add_parser("viz", help="Generate interactive HTML dashboard from existing outputs.")
    parser_viz.add_argument("end_date", nargs="?", default=None,
                            help="Snapshot date YYYY-MM-DD (default: latest available snapshot)")
    parser_viz.add_argument("--open", dest="open_browser", action="store_true",
                            help="Open the generated HTML in the default browser after generating.")

    args = parser.parse_args(argv)
    if args.command in ("mp", "backtest"):
        _split_args(parser, args)

    if args.command not in ("download", "mp", "backtest", "viz"):
        parser.print_help()
        return 1

    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler()],
    )

    if args.command == "download":
        run_download_pipeline(
            args.start_date, args.end_date,
            incremental=args.incremental,
            validate=not args.no_validate,
        )
    elif args.command == "mp":
        run_model_portfolio_pipeline(
            args.start_date, args.end_date,
            report=args.report,
            test_file=args.test_file,
        )
    elif args.command == "backtest":
        _run_backtest(args)
    elif args.command == "viz":
        _run_viz(args)

    return 0


def _run_viz(args):
    """viz 커맨드: 기존 output CSV -> 인터랙티브 HTML 대시보드 (read-only)."""
    from service.report.dashboard import build_dashboard

    out_path = build_dashboard(end_date=args.end_date)
    # cp949 콘솔 호환: ASCII 만 사용
    print(f"Dashboard saved to {out_path}")
    if getattr(args, "open_browser", False):
        import webbrowser

        webbrowser.open(out_path.resolve().as_uri())


def _run_backtest(args):
    """backtest 커맨드 실행."""
    from config import PIPELINE_PARAMS
    from service.backtest.overfit_diagnostics import (
        generate_overfit_report,
        serialize_diagnostics_csv,
    )
    from service.backtest.walk_forward_engine import WalkForwardEngine
    from service.paths import OUTPUT_DIR, dated
    from service.report.reporting import print_overfit_report

    # CLI 미지정 시 config 값 사용 (production parity)
    selection_hysteresis = (
        args.selection_hysteresis if args.selection_hysteresis is not None
        else float(PIPELINE_PARAMS.get("selection_hysteresis", 0.0))
    )

    override = {}
    if args.cluster_method:
        override["cluster_method"] = args.cluster_method
    if args.style_cap is not None:
        override["style_cap"] = args.style_cap
    override = override or None
    engine = WalkForwardEngine(
        min_is_months=args.min_is_months,
        factor_rebal_months=args.factor_rebal_months,
        weight_rebal_months=(args.weight_rebal_months if args.weight_rebal_months is not None
                             else int(PIPELINE_PARAMS.get("weight_rebal_months", 3))),
        top_factors=args.top_factors,
        selection_hysteresis=selection_hysteresis,
        pipeline_params_override=override,
        # CLI 미지정 시 config 값 (production parity). 0 지정 시 expanding 강제.
        is_window_months=(args.is_window_months if args.is_window_months is not None
                          else PIPELINE_PARAMS.get("is_window_months")) or None,
    )

    result = engine.run(args.start_date, args.end_date, test_file=getattr(args, "test_file", None))

    # 결과 저장 — 파일명 기준일 = 마지막 OOS 월 (backtest CLI 날짜는 엔진이 무시)
    as_of = result.oos_returns.index.max() if len(result.oos_returns) else None
    result.to_csv(str(dated(OUTPUT_DIR / "walk_forward_results.csv", as_of)))

    # 팩터 가중치 이력 직렬화 (viz 대시보드의 비중 추이/회전율용; 가산적, 기존 출력 불변)
    if not result.weight_history.empty:
        result.weight_history.to_csv(dated(OUTPUT_DIR / "walk_forward_weight_history.csv", as_of))

    # 팩터 기여도 이력 (비중 x 당월수익, 행합 = cew_return) — viz 성과 원천 섹션용
    if not result.contribution_history.empty:
        result.contribution_history.to_csv(dated(OUTPUT_DIR / "factor_contrib.csv", as_of))

    # 과적합 진단 (full_period_cagr은 마지막 Tier 2 시점의 IS MP CAGR)
    oos_report = generate_overfit_report(result, full_period_cagr=result.is_full_period_cagr)
    print_overfit_report(oos_report)

    # 진단 결과 CSV 저장 (세로형: Category/Metric/Value/Interpretation) — 직렬화는 도메인 모듈로 위임
    serialize_diagnostics_csv(oos_report, dated(OUTPUT_DIR / "overfit_diagnostics.csv", as_of))

    # 진단 표 포함 대시보드 자동 생성 (read-only viz). 실패해도 백테스트 산출물은 보존.
    # end_date=None: 최신 스냅샷 자동 선택. backtest CLI 날짜(args.end_date)는 production
    # parquet 에서 무시되므로(항상 전체 데이터), 이를 넘기면 대시보드가 그 옛 날짜의
    # 낡은 weights 스냅샷을 집어 실제 데이터월과 어긋난다.
    try:
        from service.report.dashboard import build_dashboard
        dash_path = build_dashboard(end_date=None)
        logging.getLogger(__name__).info("Dashboard generated: %s", dash_path)
    except Exception as e:  # viz 실패가 백테스트 결과를 무효화하지 않도록 격리
        logging.getLogger(__name__).warning("Dashboard generation skipped (%s)", e)


if __name__ == "__main__":
    sys.exit(main())
