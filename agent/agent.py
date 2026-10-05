"""The servicing agent: the same loop as agent-from-scratch, with these additions.

1. Tools come from the MCP server (via ServicingTools) instead of local functions.
2. It's a CONVERSATION: `respond()` handles one caller turn and returns everything the agent
   said during it (a voice agent speaks its in-between remarks too, like "let me pull that up").
3. Everything is recorded (trace + metrics), because the benchmark grades from it.
4. With guardrails on, two harness-level guardrails enforce rules the model kept missing:
   - OUTPUT guardrail: on a delinquent loan, if a reply states the amount owed before the
     debt-collection disclosure has been given, the disclosure is inserted first (policy rule 2).
   - WRAP-UP hook: if the caller hangs up and a loan has no call note yet, the agent gets one
     final, caller-invisible turn to write it (policy rule 9).
   (The third, "no transfer before a call note", lives in the servicing system's transfer tool.)
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import anthropic

from agent.mcp_tools import ServicingTools
from servicing.config import TODAY
from servicing.money import first_mention, parse_dollars

MODEL = "claude-opus-5"
MAX_TOOL_ROUNDS = 10  # per caller turn; a stuck agent must not loop forever on someone's phone call
POLICY = (Path(__file__).parent / "policy.md").read_text()
FALLBACK_MODELS = {"claude-opus-5", "claude-opus-5-5", "claude-fable-5-1"}

DEBT_DISCLOSURE = ("This is a communication from a debt collector. This is an attempt to collect a debt, "
                   "and any information obtained will be used for that purpose.")
WRAP_UP_NOTICE = ("[System notice, not from the caller] The call has ended. Loan(s) {loans} have no call note "
                  "yet. Call add_loan_comment now with a short factual summary of this call. Do not write "
                  "anything to the caller.")


class ServicingAgent:
    def __init__(self, tools: ServicingTools, model: str = MODEL, trace_path: Path | None = None,
                 on_tool_call=None):
        self.client = anthropic.AsyncAnthropic()
        self.tools = tools
        self.guardrails = tools.guardrails
        self.model = model
        # The simulation has a fixed date; the agent needs it to schedule payments correctly.
        self.system = f"{POLICY}\n\nToday's date is {TODAY}."
        self.messages: list = []
        self.trace_path = trace_path
        self.on_tool_call = on_tool_call  # optional callback so a UI can show tool calls live
        self.transferred = False
        # What the agent has learned during the call, used by the guardrails.
        self.identified_loans: set[str] = set()
        self.mentioned_numbers: set[str] = set()  # 10-digit numbers the caller said (maybe loan numbers)
        self.noted_loans: set[str] = set()
        self.loan_status: dict[str, dict] = {}   # loan_number → {"status", "amount_due_cents"}
        self.disclosure_given = False
        self.metrics = {"turns": 0, "model_calls": 0, "tool_calls": 0, "input_tokens": 0,
                        "cache_read_tokens": 0, "cache_write_tokens": 0, "output_tokens": 0,
                        "latency_s": [], "guardrail_interventions": 0}

    def _fallback_params(self) -> dict:
        # If the model's safety classifier declines a request, the API re-runs it on a fallback
        # model instead of returning a refusal. Only sent to models where that's documented.
        if self.model in FALLBACK_MODELS:
            return {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        return {}

    def _trace(self, event: str, **data) -> None:
        if self.trace_path:
            with self.trace_path.open("a") as f:
                f.write(json.dumps({"event": event, **data}, default=str) + "\n")

    def _add_user_text(self, text: str) -> None:
        # Roles must alternate. If the last message is already a user turn (tool results after a
        # capped tool loop), add the text to it instead of starting a second user turn.
        if self.messages and self.messages[-1]["role"] == "user":
            last = self.messages[-1]
            if isinstance(last["content"], str):
                last["content"] = [{"type": "text", "text": last["content"]}]
            last["content"].append({"type": "text", "text": text})
        else:
            self.messages.append({"role": "user", "content": text})

    def _observe_tool(self, name: str, tool_input: dict, output: str, is_error: bool) -> None:
        """Track call state from tool traffic: which loans came up, which have notes, loan status."""
        if is_error:
            return
        loan = tool_input.get("loan_number")
        if name == "lookup_loan":
            if json.loads(output).get("exists"):
                self.identified_loans.add(loan.strip())
        elif loan:
            self.identified_loans.add(loan.strip())
        if name == "add_loan_comment":
            self.noted_loans.add(loan.strip())
        if name == "get_loan_details":
            d = json.loads(output)
            self.loan_status[d["loan_number"]] = {"status": d["status"],
                                                  "amount_due_cents": parse_dollars(d["amount_due"])}
        if name == "transfer_to_human":
            self.transferred = True

    async def _run(self, max_rounds: int = MAX_TOOL_ROUNDS) -> list[str]:
        """The agent loop. Returns the text the agent produced, in order."""
        said: list[str] = []
        for _ in range(max_rounds):
            # STEP 1: send the whole conversation every time.
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
                **self._fallback_params(),
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
            text = "".join(b.text for b in response.content if b.type == "text").strip()
            if text:
                said.append(text)

            # STEP 3: anything other than tool_use means this turn is over.
            if response.stop_reason != "tool_use":
                if response.stop_reason == "refusal" and not said:
                    said.append("I'm sorry, I can't help with that. Let me get you to a team member.")
                return said

            # STEP 4: run every requested tool; errors go back to the model, never crash.
            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                output, is_error = await self.tools.call(block.name, block.input)
                self.metrics["tool_calls"] += 1
                self._observe_tool(block.name, block.input, output, is_error)
                if self.on_tool_call:
                    self.on_tool_call(block.name, block.input, output, is_error)
                self._trace("tool", name=block.name, input=block.input, output=output, is_error=is_error)
                result = {"type": "tool_result", "tool_use_id": block.id, "content": output}
                if is_error:
                    result["is_error"] = True
                results.append(result)

            # STEP 5: all results in ONE user message.
            self.messages.append({"role": "user", "content": results})

        self._trace("max_tool_rounds")
        said.append("I'm sorry, I'm having trouble with that. Let me transfer you to a team member.")
        return said

    def _output_guardrail(self, reply: str) -> str:
        """Policy rule 2, enforced in code: disclosure before the first statement of the amount owed."""
        if self.disclosure_given or "attempt to collect a debt" in reply.lower():
            self.disclosure_given = self.disclosure_given or "attempt to collect a debt" in reply.lower()
            return reply
        for loan in self.loan_status.values():
            if loan["status"].startswith("delinquent_") and first_mention(reply, loan["amount_due_cents"]) is not None:
                self.disclosure_given = True
                self.metrics["guardrail_interventions"] += 1
                self._trace("guardrail", rule="debt_disclosure", action="inserted disclosure before reply")
                return f"{DEBT_DISCLOSURE} {reply}"
        return reply

    async def respond(self, caller_text: str) -> str:
        """Handle one message from the caller. Returns everything the agent says this turn."""
        self.metrics["turns"] += 1
        self._add_user_text(caller_text)
        self._trace("caller", text=caller_text)
        self.mentioned_numbers |= set(re.findall(r"\b\d{10}\b", caller_text))
        reply = "\n\n".join(await self._run())
        if self.guardrails:
            reply = self._output_guardrail(reply)
        elif "attempt to collect a debt" in reply.lower():
            self.disclosure_given = True
        self._trace("reply", text=reply)  # exactly what the caller hears; the grader reads this
        return reply

    async def wrap_up(self) -> bool:
        """Post-call hook (guardrails only): make sure every identified loan has a call note.
        Returns True if a wrap-up turn was needed."""
        if not self.guardrails or self.transferred:
            return False
        # Loans the caller named but the agent never looked up still need a note (policy rule 9).
        # Confirm each is a real loan first, so a 10-digit phone number doesn't trigger a note.
        for number in sorted(self.mentioned_numbers - self.identified_loans - self.noted_loans):
            output, is_error = await self.tools.call("lookup_loan", {"loan_number": number})
            if not is_error and json.loads(output).get("exists"):
                self.identified_loans.add(number)
        missing = sorted(self.identified_loans - self.noted_loans)
        if not missing:
            return False
        self.metrics["guardrail_interventions"] += 1
        self._trace("guardrail", rule="call_note", action=f"wrap-up turn for {missing}")
        self._add_user_text(WRAP_UP_NOTICE.format(loans=", ".join(missing)))
        await self._run(max_rounds=3)
        return True
