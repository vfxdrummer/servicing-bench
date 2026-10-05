"""Replay a recorded benchmark call in the terminal, with guardrail moments highlighted. Made for demos:
real calls, deterministic, no API cost.

    uv run python -m bench.replay runs/<run>/<scenario>/<condition>-t<n>            # at reading speed
    uv run python -m bench.replay runs/<run>/<scenario>/<condition>-t<n> --fast     # instantly
"""

from __future__ import annotations

import json
import sqlite3
import sys
import textwrap
import time
from pathlib import Path

from bench.grader import load_trace

DIM, RED, YEL, GRN, BLU, BOLD, RESET = "\033[2m", "\033[31m", "\033[33m", "\033[32m", "\033[34m", "\033[1m", "\033[0m"
WIDTH = 78


def pause(text: str, fast: bool) -> None:
    if not fast:
        time.sleep(min(0.6 + len(text) / 55, 4.5))  # roughly reading speed


def say(prefix: str, color: str, text: str, fast: bool) -> None:
    body = textwrap.fill(" ".join(text.split()), WIDTH - 9, subsequent_indent=" " * 9)
    print(f"{color}{BOLD}{prefix:<8}{RESET} {body}")
    pause(text, fast)


def main(ep_dir: Path, fast: bool) -> None:
    trace = load_trace(ep_dir / "trace.jsonl")
    audit = sqlite3.connect(ep_dir / "episode.db").execute(
        "SELECT tool, outcome, detail FROM tool_calls ORDER BY seq").fetchall()
    run_dir, scenario, cond = ep_dir.parent.parent, ep_dir.parent.name, ep_dir.name
    results = run_dir / ("results.regraded.jsonl" if (run_dir / "results.regraded.jsonl").exists() else "results.jsonl")
    result = next((json.loads(l) for l in results.read_text().splitlines()
                   if f"/{scenario}/{cond}" in json.loads(l)["dir"]), None)

    mode = "guardrails ON (code-enforced)" if cond.startswith("guardrails") else "guardrails OFF (prompt only)"
    print(f"\n{BOLD}{scenario}{RESET}  ·  {mode}  ·  {result['agent_model'] if result else ''}")
    print(DIM + "─" * WIDTH + RESET)

    pos = 0  # walk the audit log alongside the trace to spot would-have-blocked calls
    for e in trace:
        if e["event"] == "caller":
            say("Caller", BLU, e["text"], fast)
        elif e["event"] == "reply" and e["text"].strip():
            say("Agent", GRN, e["text"], fast)
        elif e["event"] == "tool":
            flagged = None
            while pos < len(audit):
                tool, outcome, detail = audit[pos]
                pos += 1
                if tool != e["name"]:
                    continue
                if outcome == "would_block":
                    flagged = detail
                    continue
                break
            args = ", ".join(f"{k}={v!r}" for k, v in e["input"].items() if k != "text")
            if e["is_error"] and "BLOCKED BY POLICY" in e["output"]:
                print(f"{RED}{BOLD}  🛑 BLOCKED{RESET} {RED}{e['name']}({args}){RESET}")
                print(f"{RED}     {e['output'].split('BLOCKED BY POLICY: ')[-1]}{RESET}")
                pause(e["output"] + "x" * 80, fast)
            elif flagged:
                print(f"{YEL}{BOLD}  ⚠️  VIOLATION (not blocked){RESET} {YEL}{e['name']}({args}){RESET}")
                print(f"{YEL}     {flagged}{RESET}")
                pause(flagged + "x" * 80, fast)
            else:
                print(f"{DIM}  🔧 {e['name']}({args[:90]}){RESET}")
                pause("x" * 20, fast)
        elif e["event"] == "guardrail":
            print(f"{YEL}{BOLD}  🛡️  GUARDRAIL{RESET} {YEL}{e['rule']}: {e['action']}{RESET}")
            pause("x" * 120, fast)

    print(DIM + "─" * WIDTH + RESET)
    if result:
        if result["passed"]:
            print(f"{GRN}{BOLD}PASS{RESET}")
        else:
            print(f"{RED}{BOLD}FAIL{RESET}")
            for f in result["failed_checks"]:
                print(f"{RED}  · {f['check']}: {f['detail'][:WIDTH]}{RESET}")
    print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(Path(sys.argv[1]).resolve(), fast="--fast" in sys.argv)
