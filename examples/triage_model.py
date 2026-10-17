"""A stand-in for a model, so the example runs offline. It is NOT a language model.

It reads the prompt on stdin and does what two of its instructions say:

* "Answer with JSON only": without it, the stand-in chats in prose instead of printing JSON.
* an "urgency" field in the JSON shape: without it, the JSON has no urgency.

The category and urgency come from keywords in the ticket. That is enough to show what a prompt regression
looks like: examples/prompts/triage_v2.txt lost the first instruction, triage_v3.txt lost the second.
To make one case flaky in a repeatable way, it gives another label on every third run of the case called
"ambiguous", as a sampled model might. Real models are plugged in with `type = "anthropic"` in the suite,
or by pointing `command` at your own script.
"""

import json
import os
import re
import sys

prompt = sys.stdin.read()
ticket = prompt.split("Ticket:", 1)[-1].strip()
low = ticket.lower()

RULES = [
    ("billing", ["refund", "charge", "invoice", "payment", "billing", "money back"]),
    ("account", ["password", "log in", "login", "locked out", "sign in", "2fa"]),
    ("bug", ["crash", "error", "exception", "stuck", "broken", "freezes", "does not work"]),
]
category = next((name for name, words in RULES if any(w in low for w in words)), "other")
urgent = any(
    w in low for w in ["urgent", "asap", "immediately", "locked out", "charged twice", "cannot work"]
)

if (
    os.environ.get("PROMPT_REGRESSION_CASE") == "ambiguous"
    and int(os.environ["PROMPT_REGRESSION_RUN"]) % 3 == 2
):
    category = "other"

first = re.split(r"(?<=[.!?])\s", ticket, maxsplit=1)[0][:90]
if "Answer with JSON only" in prompt:
    answer = {"category": category}
    if '"urgency"' in prompt:
        answer["urgency"] = "high" if urgent else "low"
    answer["summary"] = f"Customer writes: {first}"
    print(json.dumps(answer))
else:
    print(
        f"Thanks for passing this on! Category: {category}. Urgency: {'high' if urgent else 'low'}. "
        f"In short: {first}"
    )
