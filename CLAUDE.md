# servicing-bench

A tool-using mortgage-servicing agent plus a τ-bench-style benchmark (simulated borrowers, end-state grading, pass^k). The full plan and milestones are in `PLAN.md`. Read it before starting work.

## Purpose
A portfolio project for an AI Engineer role at a company building AI agents for mortgage servicing. Optimize for: measured results, code-enforced guardrails, clear write-up of failure modes. Polish matters less than rigor.

## Rules
- **Synthetic data only.** Never generate or commit anything resembling real personal information.
- **No real company names or branding** (servicers, vendors, the hiring company) in code, docs or demo.
- **Guardrails are enforced in code** (tool gates, guardrail layer), not only in prompts. Prompt-only rules are the E1 baseline, not the design.
- **Never weaken a grader, scenario or test to make a run pass.** Fix the agent or flag the scenario.
- Every benchmark run writes a JSONL trace; results must be reproducible from traces.
- Before any benchmark run that calls the API, state the estimated cost and get approval.

## Stack
Python 3.12 with uv · Anthropic Python SDK · `mcp` SDK 2.x (MCPServer, stdio) · SQLite · FastAPI for the demo.
Models: agent `claude-opus-5` by default; user simulator `claude-sonnet-5`; experiments compare models explicitly.

## Commands
```bash
export PATH="$HOME/Library/Python/3.9/bin:$PATH" UV_PYTHON_INSTALL_DIR="$HOME/.uv-python"   # see Setup notes
uv sync                                   # install deps
uv run python -m servicing.seed data/seed.db   # build the deterministic seed DB
uv run pytest -q                          # all tests (no API calls)
uv run python chat.py                     # talk to the agent
uv run python -m bench.run --dry-run      # list scenarios + estimated cost
uv run python -m bench.run --pattern '01-*'   # smoke test, one scenario
```
Each episode runs against a *copy* of `data/seed.db` (in `runs/`), so the grader can diff end state.

## Layout
- `servicing/`: mock servicing system. `system.py` holds tools + guardrails (`enforce_guardrails=False` logs `would_block` instead of blocking: the E1 baseline). `mcp_server.py` exposes it over MCP stdio. `seed.py` builds 50 synthetic loans with a fixed clock (`config.TODAY`).
- `agent/`: `mcp_tools.py` (MCP → Anthropic tool adapter), `policy.md` (system prompt), `agent.py` (the agent loop; one `respond()` per caller turn).
- `chat.py`: terminal chat; you play the borrower.
- `bench/`: the benchmark. `scenarios/*.yaml` (format in `scenarios/README.md`), `simulator.py` (LLM borrower),
  `grader.py` (end state + violations), `judge.py` (LLM judge for soft rules), `run.py` (runner), `report.py`.
- `tests/`: guardrails, MCP boundary, agent loop, and benchmark harness. All use fake models; no API calls.

## Gotchas
- **MCP Python SDK is 2.x**: `from mcp.server.mcpserver import MCPServer` (FastMCP is gone); client results use `is_error`/`input_schema`. Only `mcp.server.mcpserver.exceptions.ToolError` passes its message to the model. Any other exception reaches the model as a bare "Error executing tool X". `mcp_server._call` translates errors; keep it that way.
- MCP tools must stay `async` (SQLite connection is single-threaded; this also serializes parallel tool calls).

## Setup notes (this Mac)
- `~/.local` is owned by root, so uv can't use its defaults there. uv was installed with `pip install --user uv` (binary in `~/Library/Python/3.9/bin`), and Python 3.12 lives in `~/.uv-python` via `UV_PYTHON_INSTALL_DIR`. A permanent fix is `sudo chown -R $(whoami) ~/.local` (the owner's call).

## Owner
Learning agentic engineering hands-on. Explain the *why* of design choices briefly, and leave key pieces (the agent loop, grader logic) for the owner to write when they ask to learn by doing.
