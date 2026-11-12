from __future__ import annotations

import argparse
import sys

from rich.console import Console
from rich.table import Table

from app.config import load_config


console = Console(force_terminal=True, soft_wrap=True)


def cmd_verify(args: argparse.Namespace) -> int:
    """알람 없이 한 번 받아서 RSI / 이평 이격을 표로 확인."""
    from app.config import MA_LINES
    from app.market import MarketData
    from app.signals import compute_snapshot, fmt_price
    from app.timeframes import TIMEFRAMES

    config = load_config(args.config)
    market = MarketData(log=lambda m: console.print(m, markup=False))
    try:
        u = config.universe
        if u.enabled and args.binance > 0:
            symbols = market.binance.top_symbols(u.top_n, u.include_tradfi, u.exclude_bases)
            console.print(f"바이낸스 상위 {len(symbols)}개: " + ", ".join(s.symbol for s in symbols))
            added, _ = market.set_binance_universe(symbols[: args.binance])
            market.bootstrap_binance(added, lambda: False)
        if config.macro.enabled and not args.no_macro:
            added, _ = market.set_macro_assets(config.macro.assets)
            market.bootstrap_macro(added)

        table = Table(title="RSI / 이평 이격% (형성 중 봉 포함)", show_lines=True)
        table.add_column("자산")
        table.add_column("가격", justify="right")
        for tf in TIMEFRAMES:
            table.add_column(tf)
        for a in market.assets():
            price = "-"
            cells = []
            for tf in TIMEFRAMES:
                bars = market.bars(a, tf)
                snap = compute_snapshot(bars, config)
                if snap is None:
                    cells.append("데이터 없음")
                    continue
                price = fmt_price(snap.price)
                rows = [f"{len(bars)}봉", f"RSI {snap.rsi:.1f}" if snap.rsi is not None else "RSI -"]
                for key, _period in MA_LINES:
                    d = snap.dist_pct(key)
                    name = snap.labels[key]
                    rows.append(f"{name} {d:+.2f}%" if d is not None else f"{name} -")
                cells.append("\n".join(rows))
            table.add_row(a.display, price, *cells)
        console.print(table)
        return 0
    finally:
        market.close()


def cmd_run(args: argparse.Namespace) -> int:
    """상시 모니터 (Ctrl+C 종료)."""
    from app.monitor import Monitor

    config = load_config(args.config)
    Monitor(config, config_path=args.config).start()
    return 0


def cmd_telegram_test(args: argparse.Namespace) -> int:
    """텔레그램 연동 테스트 메시지 전송."""
    from app.alerts import TelegramNotifier
    from app.settings import get_settings

    settings = get_settings()
    if not settings.telegram_enabled:
        console.print(
            "[red].env에 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 를 먼저 넣으세요.[/red]"
        )
        console.print("예: .env.example 을 복사해 .env 로 만든 뒤 값을 채웁니다.")
        return 1

    notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    try:
        notifier.send("stock-monitor 텔레그램 연동 테스트 OK")
        console.print("[green]Telegram 테스트 메시지 전송 성공[/green]")
        return 0
    except Exception as exc:
        console.print(f"[red]Telegram 전송 실패:[/red] {exc}")
        return 1
    finally:
        notifier.close()


def cmd_ui(args: argparse.Namespace) -> int:
    """PC용 Start/Stop UI."""
    from app.ui import run_ui

    return run_ui(args.config)


def cmd_config_web(args: argparse.Namespace) -> int:
    """모바일/PC용 설정 웹 서버."""
    from app.web.__main__ import main as web_main

    web_main()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stock-monitor",
        description="바이낸스 선물 상위 종목 + 큰 자산 RSI / 이평 근접 텔레그램 알람",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="config.yaml 경로 (기본: 프로젝트 루트/config.yaml)",
    )

    sub = parser.add_subparsers(dest="command", required=False)

    ui = sub.add_parser("ui", help="PC UI 실행 (기본)")
    ui.set_defaults(func=cmd_ui)

    verify = sub.add_parser("verify", help="알람 없이 한 번 조회해 RSI/이평 이격 표 출력")
    verify.add_argument("--binance", type=int, default=5, help="표에 넣을 바이낸스 상위 종목 수 (0이면 생략)")
    verify.add_argument("--no-macro", action="store_true", help="야후 자산 생략")
    verify.set_defaults(func=cmd_verify)

    run = sub.add_parser("run", help="상시 실행(CLI) (Ctrl+C 종료)")
    run.set_defaults(func=cmd_run)

    tg = sub.add_parser("telegram-test", help="텔레그램 테스트 메시지 전송")
    tg.set_defaults(func=cmd_telegram_test)

    web = sub.add_parser("config-web", help="비밀번호 보호 설정 웹 (모바일/PC)")
    web.set_defaults(func=cmd_config_web)

    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        argv = ["ui"]

    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        args = parser.parse_args(["ui"])
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
