"""Run the benchmark: every scenario × condition × trial is one simulated phone call.

    uv run python -m bench.run --dry-run                         # what would run, and estimated cost
    uv run python -m bench.run --pattern '01-*' --trials 1       # smoke test: one scenario
    uv run python -m bench.run --trials 4                        # the full benchmark, pass^4

Conditions:
    guardrails   policy prompt + code-enforced guardrails (the design)
    prompt_only  policy prompt only; violations are logged instead of blocked (the E1 baseline)

Each call writes runs/<run>/<scenario>/<condition>-t<n>/ with episode.db and trace.jsonl, and one
line in runs/<run>/results.jsonl. The report is regenerated from results.jsonl at the end.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import time
import traceback
from datetime import datetime
from pathlib import Path

from agent.agent import MODEL, ServicingAgent
from agent.mcp_tools import ServicingTools
from bench import report
from bench.grader import grade
from bench.judge import JUDGE_MODEL, Judge
from bench.pricing import PRICES, cost
from bench.scenario import Scenario, load_all
from bench.simulator import SIM_MODEL, BorrowerSimulator
from servicing import seed

ROOT = Path(__file__).parent.parent
SEED_DB = ROOT / "data" / "seed.db"
MAX_CALLER_TURNS = 14
CONDITIONS = {"guardrails": True, "prompt_only": False}

# Rough per-call estimate (agent ~8 model calls with caching,
# simulator ~7 short calls, ~1.5 judge calls). The report shows the real, measured cost.
EST_AGENT_PER_CALL_AT_OPUS = 0.07  # measured: $0.064 on a 5-turn payment call (2026-10-05)
EST_SIM_AND_JUDGE_PER_CALL = 0.02  # measured: ~$0.012


async def run_episode(s: Scenario, condition: str, trial: int, run_dir: Path, agent_model: str) -> dict:
    ep_dir = run_dir / s.id / f"{condition}-t{trial}"
    if ep_dir.exists():
        shutil.rmtree(ep_dir)  # leftovers from an interrupted attempt
    ep_dir.mkdir(parents=True)
    db_path = ep_dir / "episode.db"
    trace_path = ep_dir / "trace.jsonl"
    shutil.copy(SEED_DB, db_path)
    result = {"scenario": s.id, "category": s.category, "condition": condition, "trial": trial,
              "agent_model": agent_model, "dir": str(ep_dir.relative_to(ROOT))}
    started = time.monotonic()
    sim = BorrowerSimulator(s.persona)
    judge = Judge()
    try:
        async with ServicingTools(db_path, guardrails=CONDITIONS[condition]) as tools:
            agent = ServicingAgent(tools, model=agent_model, trace_path=trace_path)
            end_reason = "max_turns"
            for _ in range(MAX_CALLER_TURNS):
                line = await sim.next_line()
                if line:
                    # Even the caller's goodbye goes to the agent: it may still need to write the call note.
                    sim.hear(await agent.respond(line))
                if agent.transferred:
                    end_reason = "transferred"
                    break
                if sim.ended:
                    end_reason = "caller_ended"
                    break
            # Post-call hook: only acts with guardrails on and only if a call note is missing.
            wrapped_up = await agent.wrap_up()
        graded = await grade(s, db_path, trace_path, judge)
        result.update(graded)
        result.update(end_reason=end_reason, caller_turns=agent.metrics["turns"], wrapped_up=wrapped_up,
                      guardrail_interventions=agent.metrics["guardrail_interventions"],
                      agent_metrics=agent.metrics)
    except Exception as e:  # an API or harness failure is not an agent failure; record and move on
        result.update(error=f"{type(e).__name__}: {e}", traceback=traceback.format_exc()[-2000:])
    result["seconds"] = round(time.monotonic() - started, 1)
    am = result.get("agent_metrics", {})
    result["cost"] = {
        "agent": round(cost(agent_model, am), 4) if am else 0.0,
        "simulator": round(cost(SIM_MODEL, sim.metrics), 4),
        "judge": round(cost(JUDGE_MODEL, judge.metrics), 4),
    }
    result["cost"]["total"] = round(sum(result["cost"].values()), 4)
    return result


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--pattern", default="*.yaml", help="Scenario file glob, e.g. '0[1-5]-*'")
    p.add_argument("--trials", type=int, default=1, help="Runs per scenario per condition (k for pass^k)")
    p.add_argument("--conditions", default="guardrails,prompt_only")
    p.add_argument("--model", default=MODEL, choices=sorted(PRICES))
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--yes", action="store_true", help="Skip the cost confirmation prompt")
    p.add_argument("--resume", metavar="RUN_DIR", help="Continue an interrupted run, skipping finished calls")
    args = p.parse_args()

    if not SEED_DB.exists():
        SEED_DB.parent.mkdir(exist_ok=True)
        seed.build(SEED_DB)
    scenarios = load_all(SEED_DB, args.pattern)
    conditions = [c.strip() for c in args.conditions.split(",")]
    episodes = [(s, c, t) for s in scenarios for c in conditions for t in range(1, args.trials + 1)]
    finished: set = set()
    if args.resume:
        prev = Path(args.resume).resolve() / "results.jsonl"
        for line in prev.read_text().splitlines():
            r = json.loads(line)
            if "error" not in r:  # errored calls are retried
                finished.add((r["scenario"], r["condition"], r["trial"]))
        model = json.loads(prev.read_text().splitlines()[0])["agent_model"]
        if model != args.model:
            raise SystemExit(f"That run used {model}; pass --model {model} to resume it.")
        episodes = [e for e in episodes if (e[0].id, e[1], e[2]) not in finished]
        print(f"Resuming {prev.parent.name}: {len(finished)} calls already done")

    per_call = EST_AGENT_PER_CALL_AT_OPUS * PRICES[args.model][0] / PRICES["claude-opus-5"][0] \
        + EST_SIM_AND_JUDGE_PER_CALL
    total = len(scenarios) * len(conditions) * args.trials
    remaining = f" ({len(episodes)} still to run)" if args.resume else ""
    print(f"{len(scenarios)} scenarios × {len(conditions)} conditions × {args.trials} trials "
          f"= {total} calls{remaining} · agent model {args.model}")
    print(f"Estimated cost: ~${per_call * len(episodes):.2f} (rough; ~${per_call:.2f}/call)")
    if args.dry_run:
        for s in scenarios:
            print(f"  {s.id:<32} {s.category:<16} loan {s.loan['loan_number']} ({s.loan['status']})")
        return
    if not args.yes and input("Proceed? [y/N] ").strip().lower() != "y":
        return

    if args.resume:
        run_dir = Path(args.resume).resolve()
        results_path = run_dir / "results.jsonl"
        # drop errored rows; they're being retried
        kept = [l for l in results_path.read_text().splitlines() if l.strip() and "error" not in json.loads(l)]
        results_path.write_text("".join(l + "\n" for l in kept))
    else:
        run_dir = ROOT / "runs" / datetime.now().strftime("bench-%Y%m%d-%H%M%S")
        run_dir.mkdir(parents=True)
        results_path = run_dir / "results.jsonl"
    sem = asyncio.Semaphore(args.concurrency)
    done = 0

    async def one(s, c, t):
        nonlocal done
        async with sem:
            r = await run_episode(s, c, t, run_dir, args.model)
        with results_path.open("a") as f:
            f.write(json.dumps(r, default=str) + "\n")
        done += 1
        status = "ERROR" if "error" in r else ("PASS" if r["passed"] else "FAIL")
        why = r.get("error") or "; ".join(x["check"] for x in r.get("failed_checks", []))
        print(f"[{done}/{len(episodes)}] {status:<5} {s.id:<32} {c:<12} t{t}  ${r['cost']['total']:.3f}  {why}")

    await asyncio.gather(*(one(s, c, t) for s, c, t in episodes))
    md = report.build(results_path)
    (run_dir / "report.md").write_text(md)
    print("\n" + md)
    print(f"Saved: {run_dir}/report.md")


if __name__ == "__main__":
    asyncio.run(main())
