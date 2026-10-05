"""The agent loop against the real MCP server, with a scripted fake model (no API calls)."""

import asyncio
import json
import shutil
import sqlite3
from types import SimpleNamespace as NS
from unittest.mock import patch

from agent import agent as agent_module
from agent.agent import ServicingAgent
from agent.mcp_tools import ServicingTools
from servicing import seed


class Block(NS):
    def model_dump(self):
        return vars(self)


def text(t):
    return Block(type="text", text=t)


def tool(id_, name, inp):
    return Block(type="tool_use", id=id_, name=name, input=inp)


def reply(stop, *content):
    return NS(stop_reason=stop, content=list(content), usage=NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0))


class FakeClient:
    """Plays back scripted responses and records every request it receives."""

    def __init__(self, script):
        self.script, self.requests = list(script), []
        self.beta = NS(messages=NS(create=self.create))

    async def create(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs["messages"], default=lambda o: vars(o))))
        return self.script.pop(0)


def run(tmp_path, script, turns, guardrails=True):
    seed.build(tmp_path / "seed.db")
    db_path = tmp_path / "episode.db"
    shutil.copy(tmp_path / "seed.db", db_path)
    fake = FakeClient(script)

    async def go():
        async with ServicingTools(db_path, guardrails=guardrails) as tools:
            with patch.object(agent_module.anthropic, "AsyncAnthropic", lambda: fake):
                agent = ServicingAgent(tools, trace_path=tmp_path / "trace.jsonl")
            replies = [await agent.respond(t) for t in turns]
            return agent, replies

    agent, replies = asyncio.run(go())
    return agent, replies, fake, sqlite3.connect(db_path)


def a_loan(tmp_path):
    seed.build(tmp_path / "probe.db")
    return sqlite3.connect(tmp_path / "probe.db").execute(
        "SELECT loan_number, borrower_name, last4_ssn, zip FROM loans WHERE status='current' LIMIT 1").fetchone()


def test_blocked_call_reaches_model_then_recovers(tmp_path):
    loan_number, name, ssn, zip_ = a_loan(tmp_path)
    script = [
        reply("tool_use", tool("t1", "get_loan_details", {"loan_number": loan_number})),
        reply("end_turn", text("I need to verify you first. What's your full name?")),
        reply("tool_use", tool("t2", "verify_identity", {"loan_number": loan_number, "full_name": name,
                                                         "last4_ssn": ssn, "zip_code": zip_})),
        reply("end_turn", text("Thanks, you're verified.")),
    ]
    agent, replies, fake, db = run(tmp_path, script, [f"What's my balance on {loan_number}?", "details"])

    assert replies == ["I need to verify you first. What's your full name?", "Thanks, you're verified."]
    blocked_result = fake.requests[1][-1]["content"][0]
    assert blocked_result["is_error"] is True and "BLOCKED BY POLICY" in blocked_result["content"]
    roles = [m["role"] for m in fake.requests[-1]]
    assert roles == ["user", "assistant", "user", "assistant", "user", "assistant", "user"]
    assert [r[0] for r in db.execute("SELECT outcome FROM tool_calls ORDER BY seq")] == ["blocked", "ok"]
    assert agent.metrics["tool_calls"] == 2 and agent.metrics["model_calls"] == 4
    events = [json.loads(line)["event"] for line in (tmp_path / "trace.jsonl").read_text().splitlines()]
    assert events.count("tool") == 2 and events.count("caller") == 2


def test_transfer_marks_call_transferred(tmp_path):
    loan_number, *_ = a_loan(tmp_path)
    script = [
        reply("tool_use", tool("t1", "add_loan_comment", {"loan_number": loan_number, "text": "Caller filed BK."}),
              tool("t2", "transfer_to_human", {"reason": "bankruptcy", "note": "filed ch7",
                                               "loan_number": loan_number})),
        reply("end_turn", text("I'm connecting you with a specialist now.")),
    ]
    agent, _, _, db = run(tmp_path, script, ["I filed for bankruptcy last week."])
    assert agent.transferred
    assert db.execute("SELECT reason FROM transfers").fetchone()[0] == "bankruptcy"


def test_runaway_tool_loop_is_capped(tmp_path):
    script = [reply("tool_use", tool(f"t{i}", "lookup_loan", {"loan_number": "1"}))
              for i in range(agent_module.MAX_TOOL_ROUNDS)]
    agent, replies, _, _ = run(tmp_path, script, ["hello"])
    assert "transfer you" in replies[0]
    assert agent.metrics["model_calls"] == agent_module.MAX_TOOL_ROUNDS
