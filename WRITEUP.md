# What broke when I put an AI agent on a mortgage line

*Draft. A write-up of [servicing-bench](README.md): what I built, what I measured, and the mistakes that taught me the most.*

Mortgage servicing is a good stress test for AI agents. The work is high-volume and mostly routine: take a payment, answer "when is my next payment due," log the call. But it's wrapped in rules that matter. You can't tell a caller's spouse their balance. You must give a debt-collection disclosure before discussing an overdue amount. You can't promise a struggling borrower a loan modification. A support bot that's right 95% of the time is a nice demo. A servicing agent that's wrong 5% of the time is a liability.

So I wanted to answer one question with data instead of vibes: **can a fast, cheap model do this job safely if the rules are enforced in code, or do you need the biggest model?**

## The setup

I built three things:

- **A mock servicing system**: 50 synthetic loans across every status (current, 30/60/90 days late, forbearance, bankruptcy, paid off), exposed to the agent as seven tools over MCP: look up a loan, verify identity, get details, take a payment, write a call note, open a task, transfer to a human.
- **An agent**: a plain tool-use loop with a 10-rule compliance policy as its system prompt.
- **A benchmark**: 16 scenarios, each a caller with a persona and a goal, played by a second model. Routine payments, an impostor guessing SSNs, a spouse who "just needs the balance," a borrower who lost their job, a bankruptcy, a servicemember being deployed, a caller posing as a supervisor, and a prompt injection planted in the account notes.

A call passes only if the end state is right (the correct payment, task, transfer and call note) **and** nothing in the call broke policy. I ran every scenario four times per condition and report *pass^4*, the share of scenarios that pass on all four runs. In a regulated business, consistency is the metric that matters.

The experiment had two conditions with an **identical prompt**: rules in the prompt only, or the same prompt plus guardrails in code.

## First result: the big model barely needed help

Claude Opus 5 passed nearly everything. Its only "failures" turned out to be my mistake: a caller said "I want to pay $500 now and the rest next week," and Opus offered to schedule both, read them back, got a yes, and did it. Good service. My scenario only allowed one payment.

That was the first lesson. A simulated caller can legitimately go beyond the persona you wrote, and your expectations have to cover every correct outcome, not just the one you pictured.

## The flaw in my own experiment

On an early run, the one difference between conditions was a $25,000 payment the prompt-only agent accepted and the guardrail would have blocked. A win for code, right?

No. The payment limit existed **only in the code**. Neither the prompt nor the tool description mentioned it. The prompt-only agent couldn't have complied; I was measuring missing information, not compliance. I added the limit to the policy so both conditions knew the same rules and differed only in enforcement.

If you compare "rules in the prompt" with "rules in code," both sides have to be told the same rules. Otherwise the gap is an artifact.

## Guardrails that guarded the wrong thing

Next I switched to Claude Haiku 4.5: about half the latency and 40% of the cost, which matters on a phone line. It was clearly worse, at 84% versus Opus's high-90s. But my guardrails made **zero** difference: both conditions scored the same.

Reading the transcripts showed why. My guardrails blocked unverified disclosure and over-limit payments, and Haiku never attempted either. Every one of its failures was in rules I hadn't enforced: transferring a caller *before* writing the call note, giving the debt disclosure one sentence too late, gently asking a borrower who'd just lost their job whether they could "make any payment toward that $10,231."

I had chosen guardrails a priori. They protected a surface this model didn't touch.

So I moved enforcement to where the failures actually were:
- the transfer tool refuses until the call has a note;
- an output check inserts the debt disclosure if a reply states the amount owed first;
- a post-call hook gives the agent one invisible turn to write a missing note after the caller hangs up.

## Final numbers

| | Pass rate | pass^4 | Calls with a violation | Agent cost / call | Median latency |
|---|---|---|---|---|---|
| Haiku 4.5, policy in prompt only | 83% | 56% | 16% | $0.025 | 1.3 s |
| Haiku 4.5 + code-enforced guardrails | 92% | 81% | 8% | $0.026 | 1.3 s |
| Opus 5 (either condition) | 100% | 100% | 0% | $0.065 | 2.6 s |

Guardrails halved Haiku's violations and lifted pass^4 by 25 points. That's about half the gap to Opus, at a fraction of the cost. Opus never triggered a single guardrail: on a strong model they cost nothing. They're insurance.

The remaining Haiku failures point at the next guardrails. My favorite: "I can see your loan is flagged for servicemember status," said by an agent that never looked the loan up. The caller had mentioned deployment. It was true by luck, and still a claim about data it never checked.

## The grader was a hypothesis too

I found more bugs by reading transcripts than my grader found:

- A judge rule flagged "we'll stop contacting you" after a cease-contact request as a *false promise*. It's confirming the caller's own legal right.
- My grader showed the judge tool output truncated at 300 characters. That cut off the servicemember field, so the judge called a true statement an invented fact.
- I added "for example, *your case is flagged as a priority*" to a rule, and the judge became suspicious of the word "flagged."

## Calibrating the judge, and myself

Soft rules ("did it make a promise it can't keep?") are graded by an LLM judge, which raises the obvious question: who checks the judge? I hand-labeled 40 judged calls blind and compared.

First pass: 82% agreement. The interesting part was the disagreements. They fell into three groups, and each needed a different fix:

1. **Judge errors**: fixed by tightening the rule wording.
2. **Ambiguous rules**: "if he can give me those details, I can verify him." Does that mean the borrower calls in, or the spouse relays his SSN? The rule didn't say, so I made it say.
3. **My errors**: I'd marked "pay online if you know her login" as fine. On review, it's coaching a third party to act as the borrower. The judge was right, and I'd missed it. That happened three times.

After fixes, the judge agrees with my labels 40/40, and it never missed a violation I found. With one caveat I take seriously: I tuned it on those same 40 calls, so the next step is validating on fresh ones.

## What I'd tell someone building this for real

1. **Measure where your model fails before deciding what to enforce in code.** Guardrails chosen up front can protect the wrong surface.
2. **Give both arms of an experiment the same knowledge.** Otherwise you're measuring the information gap, not the enforcement.
3. **Read transcripts, including the passing ones.** The grader is a hypothesis.
4. **Calibrate the judge against humans, and review the human labels too.** Disagreement isn't automatically the judge's fault.
5. **Report consistency, not just averages.** pass^4 told a much sharper story than pass rate.

*All data is synthetic and the policy is a simplified illustration, not legal advice. Code, scenarios, labels and the full findings log are in the repo.*
