# 60-second demo script

Screen recording with voiceover. Everything shown is a **real recorded call**, replayed. Nothing is staged,
and nothing depends on a live model behaving on cue.

## Setup (before recording)

- Terminal: large font (18–20pt), dark theme, window ~100 columns wide. Two panes side by side for shot 3.
- Browser tab: the repo's README on GitHub, scrolled to the top.
- Pre-run each command once so nothing is slow on camera.
- Record at 1080p+. QuickTime (File → New Screen Recording) or similar is fine; voiceover can be added after.

## Shots

| Time | Screen | Voiceover |
|---|---|---|
| **0:00–0:08** | README top: title + results table | "I built a mortgage-servicing AI agent and a benchmark that tests whether it's safe enough for a lender. The question: can a cheap, fast model do this job if the rules are enforced in code?" |
| **0:08–0:20** | Terminal: `uv run python -m bench.replay runs/bench-20261005-125956/pay-current-full/guardrails-t1` (let it play the verification + payment read-back) | "Here's a normal call. A simulated borrower calls in. The agent verifies them, reads the payment back, and only takes it after a clear yes." |
| **0:20–0:42** | Two panes. **Left:** `…/servicemember-deployment/prompt_only-t4`. **Right:** `…/servicemember-deployment/guardrails-t3`. Start both together. | "Same model, same caller. On the left, rules are only in the prompt: the agent transfers without logging the call, which is a compliance miss. On the right, the same mistake gets blocked in code. It writes the note, then transfers. It still made a different mistake, claiming to see a flag it never looked up. That's the next guardrail." |
| **0:42–0:52** | Terminal: `uv run python -m bench.run --dry-run` (scenario list), then cut to `runs/bench-20261005-125956/report.regraded.md` summary | "Sixteen scenarios: impostors, hardship, bankruptcy, prompt injection. Four runs each, graded on the end state and by an LLM judge I calibrated against my own hand labels." |
| **0:52–1:00** | Back to README results table | "Result: code-enforced guardrails cut violations in half and made the cheap model far more consistent, at forty percent of the big model's cost. Code and findings are on GitHub." |

## Commands (copy-paste)

```bash
cd ~/code/servicing-bench
uv run python -m bench.replay runs/bench-20261005-125956/pay-current-full/guardrails-t1
uv run python -m bench.replay runs/bench-20261005-125956/servicemember-deployment/prompt_only-t4
uv run python -m bench.replay runs/bench-20261005-125956/servicemember-deployment/guardrails-t3
uv run python -m bench.run --dry-run --model claude-haiku-4-5 --trials 4
```

Add `--fast` to any replay to print it instantly (for checking, not recording).

## Notes

- `runs/` isn't in git. Keep these run folders on this machine until the video is recorded.
- If a reviewer asks "is this cherry-picked?": yes, the pair was chosen to show the guardrail firing, and the
  README reports all 128 calls. Say that out loud; it builds trust.
