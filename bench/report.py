"""Turn results.jsonl into a Markdown report.  Re-run any time:  uv run python -m bench.report runs/<run>/results.jsonl"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from math import comb
from pathlib import Path


def pass_hat_k(n: int, c: int, k: int) -> float:
    """τ-bench's pass^k: probability that k independent runs of a task ALL pass, given c of n passed."""
    return comb(c, k) / comb(n, k) if n >= k else float("nan")


def build(results_path: Path) -> str:
    rows = [json.loads(line) for line in Path(results_path).read_text().splitlines() if line.strip()]
    ok = [r for r in rows if "error" not in r]
    errors = [r for r in rows if "error" in r]
    conditions = sorted({r["condition"] for r in rows})
    scenarios = sorted({r["scenario"] for r in rows})
    k = min((sum(1 for r in ok if r["scenario"] == s and r["condition"] == c)
             for s in scenarios for c in conditions), default=0)

    out = [f"# Benchmark report\n",
           f"`{results_path}` · {len(rows)} calls · {len(errors)} errors · agent model "
           f"`{rows[0]['agent_model'] if rows else '?'}`\n",
           "## Summary\n",
           f"| condition | calls | pass@1 | pass^{k} | calls with a violation | blocked attempts | outcome correct | avg cost/call | total cost |",
           "|---|---|---|---|---|---|---|---|---|"]
    for c in conditions:
        rs = [r for r in ok if r["condition"] == c]
        if not rs:
            continue
        by_s = defaultdict(list)
        for r in rs:
            by_s[r["scenario"]].append(r["passed"])
        phk = [pass_hat_k(len(v), sum(v), k) for v in by_s.values()] if k else []
        viol = sum(1 for r in rs if r["violations"])
        blocked = sum(len(r["blocked_attempts"]) for r in rs)
        outcome = sum(1 for r in rs if r["outcome_ok"])
        total = sum(r["cost"]["total"] for r in rows if r["condition"] == c)
        out.append(f"| {c} | {len(rs)} | {sum(r['passed'] for r in rs) / len(rs):.0%} | "
                   f"{sum(phk) / len(phk):.0%} | {viol / len(rs):.0%} | {blocked} | {outcome / len(rs):.0%} | "
                   f"${total / len(rs):.3f} | ${total:.2f} |")

    out += ["", "*pass@1*: share of calls that passed. *pass^k*: share of scenarios that passed on all k runs "
            "(reliability). *blocked attempts*: violations the guardrails stopped.", "",
            "## By scenario\n", "| scenario | category | " + " | ".join(conditions) + " |",
            "|---|---|" + "---|" * len(conditions)]
    for s in scenarios:
        cat = next(r["category"] for r in rows if r["scenario"] == s)
        cells = []
        for c in conditions:
            rs = [r for r in ok if r["scenario"] == s and r["condition"] == c]
            cells.append(f"{sum(r['passed'] for r in rs)}/{len(rs)}" if rs else "–")
        out.append(f"| {s} | {cat} | " + " | ".join(cells) + " |")

    fails = [r for r in ok if not r["passed"]]
    if fails:
        out += ["", "## Failures\n"]
        for r in sorted(fails, key=lambda r: (r["scenario"], r["condition"], r["trial"])):
            out.append(f"- **{r['scenario']}** · {r['condition']} · t{r['trial']} · `{r['dir']}`")
            for f in r["failed_checks"]:
                out.append(f"  - {f['kind']} `{f['check']}`: {f['detail']}")
    if errors:
        out += ["", "## Errors (harness/API; excluded from scores)\n"]
        out += [f"- {r['scenario']} · {r['condition']} · t{r['trial']}: {r['error']}" for r in errors]
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    print(build(Path(sys.argv[1])))
