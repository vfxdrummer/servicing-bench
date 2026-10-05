"""Re-grade an existing run with the CURRENT grader and scenario expectations, without re-running calls.

    uv run python -m bench.regrade runs/bench-20261005-122510

When the grader gets stricter, old runs must be re-graded before comparing them to new ones;
otherwise a difference could just be a change in grading. Only the judge calls cost money.
Writes results.regraded.jsonl and report.regraded.md next to the originals.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from bench import report
from bench.grader import grade
from bench.judge import JUDGE_MODEL, Judge
from bench.pricing import cost
from bench.scenario import load_all

ROOT = Path(__file__).parent.parent
SEED_DB = ROOT / "data" / "seed.db"


async def main(run_dir: Path, concurrency: int = 6) -> None:
    rows = [json.loads(l) for l in (run_dir / "results.jsonl").read_text().splitlines() if l.strip()]
    scenarios = {s.id: s for s in load_all(SEED_DB)}
    sem = asyncio.Semaphore(concurrency)
    judge_cost = 0.0

    async def one(r: dict) -> dict:
        nonlocal judge_cost
        if "error" in r:
            return r
        ep = ROOT / r["dir"]
        async with sem:
            judge = Judge()
            graded = await grade(scenarios[r["scenario"]], ep / "episode.db", ep / "trace.jsonl", judge)
        judge_cost += cost(JUDGE_MODEL, judge.metrics)
        before = r["passed"]
        r.update(graded)
        if before != r["passed"]:
            print(f"  {r['scenario']:<32} {r['condition']:<12} t{r['trial']}: "
                  f"{'PASS' if before else 'FAIL'} → {'PASS' if r['passed'] else 'FAIL'}  "
                  f"{'; '.join(c['check'] for c in r['failed_checks'])}")
        return r

    print(f"Re-grading {len(rows)} calls in {run_dir} (changes listed below)")
    regraded = await asyncio.gather(*(one(r) for r in rows))
    out = run_dir / "results.regraded.jsonl"
    out.write_text("\n".join(json.dumps(r, default=str) for r in regraded) + "\n")
    md = report.build(out)
    (run_dir / "report.regraded.md").write_text(md)
    print("\n" + md)
    print(f"Judge cost for re-grade: ${judge_cost:.2f}")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]).resolve()))
