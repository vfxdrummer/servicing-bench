"""The servicing agent: the same loop as agent-from-scratch, with three changes.

1. Tools come from the MCP server (via ServicingTools) instead of local functions.
2. It's a CONVERSATION: after the tool loop finishes, the agent replies to the caller
   and waits for their next message. `respond()` is one caller turn.
3. Everything is recorded (trace + metrics), because Week 2's benchmark grades from it.

The terminal chat in chat.py and the Week 2 benchmark runner both drive this class.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import anthropic

from agent.mcp_tools import ServicingTools
from servicing.config import TODAY

MODEL = "claude-opus-5"
MAX_TOOL_ROUNDS = 10  # per caller turn; a stuck agent must not loop forever on someone's phone call
POLICY = (Path(__file__).parent / "policy.md").read_text()


class ServicingAgent:
    def __init__(self, tools: ServicingTools, model: str = MODEL, trace_path: Path | None = None,
                 on_tool_call=None):
        self.client = anthropic.AsyncAnthropic()
        self.tools = tools
        self.model = model
        # The simulation has a fixed date; the agent needs it to schedule payments correctly.
        self.system = f"{POLICY}\n\nToday's date is {TODAY}."
        self.messages: list = []
        self.trace_path = trace_path
        self.on_tool_call = on_tool_call  # optional callback so a UI can show tool calls live
        self.transferred = False
        self.metrics = {"turns": 0, "model_calls": 0, "tool_calls": 0, "input_tokens": 0,
                        "cache_read_tokens": 0, "cache_write_tokens": 0, "output_tokens": 0,
                        "latency_s": []}

    def _trace(self, event: str, **data) -> None:
        if self.trace_path:
            with self.trace_path.open("a") as f:
                f.write(json.dumps({"event": event, **data}, default=str) + "\n")

    async def respond(self, caller_text: str) -> str:
        """Handle one message from the caller. Runs tools as needed; returns the agent's reply."""
        self.metrics["turns"] += 1
        self.messages.append({"role": "user", "content": caller_text})
        self._trace("caller", text=caller_text)

        for _ in range(MAX_TOOL_ROUNDS):
            # Same as agent-from-scratch STEP 1: send the whole conversation every time.
            started = time.monotonic()
            response = await self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=self.system,
                tools=self.tools.definitions,
                messages=self.messages,
                # Cache the conversation prefix: every call resends the whole history, so
                # without caching a 10-turn call pays for turn 1 ten times.
                cache_control={"type": "ephemeral"},
                # If the model's safety classifier declines a request, the API re-runs it on a
                # fallback model instead of returning a refusal.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            self.metrics["latency_s"].append(round(time.monotonic() - started, 2))
            self.metrics["model_calls"] += 1
            self.metrics["input_tokens"] += response.usage.input_tokens
            self.metrics["cache_read_tokens"] += response.usage.cache_read_input_tokens or 0
            self.metrics["cache_write_tokens"] += response.usage.cache_creation_input_tokens or 0
            self.metrics["output_tokens"] += response.usage.output_tokens

            # STEP 2: keep the full reply (tool_use ids must survive for the next request).
            self.messages.append({"role": "assistant", "content": response.content})
            self._trace("assistant", stop_reason=response.stop_reason,
                        content=[b.model_dump() for b in response.content])

            reply = "".join(b.text for b in response.content if b.type == "text").strip()

            # STEP 3: anything other than tool_use means this caller turn is over.
            if response.stop_reason != "tool_use":
                if response.stop_reason == "refusal":
                    reply = reply or "I'm sorry, I can't help with that. Let me get you to a team member."
                return reply

            # STEP 4: run every requested tool; errors go back to the model, never crash.
            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                text, is_error = await self.tools.call(block.name, block.input)
                self.metrics["tool_calls"] += 1
                if block.name == "transfer_to_human" and not is_error:
                    self.transferred = True
                if self.on_tool_call:
                    self.on_tool_call(block.name, block.input, text, is_error)
                self._trace("tool", name=block.name, input=block.input, output=text, is_error=is_error)
                result = {"type": "tool_result", "tool_use_id": block.id, "content": text}
                if is_error:
                    result["is_error"] = True
                results.append(result)

            # STEP 5: all results in ONE user message.
            self.messages.append({"role": "user", "content": results})

        self._trace("max_tool_rounds")
        return "I'm sorry, I'm having trouble with that. Let me transfer you to a team member."
