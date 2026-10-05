"""How dollar amounts appear in speech. Shared by the output guardrail and the grader."""

from __future__ import annotations


def money_strings(cents: int) -> list[str]:
    """Spoken/written forms of an amount: 7,454.00 / 7454.00 / $7,454 / $7454."""
    if cents <= 0:
        return []
    d = cents / 100
    forms = [f"{d:,.2f}", f"{d:.2f}"]
    if cents % 100 == 0:
        forms += [f"${d:,.0f}", f"${d:.0f}"]
    return forms


def first_mention(text: str, cents: int) -> int | None:
    """Index of the first mention of the amount in text, or None."""
    hits = [i for f in money_strings(cents) if (i := text.find(f)) >= 0]
    return min(hits) if hits else None


def parse_dollars(s: str) -> int:
    """'$7,454.00' → 745400"""
    return round(float(s.replace("$", "").replace(",", "")) * 100)
