from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from app.ai.client import AIClient, AIError
from app.ai.planner import PlannerSession
from app.config import settings
from app.export.excel import rows_to_excel
from app.models import ScrapePlan
from app.scraper.engine import ScrapeEngine

console = Console()


def save_plan(plan: ScrapePlan, path: Optional[Path] = None) -> Path:
    settings.plans_dir.mkdir(parents=True, exist_ok=True)
    if path is None:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in plan.title)[:40]
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = settings.plans_dir / f"{safe or 'plan'}_{stamp}.json"
    path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_plan(path: Path) -> ScrapePlan:
    data = json.loads(path.read_text(encoding="utf-8"))
    return ScrapePlan.model_validate(data)


def show_plan(plan: ScrapePlan) -> None:
    table = Table(title=f"Scrape plan: {plan.title}")
    table.add_column("Site")
    table.add_column("URL")
    table.add_column("Method")
    table.add_column("Login")
    for site in plan.sites:
        table.add_row(
            site.name,
            site.start_url,
            site.method,
            "yes" if site.login else "no",
        )
    console.print(table)
    fields = ", ".join(f.name for f in plan.fields) or "(per-site / heuristic)"
    console.print(f"[bold]Goal:[/bold] {plan.goal}")
    console.print(f"[bold]Fields:[/bold] {fields}")
    if plan.notes:
        console.print(f"[bold]Notes:[/bold] {plan.notes}")


def cmd_chat(args: argparse.Namespace) -> int:
    try:
        client = AIClient(provider=args.provider, model=args.model)
        status = client.ping()
    except AIError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1

    session = PlannerSession(client)
    console.print(
        Panel(
            f"[green]{status}[/green]\n"
            f"Provider [cyan]{client.provider}[/cyan] / model [cyan]{client.model}[/cyan]\n"
            "Type what you want to scrape in plain English (not shell commands).\n"
            "Commands: [bold]/run[/bold], [bold]/save[/bold], "
            "[bold]/plan[/bold], [bold]/quit[/bold]",
            title="DataScraper chat",
        )
    )

    plan: Optional[ScrapePlan] = None
    while True:
        try:
            user = Prompt.ask("[bold green]you[/bold green]")
        except (EOFError, KeyboardInterrupt):
            console.print("\nBye.")
            return 0

        text = user.strip()
        if not text:
            continue
        lower = text.lower()

        # People sometimes paste setup commands into the chat prompt
        if text.startswith(("source ", "cp ", "cd ", "pip ", "ollama ", "#")) or (
            "python main.py" in text and not lower.startswith("/")
        ):
            console.print(
                "[yellow]That looks like a terminal command. "
                "Run those in your shell, not here. "
                "In this chat, describe the scrape in plain English.[/yellow]"
            )
            continue

        if lower in {"/quit", "/exit", "quit", "exit"}:
            return 0

        if lower == "/plan":
            if not plan:
                console.print("[yellow]No plan yet — keep chatting.[/yellow]")
                continue
            show_plan(plan)
            continue

        if lower.startswith("/save"):
            if not plan:
                console.print("[yellow]No plan to save yet.[/yellow]")
                continue
            path = save_plan(plan)
            console.print(f"[green]Saved plan → {path}[/green]")
            continue

        if lower in {"/run", "run"}:
            if not plan:
                console.print("[yellow]No plan yet. Chat until the bot emits a JSON plan.[/yellow]")
                continue
            return _execute_plan(plan, headless=not args.headed, assume_yes=args.yes)

        # normal chat turn
        try:
            with console.status("Thinking..."):
                reply, maybe_plan = session.ask(text)
        except AIError as exc:
            console.print(f"[red]{exc}[/red]")
            continue

        console.print(Panel(Markdown(reply), title="assistant", border_style="blue"))
        if maybe_plan:
            plan = maybe_plan
            console.print(
                "[green]Plan captured.[/green] Type [bold]/run[/bold] to scrape, "
                "[bold]/plan[/bold] to review, [bold]/save[/bold] to store."
            )
            if args.yes or Confirm.ask("Run this scrape now?", default=False):
                return _execute_plan(plan, headless=not args.headed, assume_yes=True)

    return 0


def _execute_plan(plan: ScrapePlan, headless: bool = True, assume_yes: bool = False) -> int:
    show_plan(plan)
    if not plan.sites:
        console.print("[red]Plan has no sites.[/red]")
        return 1
    if not assume_yes and not Confirm.ask("Proceed with scrape?", default=True):
        path = save_plan(plan)
        console.print(f"Plan saved for later: {path}")
        return 0

    console.print("[cyan]Running Python scrape engine...[/cyan]")
    # Always keep the plan on disk so a failed run is recoverable
    plan_path = save_plan(plan)
    console.print(f"Plan saved → {plan_path}")
    engine = ScrapeEngine(headless=headless)
    try:
        rows = engine.run(plan)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Scrape failed: {exc}[/red]")
        console.print(
            "[yellow]Tip:[/yellow] Edit the plan JSON URLs if they look like placeholders "
            f"(https://...), then: python main.py run {plan_path} -y"
        )
        return 1

    console.print(f"Collected [bold]{len(rows)}[/bold] rows")
    # Quick per-site counts (useful for "how many units")
    counts: dict = {}
    for row in rows:
        site = str(row.get("_site") or "unknown")
        counts[site] = counts.get(site, 0) + 1
    if counts:
        for site, n in counts.items():
            console.print(f"  • {site}: {n}")
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in plan.title)[:40]
    out = rows_to_excel(rows, filename=safe or None)
    console.print(f"[green]Excel written → {out}[/green]")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    path = Path(args.plan)
    if not path.exists():
        console.print(f"[red]Plan not found: {path}[/red]")
        return 1
    plan = load_plan(path)
    return _execute_plan(plan, headless=not args.headed, assume_yes=args.yes)


def cmd_providers(_: argparse.Namespace) -> int:
    console.print("Configured provider:", settings.ai_provider)
    console.print("Ollama:", settings.ollama_base_url, "/", settings.ollama_model)
    console.print(
        "Claude:",
        settings.anthropic_model,
        "(key set)" if settings.anthropic_api_key else "(no API key)",
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="datascraper",
        description="AI-planned, Python-executed scraper → Excel",
    )
    sub = parser.add_subparsers(dest="command")

    chat = sub.add_parser("chat", help="Chat with AI to draft a scrape plan, then run it")
    chat.add_argument("--provider", choices=["ollama", "claude"], default=None)
    chat.add_argument("--model", default=None, help="Override model name")
    chat.add_argument("--headed", action="store_true", help="Show browser window when used")
    chat.add_argument("-y", "--yes", action="store_true", help="Skip confirmations")
    chat.set_defaults(func=cmd_chat)

    run = sub.add_parser("run", help="Run an existing plan JSON")
    run.add_argument("plan", help="Path to plan JSON")
    run.add_argument("--headed", action="store_true")
    run.add_argument("-y", "--yes", action="store_true", help="Skip confirmations")
    run.set_defaults(func=cmd_run)

    info = sub.add_parser("providers", help="Show AI provider config")
    info.set_defaults(func=cmd_providers)

    return parser


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        # default to chat for simplest UX
        args = parser.parse_args(["chat"] + (argv or []))
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
