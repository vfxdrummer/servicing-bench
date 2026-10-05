"""The three guardrails added after the Haiku run, each tested ON (enforces) and OFF (stays out of the way):
  1. transfer gate        servicing system refuses a transfer until the call has a note
  2. output guardrail     debt disclosure inserted before the first statement of the amount owed
  3. wrap-up hook         caller hangs up with no note → one caller-invisible turn to write it
"""

import asyncio
import json
import shutil
import sqlite3

import pytest

from agent import agent as agent_module
from agent.agent import DEBT_DISCLOSURE, ServicingAgent
from agent.mcp_tools import ServicingTools
from bench import grader as grader_mod
from bench.scenario import load_all
from servicing import seed
from servicing.money import first_mention, money_strings
from servicing.system import PolicyBlocked, ServicingSystem
from tests.test_agent import FakeClient, reply, text, tool
from unittest.mock import patch


@pytest.fixture
def db(tmp_path):
    seed.build(tmp_path / "seed.db")
    shutil.copy(tmp_path / "seed.db", tmp_path / "episode.db")
    return tmp_path / "episode.db"


def loan(db, status):
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    return dict(c.execute("SELECT * FROM loans WHERE status = ? AND servicemember = 0 LIMIT 1", (status,)).fetchone())


def verify_call(l):
    return tool("v", "verify_identity", {"loan_number": l["loan_number"], "full_name": l["borrower_name"],
                                         "last4_ssn": l["last4_ssn"], "zip_code": l["zip"]})


def drive(db, script, turns, guardrails=True, wrap_up=False):
    fake = FakeClient(script)

    async def go():
        async with ServicingTools(db, guardrails=guardrails) as tools:
            with patch.object(agent_module.anthropic, "AsyncAnthropic", lambda: fake):
                agent = ServicingAgent(tools, trace_path=db.parent / "trace.jsonl")
            replies = [await agent.respond(t) for t in turns]
            wrapped = await agent.wrap_up() if wrap_up else None
            return agent, replies, wrapped

    agent, replies, wrapped = asyncio.run(go())
    return agent, replies, wrapped, fake


# ── 1. transfer gate ─────────────────────────────────────────────────────────

def test_transfer_blocked_until_call_note(db):
    l = loan(db, "current")
    s = ServicingSystem(db)
    s.call("lookup_loan", loan_number=l["loan_number"])
    with pytest.raises(PolicyBlocked, match="call note"):
        s.call("transfer_to_human", reason="caller_request", note="x")
    s.call("add_loan_comment", loan_number=l["loan_number"], text="Caller asked for a person.")
    assert s.call("transfer_to_human", reason="caller_request", note="x")["transferred"]


def test_transfer_gate_off_records_violation(db):
    l = loan(db, "current")
    s = ServicingSystem(db, enforce_guardrails=False)
    s.call("lookup_loan", loan_number=l["loan_number"])
    s.call("transfer_to_human", reason="caller_request", note="x")
    outcomes = [r[0] for r in sqlite3.connect(db).execute("SELECT outcome FROM tool_calls ORDER BY seq")]
    assert outcomes == ["ok", "would_block", "ok"]


def test_unknown_loan_lookup_does_not_require_note(db):
    s = ServicingSystem(db)
    s.call("lookup_loan", loan_number="0000000000")
    assert s.call("transfer_to_human", reason="caller_request", note="x")["transferred"]


# ── 2. output guardrail ──────────────────────────────────────────────────────

def _details_then_amount(l, amount_text):
    return [
        reply("tool_use", verify_call(l)),
        reply("tool_use", tool("d", "get_loan_details", {"loan_number": l["loan_number"]})),
        reply("end_turn", text(amount_text)),
    ]


def test_disclosure_inserted_before_amount_on_delinquent_loan(db):
    l = loan(db, "delinquent_30")
    due = f"${l['amount_due_cents'] / 100:,.2f}"
    agent, replies, _, _ = drive(db, _details_then_amount(l, f"You're verified. You owe {due}."), ["hi"])
    assert replies[0].startswith(DEBT_DISCLOSURE)
    assert agent.metrics["guardrail_interventions"] == 1
    events = [json.loads(x) for x in (db.parent / "trace.jsonl").read_text().splitlines()]
    assert any(e["event"] == "guardrail" for e in events)
    assert next(e for e in events if e["event"] == "reply")["text"].startswith(DEBT_DISCLOSURE)


def test_disclosure_not_inserted_without_guardrails(db):
    l = loan(db, "delinquent_30")
    due = f"${l['amount_due_cents'] / 100:,.2f}"
    agent, replies, _, _ = drive(db, _details_then_amount(l, f"You owe {due}."), ["hi"], guardrails=False)
    assert replies[0] == f"You owe {due}."
    assert agent.metrics["guardrail_interventions"] == 0


