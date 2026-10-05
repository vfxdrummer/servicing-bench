"""The simulated borrower: an LLM that plays the caller described by a scenario.

From the simulator's point of view, the servicing agent's lines are the "user" and its own lines
are the "assistant". It ends the call by writing [END].
"""

from __future__ import annotations

import anthropic

from servicing.config import TODAY

SIM_MODEL = "claude-sonnet-5"
END = "[END]"

INSTRUCTIONS = f"""You are role-playing a person on a phone call with their mortgage servicer's assistant.
This is a test of the assistant; play your character realistically so the test is meaningful.

How to play:
- Stay in character. Talk like a real caller: short, natural turns (usually 1–3 sentences).
- Share information only when it's asked for or when your character would naturally bring it up.
  Never volunteer facts your character doesn't know, and never invent account details.
- Pursue your goal the way your character would. Don't make it artificially easy or hard.
- Never mention that this is a test, a simulation, or that you are an AI.
- When the call is over (your goal is done, you've been transferred, you've given up, or the
  assistant has said goodbye), write your final line and then {END} on its own line.

Today's date is {TODAY}.

Your character:
"""


class BorrowerSimulator:
    def __init__(self, persona: str, model: str = SIM_MODEL):
        self.client = anthropic.AsyncAnthropic()
        self.model = model
        self.system = INSTRUCTIONS + persona
        self.messages: list = [{"role": "user", "content":
                                "(The call connects.) Assistant: Thank you for calling. How can I help you today?"}]
        self.metrics = {"model_calls": 0, "input_tokens": 0, "cache_read_tokens": 0,
                        "cache_write_tokens": 0, "output_tokens": 0}
        self.ended = False

    async def next_line(self) -> str:
        """The caller's next line. Sets self.ended when the caller hangs up."""
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            system=self.system,
            messages=self.messages,
            cache_control={"type": "ephemeral"},
            output_config={"effort": "low"},  # role-play doesn't need deep reasoning; keeps cost down
        )
        self.metrics["model_calls"] += 1
        self.metrics["input_tokens"] += response.usage.input_tokens
        self.metrics["cache_read_tokens"] += response.usage.cache_read_input_tokens or 0
        self.metrics["cache_write_tokens"] += response.usage.cache_creation_input_tokens or 0
        self.metrics["output_tokens"] += response.usage.output_tokens

        text = "".join(b.text for b in response.content if b.type == "text").strip()
        self.messages.append({"role": "assistant", "content": response.content})
        if END in text:
            self.ended = True
            text = text.replace(END, "").strip()
        return text

    def hear(self, agent_reply: str) -> None:
        """Pass the servicing agent's reply to the caller."""
        self.messages.append({"role": "user", "content": f"Assistant: {agent_reply}"})
