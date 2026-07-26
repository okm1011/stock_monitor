from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from app.config import load_config
from app.models import Quote
from app.service import fetch_all


console = Console(force_terminal=True, soft_wrap=True)


def _fmt_price(price: float) -> str:
    abs_price = abs(price)
    if abs_price >= 1000:
        return f"{price:,.2f}"
    if abs_price >= 1:
        return f"{price:,.4f}"
    return f"{price:.6f}"


def _build_table(quotes: list[Quote]) -> Table:
    table = Table(title="시세 조회 결과", show_lines=False)
    table.add_column("구분", style="cyan")
    table.add_column("이름")
    table.add_column("심볼")
    table.add_column("가격", justify="right")
    table.add_column("통화")
    table.add_column("등락%", justify="right")
    table.add_column("소스")
    table.add_column("시각(UTC)")

    for q in quotes:
        change = f"{q.change_pct:+.2f}" if q.change_pct is not None else "-"
        table.add_row(
            q.asset_class.value,
            q.name,
            q.symbol,
            _fmt_price(q.price),
            q.currency,
            change,
            q.source,
            q.fetched_at.strftime("%Y-%m-%d %H:%M:%S"),
        )
    return table


def cmd_verify(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    console.print(f"[bold]config:[/bold] {Path(args.config).resolve() if args.config else 'config.yaml'}")
    console.print(
        f"감시 대상 - crypto={len(config.crypto)}, "
        f"kr={len(config.kr_stocks)}, us={len(config.us_stocks)}"
    )

    try:
        quotes = fetch_all(config)
    except Exception as exc:
        console.print(f"[red]조회 실패:[/red] {exc}")
        return 1

    if not quotes:
        console.print("[yellow]조회된 시세가 없습니다. config.yaml 심볼을 확인하세요.[/yellow]")
        return 1

    console.print(_build_table(quotes))
    console.print(f"[green]OK[/green] - {len(quotes)}건 조회 성공")

    if args.json:
        out = [q.model_dump(mode="json") for q in quotes]
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"JSON 저장: {args.json}")

    return 0


def cmd_fetch_one(args: argparse.Namespace) -> int:
    from app.fetchers.crypto import CryptoFetcher
    from app.fetchers.stocks import StockFetcher
    from app.models import AssetClass

    config = load_config(args.config)
    kind = args.kind

    if kind == "crypto":
        quotes = CryptoFetcher(config.crypto).fetch()
    elif kind == "kr":
        quotes = StockFetcher(config.kr_stocks, AssetClass.KR_STOCK, "KRW").fetch()
    elif kind == "us":
        quotes = StockFetcher(config.us_stocks, AssetClass.US_STOCK, "USD").fetch()
    else:
        console.print(f"[red]unknown kind:[/red] {kind}")
        return 1

    if not quotes:
        console.print(f"[yellow]{kind}: 결과 없음[/yellow]")
        return 1

    console.print(_build_table(quotes))
    console.print(f"[green]OK[/green] - {kind} {len(quotes)}건")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """상시 모니터: 폴링 -> 봉 저장 -> RSI -> 알림."""
    from app.monitor import Monitor

    config = load_config(args.config)
    Monitor(config).start()
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
    """설정 UI + Start/Stop."""
    from app.ui import run_ui

    return run_ui(args.config)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stock-monitor",
        description="코인/한국주식/미국주식 시세 조회 및 RSI 알림 모니터",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="config.yaml 경로 (기본: 프로젝트 루트/config.yaml)",
    )

    sub = parser.add_subparsers(dest="command", required=False)

    ui = sub.add_parser("ui", help="설정 UI 실행 (기본)")
    ui.set_defaults(func=cmd_ui)

    verify = sub.add_parser("verify", help="설정된 전체 심볼 시세를 조회해 표로 검증")
    verify.add_argument("--json", metavar="PATH", help="결과를 JSON 파일로 저장")
    verify.set_defaults(func=cmd_verify)

    one = sub.add_parser("fetch", help="자산 종류별로만 조회 (crypto|kr|us)")
    one.add_argument("kind", choices=["crypto", "kr", "us"])
    one.set_defaults(func=cmd_fetch_one)

    run = sub.add_parser("run", help="상시 실행(CLI): 폴링 + RSI 알림 (Ctrl+C 종료)")
    run.set_defaults(func=cmd_run)

    tg = sub.add_parser("telegram-test", help="텔레그램 테스트 메시지 전송")
    tg.set_defaults(func=cmd_telegram_test)

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