def test_disclosure_not_duplicated_when_model_says_it(db):
    l = loan(db, "delinquent_30")
    due = f"${l['amount_due_cents'] / 100:,.2f}"
    said = f"{DEBT_DISCLOSURE} You owe {due}."
    agent, replies, _, _ = drive(db, _details_then_amount(l, said), ["hi"])
    assert replies[0] == said and agent.metrics["guardrail_interventions"] == 0


def test_no_disclosure_for_current_loan(db):
    l = loan(db, "current")
    due = f"${l['amount_due_cents'] / 100:,.2f}"
    _, replies, _, _ = drive(db, _details_then_amount(l, f"Your payment is {due}."), ["hi"])
    assert replies[0] == f"Your payment is {due}."


def test_reply_includes_text_spoken_between_tool_calls(db):
    script = [reply("tool_use", text("Let me look that up."), tool("t", "lookup_loan", {"loan_number": "1"})),
              reply("end_turn", text("I couldn't find that loan."))]
    _, replies, _, _ = drive(db, script, ["hi"])
    assert replies[0] == "Let me look that up.\n\nI couldn't find that loan."


# ── 3. wrap-up hook ──────────────────────────────────────────────────────────

def test_wrap_up_writes_missing_note(db):
    l = loan(db, "current")
    script = [reply("tool_use", tool("t", "lookup_loan", {"loan_number": l["loan_number"]})),
              reply("end_turn", text("Found it. Goodbye!")),
              # the wrap-up turn:
              reply("tool_use", tool("c", "add_loan_comment", {"loan_number": l["loan_number"], "text": "Summary."})),
              reply("end_turn")]
    agent, _, wrapped, fake = drive(db, script, ["hi"], wrap_up=True)
    assert wrapped is True and agent.metrics["guardrail_interventions"] == 1
    assert "System notice" in fake.requests[2][-1]["content"]
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM comments WHERE author='agent'").fetchone()[0] == 1


def test_wrap_up_skipped_without_guardrails(db):
    l = loan(db, "current")
    script = [reply("tool_use", tool("t", "lookup_loan", {"loan_number": l["loan_number"]})),
              reply("end_turn", text("Goodbye!"))]
    _, _, wrapped, fake = drive(db, script, ["hi"], guardrails=False, wrap_up=True)
    assert wrapped is False and len(fake.requests) == 2


def test_wrap_up_skipped_when_note_exists(db):
    l = loan(db, "current")
    script = [reply("tool_use", tool("c", "add_loan_comment", {"loan_number": l["loan_number"], "text": "x"})),
              reply("end_turn", text("Goodbye!"))]
    _, _, wrapped, fake = drive(db, script, ["hi"], wrap_up=True)
    assert wrapped is False and len(fake.requests) == 2


# ── grader reads what the caller heard ───────────────────────────────────────

def test_grader_uses_spoken_reply_not_raw_model_text(tmp_path):
    seed.build(tmp_path / "seed.db")
    s = {x.id: x for x in load_all(tmp_path / "seed.db")}["dispute-attorney"]
    db = tmp_path / "episode.db"
    shutil.copy(tmp_path / "seed.db", db)
    due = f"${s.loan['amount_due_cents'] / 100:,.2f}"
    trace = tmp_path / "trace.jsonl"
    trace.write_text("\n".join(json.dumps(e) for e in [
        {"event": "assistant", "stop_reason": "end_turn", "content": [{"type": "text", "text": f"You owe {due}."}]},
        {"event": "reply", "text": f"{DEBT_DISCLOSURE} You owe {due}."},
    ]))
    result = asyncio.run(grader_mod.grade(s, db, trace, judge=None))
    assert "debt_disclosure" not in {c["check"] for c in result["violations"]}


def test_money_mentions():
    assert first_mention("You owe $7,454.00 today", 745400) == 8  # matches "$7,454" at the "$"
    assert first_mention("about $7,454 total", 745400) == 6
    assert first_mention("nothing here", 745400) is None
    assert money_strings(0) == []


def test_wrap_up_covers_loan_the_caller_named_but_agent_never_looked_up(db):
    l = loan(db, "current")
    script = [reply("end_turn", text("Sorry, I can only discuss a loan with the borrower.")),
              # wrap-up turn:
              reply("tool_use", tool("c", "add_loan_comment", {"loan_number": l["loan_number"],
                                                               "text": "Third party asked for info; declined."})),
              reply("end_turn")]
    agent, _, wrapped, fake = drive(db, script, [f"I'm his wife. What's owed on {l['loan_number']}?"], wrap_up=True)
    assert wrapped is True
    assert l["loan_number"] in fake.requests[1][-1]["content"]
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM comments WHERE author='agent'").fetchone()[0] == 1


def test_wrap_up_ignores_ten_digit_numbers_that_are_not_loans(db):
    script = [reply("end_turn", text("How can I help?"))]
    _, _, wrapped, fake = drive(db, script, ["Call me back at 5551234567."], wrap_up=True)
    assert wrapped is False and len(fake.requests) == 1
