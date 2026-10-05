"""Bridge between your agent loop and the servicing MCP server.

    async with ServicingTools(db_path, guardrails=True) as tools:
        tools.definitions          # list of tool dicts, ready for client.messages.create(tools=...)
        text, is_error = await tools.call("verify_identity", {...})

One ServicingTools = one MCP server process = one simulated phone call.
"""

from __future__ import annotations

import sys
from contextlib import AsyncExitStack
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


class ServicingTools:
    def __init__(self, db_path: str | Path, guardrails: bool = True):
        self.db_path = str(db_path)
        self.guardrails = guardrails
        self.definitions: list[dict] = []
        self._stack = AsyncExitStack()
        self._session: ClientSession | None = None

    async def __aenter__(self) -> "ServicingTools":
        args = ["-m", "servicing.mcp_server", "--db", self.db_path]
        if not self.guardrails:
            args.append("--no-guardrails")
        read, write = await self._stack.enter_async_context(
            stdio_client(StdioServerParameters(command=sys.executable, args=args)))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        listed = await self._session.list_tools()
        # MCP tool → Anthropic tool: same three things, slightly different names.
        self.definitions = [
            {"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
            for t in listed.tools
        ]
        return self

    async def __aexit__(self, *exc) -> None:
        await self._stack.aclose()

    async def call(self, name: str, tool_input: dict) -> tuple[str, bool]:
        """Run a tool. Returns (text for the tool_result, is_error). Never raises for tool failures:
        the model should see the error and recover."""
        result = await self._session.call_tool(name, tool_input)
        text = "\n".join(block.text for block in result.content if getattr(block, "text", None))
        return text, bool(result.is_error)
