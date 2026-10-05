"""Hand-label judge decisions, then measure how often the LLM judge agrees with you.

    uv run python -m bench.label sample runs/bench-A [runs/bench-B ...]   # pick ~40 items (blind)
    uv run python -m bench.label serve                                   # label at http://localhost:8765
    uv run python -m bench.label report                                  # agreement + every disagreement

The sample is stratified: every call the judge flagged, plus a random set it passed, spread across
rules. You never see the judge's verdict while labeling. Transcripts are copied into the sample, so the
labels stay valid even after runs/ is cleaned up. labels/ is meant to be committed.
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from bench.grader import load_trace, transcript
from bench.judge import RULES
from bench.scenario import load_all

ROOT = Path(__file__).parent.parent
LABEL_DIR = ROOT / "labels"
SAMPLE = LABEL_DIR / "sample.json"
LABELS = LABEL_DIR / "labels.json"
TARGET = 40
SEED = 11


def cmd_sample(run_dirs: list[Path]) -> None:
    scenarios = {s.id: s for s in load_all(ROOT / "data" / "seed.db")}
    flagged, passed = [], []
    for run in run_dirs:
        results = run / ("results.regraded.jsonl" if (run / "results.regraded.jsonl").exists() else "results.jsonl")
        for line in results.read_text().splitlines():
            r = json.loads(line)
            for rule, verdict in r.get("judgments", {}).items():
                item = {
                    "id": hashlib.sha1(f"{r['dir']}|{rule}".encode()).hexdigest()[:10],
                    "run": run.name, "dir": r["dir"], "scenario": r["scenario"], "condition": r["condition"],
                    "trial": r["trial"], "model": r["agent_model"], "rule": rule,
                    "persona": scenarios[r["scenario"]].persona,
                    "transcript": transcript(load_trace(ROOT / r["dir"] / "trace.jsonl")),
                    "judge": verdict,  # hidden from the labeling page
                }
                (flagged if verdict["violated"] else passed).append(item)

    rng = random.Random(SEED)
    chosen = list(flagged)
    if len(chosen) > TARGET // 2:  # keep at least half the sample as judge-passed items
        chosen = rng.sample(chosen, TARGET // 2)
    by_rule = defaultdict(list)
    for item in passed:
        by_rule[item["rule"]].append(item)
    for items in by_rule.values():
        rng.shuffle(items)
    while len(chosen) < TARGET and any(by_rule.values()):  # round-robin across rules
        for rule in sorted(by_rule):
            if by_rule[rule] and len(chosen) < TARGET:
                chosen.append(by_rule[rule].pop())
    rng.shuffle(chosen)

    LABEL_DIR.mkdir(exist_ok=True)
    SAMPLE.write_text(json.dumps({"created": datetime.now().isoformat(timespec="seconds"),
                                  "runs": [r.name for r in run_dirs], "items": chosen}, indent=1))
    counts = Counter(i["rule"] for i in chosen)
    print(f"Sampled {len(chosen)} items ({sum(i['judge']['violated'] for i in chosen)} judge-flagged) → {SAMPLE}")
    for rule, n in sorted(counts.items()):
        print(f"  {rule:<30} {n}")


def _load_labels() -> dict:
    return json.loads(LABELS.read_text()) if LABELS.exists() else {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # quiet
        pass

    def _send(self, body: bytes, ctype: str = "application/json", code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/":
            self._send((Path(__file__).parent / "label.html").read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/items":
            items = json.loads(SAMPLE.read_text())["items"]
            blind = [{k: v for k, v in i.items() if k != "judge"} for i in items]  # never send the verdict
            self._send(json.dumps({"items": blind, "rules": RULES, "labels": _load_labels()}).encode())
        else:
            self._send(b'{"error": "not found"}', code=404)

    def do_POST(self) -> None:
        if self.path != "/api/label":
            self._send(b'{"error": "not found"}', code=404)
            return
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        labels = _load_labels()
        labels[data["id"]] = {"label": data["label"], "note": data.get("note", ""),
                              "at": datetime.now().isoformat(timespec="seconds")}
        LABELS.write_text(json.dumps(labels, indent=1))
        self._send(json.dumps({"saved": len(labels)}).encode())


def cmd_serve(port: int = 8765) -> None:
    if not SAMPLE.exists():
        sys.exit("No sample yet. Run: uv run python -m bench.label sample runs/<run> ...")
    print(f"Labeling at http://localhost:{port}  (Ctrl-C to stop; labels save as you go)")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


def cmd_report() -> None:
    items = {i["id"]: i for i in json.loads(SAMPLE.read_text())["items"]}
    labels = _load_labels()
    rows = [(items[i], l) for i, l in labels.items() if i in items and l["label"] in ("violation", "ok")]
    unsure = sum(1 for l in labels.values() if l["label"] == "unsure")
    if not rows:
        sys.exit("No labels yet.")
    agree = [(i, l) for i, l in rows if i["judge"]["violated"] == (l["label"] == "violation")]
    fp = [(i, l) for i, l in rows if i["judge"]["violated"] and l["label"] == "ok"]
    fn = [(i, l) for i, l in rows if not i["judge"]["violated"] and l["label"] == "violation"]
    out = ["# Judge calibration\n",
           f"{len(rows)} labeled items ({unsure} marked unsure, excluded) · {len(labels)}/{len(items)} done\n",
           f"**Agreement: {len(agree)}/{len(rows)} = {len(agree) / len(rows):.0%}**  ·  "
           f"judge too strict (flagged, you said OK): {len(fp)}  ·  judge too lenient (missed): {len(fn)}\n",
           "| rule | labeled | agree | too strict | too lenient |", "|---|---|---|---|---|"]
    by_rule = defaultdict(list)
    for i, l in rows:
        by_rule[i["rule"]].append((i, l))
    for rule, rs in sorted(by_rule.items()):
        a = sum(1 for i, l in rs if i["judge"]["violated"] == (l["label"] == "violation"))
        s = sum(1 for i, l in rs if i["judge"]["violated"] and l["label"] == "ok")
        n = sum(1 for i, l in rs if not i["judge"]["violated"] and l["label"] == "violation")
        out.append(f"| {rule} | {len(rs)} | {a}/{len(rs)} | {s} | {n} |")
    for title, group in [("Judge too strict", fp), ("Judge too lenient", fn)]:
        if group:
            out += ["", f"## {title}\n"]
            for i, l in group:
                out.append(f"- **{i['scenario']}** · {i['condition']} t{i['trial']} · `{i['rule']}` · `{i['dir']}`")
                out.append(f"  - judge evidence: {i['judge']['evidence'][:250]}")
                if l.get("note"):
                    out.append(f"  - your note: {l['note']}")
    md = "\n".join(out) + "\n"
    (LABEL_DIR / "report.md").write_text(md)
    print(md)


if __name__ == "__main__":
    cmd, args = (sys.argv[1] if len(sys.argv) > 1 else ""), sys.argv[2:]
    if cmd == "sample" and args:
        cmd_sample([Path(a).resolve() for a in args])
    elif cmd == "serve":
        cmd_serve()
    elif cmd == "report":
        cmd_report()
    else:
        sys.exit(__doc__)
