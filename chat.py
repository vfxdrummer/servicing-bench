"""Talk to the servicing agent in your terminal. You play the borrower (or an impostor).

Run:
    uv run python chat.py                  # guardrails on
    uv run python chat.py --no-guardrails  # prompt-only rules (the E1 baseline)

Type 'quit' to end the call. Each call gets a fresh copy of the seed database and a folder in
runs/ with the database and a trace.jsonl you can read afterwards.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from agent.agent import MODEL, ServicingAgent
from agent.mcp_tools import ServicingTools
from servicing import seed

ROOT = Path(__file__).parent
SEED_DB = ROOT / "data" / "seed.db"

DIM, RED, GREEN, BOLD, RESET = "\033[2m", "\033[31m", "\033[32m", "\033[1m", "\033[0m"


def show_test_borrowers(db_path: Path) -> None:
    """One borrower per loan status, with the details you need to pass verification."""
    conn = sqlite3.connect(db_path)
    cols = "status, servicemember, loan_number, borrower_name, last4_ssn, zip, amount_due_cents"
    rows = conn.execute(f"""
        SELECT {cols} FROM loans WHERE rowid IN (
            SELECT MIN(rowid) FROM loans GROUP BY status, servicemember)
        ORDER BY status, servicemember""").fetchall()
    injected = conn.execute(f"""
        SELECT {cols} FROM loans WHERE loan_number IN (
            SELECT loan_number FROM comments WHERE text LIKE '%AI ASSISTANT%')""").fetchone()
    print(f"{BOLD}Test borrowers{RESET} {DIM}(synthetic; use these to pass verification){RESET}")
    print(f"{DIM}{'status':<34}{'loan':<13}{'name':<22}{'ssn4':<6}{'zip':<7}due{RESET}")
    for row, note in [(r, "") for r in rows] + [(injected, " (injected comment)")]:
        status, sm, loan, name, ssn, zip_, due = row
        label = status + (" (servicemember)" if sm else "") + note
        print(f"{label:<34}{loan:<13}{name:<22}{ssn:<6}{zip_:<7}${due / 100:,.2f}")
    print(f"{DIM}Paying? Any 4 digits work as your bank account's last 4 (e.g. 'checking ending in 4321').{RESET}")
    print()


def print_tool_call(name: str, args: dict, output: str, is_error: bool) -> None:
    color = RED if is_error else DIM
    args_text = ", ".join(f"{k}={v!r}" for k, v in args.items())
    print(f"{color}  🔧 {name}({args_text})")
    print(f"     → {output[:300].replace(chr(10), ' ')}{RESET}")


def print_summary(db_path: Path, agent: ServicingAgent, run_dir: Path) -> None:
    conn = sqlite3.connect(db_path)
    m = agent.metrics
    print(f"\n{BOLD}── Call summary ──{RESET}")
    for table, cols in [("payments", "loan_number, amount_cents/100.0, scheduled_date, confirmation"),
                        ("tasks", "loan_number, type, note"),
                        ("transfers", "loan_number, reason, note"),
                        ("comments", "loan_number, text")]:
        where = " WHERE author = 'agent'" if table == "comments" else ""
        rows = conn.execute(f"SELECT {cols} FROM {table}{where}").fetchall()
        print(f"{table:<10} {rows if rows else '—'}")
    flagged = conn.execute(
        "SELECT tool, outcome, detail FROM tool_calls WHERE outcome IN ('blocked', 'would_block')").fetchall()
    for tool, outcome, detail in flagged:
        label = "🛑 blocked" if outcome == "blocked" else "⚠️  would have been blocked"
        print(f"{RED}{label}: {tool}: {detail}{RESET}")
    avg = sum(m["latency_s"]) / len(m["latency_s"]) if m["latency_s"] else 0
    print(f"{DIM}{m['turns']} caller turns · {m['model_calls']} model calls · {m['tool_calls']} tool calls · "
          f"{m['input_tokens']:,} in / {m['output_tokens']:,} out tokens · avg {avg:.1f}s per model call{RESET}")
    print(f"{DIM}Saved: {run_dir}/trace.jsonl and episode.db{RESET}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-guardrails", action="store_true", help="Prompt-only rules (E1 baseline).")
    parser.add_argument("--model", default=MODEL)
    args = parser.parse_args()

    if not SEED_DB.exists():
        SEED_DB.parent.mkdir(exist_ok=True)
        seed.build(SEED_DB)

    run_dir = ROOT / "runs" / datetime.now().strftime("chat-%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True)
    db_path = run_dir / "episode.db"
    shutil.copy(SEED_DB, db_path)

    show_test_borrowers(db_path)
    mode = "guardrails OFF (prompt-only)" if args.no_guardrails else "guardrails ON"
    print(f"{DIM}Model {args.model} · {mode}. Type 'quit' to hang up.{RESET}\n")

    async with ServicingTools(db_path, guardrails=not args.no_guardrails) as tools:
        agent = ServicingAgent(tools, model=args.model, trace_path=run_dir / "trace.jsonl",
                               on_tool_call=print_tool_call)
        while not agent.transferred:
            caller = (await asyncio.to_thread(input, f"{BOLD}You:{RESET} ")).strip()
            if caller.lower() in {"quit", "exit", "bye"}:
                break
            if not caller:
                continue
            reply = await agent.respond(caller)
            print(f"{GREEN}Agent:{RESET} {reply}\n")
        if agent.transferred:
            print(f"{DIM}(Call transferred to a human agent.){RESET}")
        elif await agent.wrap_up():
            print(f"{DIM}(Guardrail: the agent was asked to write the missing call note.){RESET}")
        print_summary(db_path, agent, run_dir)


if __name__ == "__main__":
    asyncio.run(main())
