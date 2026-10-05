"""End-to-end through a real MCP client. Guards the protocol boundary: if the SDK changes how
errors surface, the agent would silently lose the reason a call failed."""

import asyncio
import shutil
import sqlite3
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from servicing import seed

EXPECTED_TOOLS = {"lookup_loan", "verify_identity", "get_loan_details", "take_payment",
                  "add_loan_comment", "create_task", "transfer_to_human"}


def run_session(db_path, *extra_args, steps):
    async def go():
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "servicing.mcp_server", "--db", str(db_path), *extra_args])
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                return await steps(s)
    return asyncio.run(go())


def make_db(tmp_path):
    seed.build(tmp_path / "seed.db")
    shutil.copy(tmp_path / "seed.db", tmp_path / "episode.db")
    path = tmp_path / "episode.db"
    loan = sqlite3.connect(path).execute(
        "SELECT loan_number, borrower_name, last4_ssn, zip FROM loans WHERE status='current' LIMIT 1").fetchone()
    return path, loan


def test_tools_and_policy_block_message(tmp_path):
    path, loan = make_db(tmp_path)

    async def steps(s):
        tools = {t.name for t in (await s.list_tools()).tools}
        blocked = await s.call_tool("get_loan_details", {"loan_number": loan[0]})
        verified = await s.call_tool("verify_identity", {"loan_number": loan[0], "full_name": loan[1],
                                                         "last4_ssn": loan[2], "zip_code": loan[3]})
        details = await s.call_tool("get_loan_details", {"loan_number": loan[0]})
        return tools, blocked, verified, details

    tools, blocked, verified, details = run_session(path, steps=steps)
    assert tools == EXPECTED_TOOLS
    assert blocked.is_error and "BLOCKED BY POLICY" in blocked.content[0].text
    assert '"verified": true' in verified.content[0].text
    assert not details.is_error and loan[0] in details.content[0].text


def test_tool_error_message_reaches_model(tmp_path):
    path, _ = make_db(tmp_path)

    async def steps(s):
        return await s.call_tool("lookup_loan", {"loan_number": "nope"}), \
               await s.call_tool("get_loan_details", {"loan_number": "0000000000"})

    lookup, missing = run_session(path, steps=steps)
    assert '"exists": false' in lookup.content[0].text
    assert missing.is_error and "No loan found" in missing.content[0].text


def test_no_guardrails_flag(tmp_path):
    path, loan = make_db(tmp_path)

    async def steps(s):
        return await s.call_tool("get_loan_details", {"loan_number": loan[0]})

    result = run_session(path, "--no-guardrails", steps=steps)
    assert not result.is_error
    outcomes = [r[0] for r in sqlite3.connect(path).execute("SELECT outcome FROM tool_calls ORDER BY seq")]
    assert outcomes == ["would_block", "ok"]
