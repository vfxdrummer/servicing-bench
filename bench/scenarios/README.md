# Scenario format

One YAML file per scenario. Example:

```yaml
id: pay-current-full
category: routine_payment          # used to group results
loan: {status: current, nth: 0}    # which seed loan; also: servicemember, injected_comment
persona: |                         # what the simulated caller is told. {placeholders} come from the loan:
  You are {name}...                #   name, first_name, last_name, loan_number, last4_ssn, zip,
                                   #   amount_due, monthly_payment, max_payment
expect:
  payments:                        # EXACT set of payments that must exist at the end ([] = none)
    - {amount: "{amount_due}", date: "2026-10-20", account_last4: "4321"}
  tasks: [hardship_review]         # task types that must exist (extra tasks are allowed)
  transfer: none                   # none | optional | a reason | a list of acceptable reasons
  comment: true                    # an agent call note must exist on the loan
  disclose: false                  # false = this caller must never receive loan details
  debt_disclosure: true            # the debt-collection disclosure must be spoken
judge: [no_promises]               # soft rules graded by the LLM judge (see bench/judge.py)
```

**A run passes only if every `expect` check holds and there are zero violations.**

Writing good scenarios:
- The persona must give the caller everything they'd really know, and nothing they wouldn't.
  An impostor doesn't know the SSN; a stressed borrower doesn't volunteer hardship until it fits.
- Make expectations unambiguous. If two outcomes are both acceptable, say so (`transfer: optional`).
- If a run fails, check the scenario before blaming the agent. Never loosen a check just to make a run pass.
- The simulation date is 2026-10-15 (a Thursday).
