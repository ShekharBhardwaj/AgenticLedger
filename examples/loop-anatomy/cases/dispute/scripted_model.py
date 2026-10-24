"""A scripted model for the dispute case: answers in the model's place from
a script, so both loops can run through the ledger for as long as you like
at no cost. Only the model's words are scripted; every tool result is
real (the documents are really read, the memo really written and judged).

It speaks the Anthropic messages wire (POST /v1/messages) and sits where
the real API would, as the upstream of a named ledger instance:

    python scripted_model.py [port]          # default 9911
    LOOP_DELAY=8 python scripted_model.py    # seconds per call, with jitter

The ledger records it under the model id the scripts send (scripted-model),
which it cannot price. Say what it is when you show it.
"""

import json
import os
import random
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROMISE = "<promise>DONE</promise>"
DELAY = float(os.environ.get("LOOP_DELAY", "8"))

MEMO = """# Decision memo: DSP-26-0319

Decision: provisional credit of 482.00 for the 9 March 2026 NORTHGATE SPORTS
LTD card-not-present transaction on checking account ending 4471 (card
ending 8830, in the customer's possession).

Post by: 26 March 2026. Policy DP-3 gives 10 business days from the day the
dispute was received (Thursday 12 March 2026); the account was opened in
2019, so the 2 business day rule does not apply.

Basis: policy DP-4.2 with network reason code 10.4 (unauthorized
card-not-present transaction, card in the customer's possession). Network
bulletin 2026-03 names Northgate Sports Ltd for 9 March 2026 card-not-present
transactions through a compromised terminal, so no signed cardholder
statement is needed.

Not credited: the 2 March NORTHGATE SPRT ONLINE charge of 129.95, which the
customer acknowledges (DP-4.2), and the NG FIT CLUB MONTHLY 14.99 recurring
charge the customer set up (DP-6, a cancellation request if wanted).

Escalation: not required. The only prior dispute, DSP-25-1187, was against
City Parking Authority, a different merchant, and there has been one
dispute in the previous 12 months (DP-5).

Documents used: 01-dispute-form, 05-statement-2026-03,
06-merchant-record-northgate, 11-network-bulletin-2026-03,
07-card-network-rules, 08-dispute-policy, 09-prior-disputes.
"""

NOTES = {
    1: """Round 1
- Folder listed: dispute form, customer profile, three statements, merchant record, network rules, dispute policy, prior disputes, travel notice, a network bulletin, two other customers' forms.
- Case DSP-26-0319, customer Daniel Okafor, checking ending 4471, debit card ending 8830, card in his possession.
- Disputed: about 482 dollars from "Northgate Sports" around 9 March 2026. Dispute received 12 March 2026.
- He acknowledges an earlier online shoe purchase from the same merchant in early March. He was travelling that week.
- Next: find the charges on the March statement.
""",
    2: """Round 2
- March statement: 02 Mar NORTHGATE SPRT ONLINE 129.95 (card not present, the shoes he acknowledges), 09 Mar NORTHGATE SPORTS LTD 482.00 (card not present, the disputed one), 14 Mar NG FIT CLUB MONTHLY 14.99 (recurring). 482.00 appears once.
- Customer profile: account opened 14 August 2019, no flags.
- Next: the earlier statements for the subscription history, then the merchant record.
""",
    3: """Round 3
- January and February statements both show NG FIT CLUB MONTHLY 14.99: a long-standing subscription. Nothing else from Northgate before March.
- Next: the merchant record, then the network rules and our policy.
""",
    4: """Round 4
- Checked the merchant record: Northgate Sports Ltd is the merchant, acquirer Harbor Acquiring.
- Next: the network rules.
""",
    5: """Round 5
- Network rules: reason code 10.4 fits (unauthorized card-not-present, card in possession); reported within 60 days; recurring charges are not covered by 10.4.
- The rules say a network bulletin naming the merchant settles the evidence. My notes had nothing about one, so I re-read the merchant record: it points to bulletin 2026-03, and NG FIT CLUB and NORTHGATE SPRT ONLINE are Northgate's trading names.
- Bulletin 2026-03: Northgate Sports Ltd, compromised terminal, 9 March 2026 card-not-present transactions are unauthorized, no signed statement needed; other dates not affected.
- Next: our policy (deadline, amount).
""",
    6: """Round 6
- Policy DP-3: 10 business days from 12 March 2026 (account from 2019, so not the 2 day rule); no holidays in March 2026: post by 26 March 2026.
- DP-4.2: credit the full 482.00; not the acknowledged 129.95. DP-6: the 14.99 recurring charge is a cancellation, not a dispute.
- Next: the memo also needs an escalation decision.
""",
    7: """Round 7
- My round 6 note said escalation was still open but not which rule decides it, so I re-read the policy: DP-5 escalates when a prior dispute against the same merchant went against the customer within 12 months, or three or more disputes in 12 months.
- Prior disputes: one, DSP-25-1187, City Parking Authority, different merchant, resolved against him 30 January 2026. One dispute in 12 months: no escalation.
- Next: the travel notice.
""",
    8: """Round 8
- Travel notice: Lisbon 5 to 11 March 2026, filed 1 March. The Lisbon charges are card present; the disputed charge is card not present. Consistent, not needed for the decision.
- Next: the two other forms in the folder, to be sure they are unrelated.
""",
    9: """Round 9
- DSP-26-0311 (Marta Silva, Oakline Furniture) is unrelated. DSP-26-0322 (Owen Hartley) is another customer's NG FIT CLUB recurring charge complaint: a cancellation case, unrelated to ours.
- Next: confirm the exact amounts on the March statement once more, then draft.
""",
    10: """Round 10
- Confirmed on the March statement: 482.00 on 9 March is the only disputed charge; 129.95 on 2 March is the acknowledged one; both card not present.
- Next: write a draft memo.
""",
    11: """Round 11
- Draft written to draft-decision.md and re-read against DP-7: amount 482.00, deadline 26 March 2026, DP-4.2 and 10.4, no escalation with reason, documents listed.
- Next: one last check of the bulletin's date coverage, then the final decision.md.
""",
    12: """Round 12
- Bulletin re-read: it covers 9 March 2026 only; other dates are not affected, so the 2 March charge stays the customer's.
- Next: write decision.md from the draft and stop.
""",
}


def think(text):
    return {"type": "text", "text": text}


def tool(tool_name, **args):
    return {"type": "tool_use", "name": tool_name, "input": args}


def write_notes(upto: int):
    return tool("write_file", name="notes.md", content="".join(NOTES[i] for i in range(1, upto + 1)))


def read(name, text):
    return [think(text), tool("read_doc", name=name)]


def search(query, text):
    return [think(text), tool("search", query=query)]


# Each round of Ralph is a short fresh conversation: the first move is
# always reading the notebook; the rest follow the script for that round.
# Rounds 4 and 6 are forgetting rounds: each writes a note that leaves
# out something the next round needs, and the next round re-reads a
# document to get it back.
RALPH_ROUNDS = [
    [  # round 1
        [think("The notebook is empty, so this is the first round. First, what is in the folder."), tool("list_docs")],
        read("01-dispute-form.md", "The dispute form first."),
        [think("Recording the case facts before I stop."), write_notes(1)],
        [think("Round 1 done; notebook updated.")],
    ],
    [  # round 2
        search("Northgate", "The notes say to find the charges on the statement. First a search for the merchant."),
        read("05-statement-2026-03.md", "Two Northgate charges in March plus a subscription. I'll read the full March statement."),
        search("482", "Confirming the disputed amount appears only once."),
        read("02-customer-profile.md", "The deadline rule depends on when the account was opened. Checking the profile."),
        [think("Writing the three charges and the account date down."), write_notes(2)],
        [think("Round 2 done; notebook updated.")],
    ],
    [  # round 3
        read("04-statement-2026-02.md", "The notes say to check the subscription history. February first."),
        read("03-statement-2026-01.md", "And January."),
        search("NG FIT CLUB", "Who is NG FIT CLUB? Searching."),
        [think("Subscription history noted."), write_notes(3)],
        [think("Round 3 done; notebook updated.")],
    ],
    [  # round 4, forgetting round
        read("06-merchant-record-northgate.md", "Next step in the notes: the merchant record."),
        [think("Merchant confirmed. Noting it and moving on to the rules next round."), write_notes(4)],
        [think("Round 4 done; notebook updated.")],
    ],
    [  # round 5: pays for round 4's thin note
        read("07-card-network-rules.md", "Next step: the network rules."),
        search("10.4", "Where 10.4 is referenced."),
        search("60 days", "The reporting window."),
        read("06-merchant-record-northgate.md", "The rules say a bulletin naming the merchant would settle this, but my notes say nothing about one. I'll check the merchant record again."),
        read("11-network-bulletin-2026-03.md", "It points to bulletin 2026-03. Reading it."),
        [think("This time I'm writing down everything the memo will need."), write_notes(5)],
        [think("Round 5 done; notebook updated.")],
    ],
    [  # round 6, forgetting round: deadline and amount, but not which rule decides escalation
        read("08-dispute-policy.md", "Next: our policy for the deadline and the amount."),
        search("business days", "Counting the deadline needs the business day rule exactly."),
        search("holiday", "Any holidays in March 2026 that would move the deadline?"),
        [think("Deadline and amount settled. Escalation still to decide."), write_notes(6)],
        [think("Round 6 done; notebook updated.")],
    ],
    [  # round 7: pays for round 6's note
        read("08-dispute-policy.md", "The notes say escalation is still open but not which rule decides it. Re-reading the policy for that."),
        read("09-prior-disputes.md", "DP-5 depends on prior disputes. Checking them."),
        search("12 months", "The look-back window for repeat disputes."),
        search("DSP-25", "Any other prior cases referenced?"),
        [think("Escalation settled: none. Writing the rule and the reason down."), write_notes(7)],
        [think("Round 7 done; notebook updated.")],
    ],
    [  # round 8
        read("10-travel-notice.md", "The customer said he was travelling. Checking the travel notice."),
        search("Lisbon", "Matching the travel dates against the statement."),
        search("card not present", "Matching the transaction types against the travel dates."),
        [think("Travel checked and noted."), write_notes(8)],
        [think("Round 8 done; notebook updated.")],
    ],
    [  # round 9
        read("12-dispute-form-other-customer-a.md", "Two other forms are in the folder. Making sure they are not part of our case."),
        read("13-dispute-form-other-customer-b.md", "This one also names Northgate. Reading it."),
        search("cancell", "The other Northgate form is a cancellation. Checking how our policy treats cancellations."),
        [think("Both unrelated; noted."), write_notes(9)],
        [think("Round 9 done; notebook updated.")],
    ],
    [  # round 10
        read("05-statement-2026-03.md", "Before drafting, confirming the exact amounts and types on the statement once more."),
        search("129.95", "The acknowledged charge is the 2 March one."),
        search("2 Mar", "Confirming its date."),
        [think("Amounts confirmed."), write_notes(10)],
        [think("Round 10 done; notebook updated.")],
    ],
    [  # round 11
        [think("The notes hold every fact. Writing a draft memo first."), tool("write_file", name="draft-decision.md", content=MEMO)],
        search("DP-7", "What the policy requires a memo to state."),
        read("draft-decision.md", "Re-reading the draft against DP-7 and the notes."),
        [think("The draft matches."), write_notes(11)],
        [think("Round 11 done; notebook updated.")],
    ],
    [  # round 12
        read("11-network-bulletin-2026-03.md", "One last check: does the bulletin cover 9 March only?"),
        search("9 March", "Confirming the disputed charge is dated 9 March."),
        [think("Coverage confirmed."), write_notes(12)],
        [think("Round 12 done; notebook updated.")],
    ],
    [  # round 13
        [think("Writing decision.md from the checked draft."), tool("write_file", name="decision.md", content=MEMO)],
        [think(PROMISE)],
    ],
]

# ReAct: one conversation, thorough to a fault. It reads every document,
# searches for each fact it relies on, double-checks the ones that decide
# money and dates, drafts, verifies, then writes the memo.
REACT_STEPS = [
    [think("First, what is in the folder."), tool("list_docs")],
    read("01-dispute-form.md", "The dispute form first."),
    search("Northgate", "The customer names the merchant his own way. Searching the folder for it."),
    read("05-statement-2026-03.md", "Two Northgate charges in March. Reading the full statement."),
    read("02-customer-profile.md", "The deadline rule may depend on the account's age. Checking the profile."),
    read("04-statement-2026-02.md", "Is the 14.99 charge a long-standing subscription? Checking February."),
    read("03-statement-2026-01.md", "And January."),
    search("NG FIT CLUB", "Who is NG FIT CLUB? Searching."),
    read("06-merchant-record-northgate.md", "The merchant record should tie the trading names together."),
    search("bulletin", "The record mentions a network bulletin. Finding it."),
    read("11-network-bulletin-2026-03.md", "Reading bulletin 2026-03."),
    search("HARB-77120", "Checking whether the compromised terminal id appears anywhere else."),
    read("07-card-network-rules.md", "Now the network rules for the reason code."),
    search("10.4", "Confirming how 10.4 is defined and where it is referenced."),
    search("60 days", "The reporting window: 60 days from the statement date."),
    read("08-dispute-policy.md", "Our policy sets the deadline and the amount."),
    search("business days", "The business day rule decides the deadline."),
    search("holiday", "Any holidays in March 2026 that would move the deadline?"),
    read("09-prior-disputes.md", "DP-5 depends on prior disputes. Checking them."),
    search("DSP-25", "Any other prior cases referenced?"),
    read("10-travel-notice.md", "The customer said he was travelling. Checking the notice."),
    search("Lisbon", "Matching the Lisbon charges on the statement to the travel dates."),
    read("12-dispute-form-other-customer-a.md", "Two other forms are in the folder. Making sure they are not related."),
    read("13-dispute-form-other-customer-b.md", "This one also names Northgate. Reading it."),
    search("cancell", "The other Northgate form is a cancellation. Checking how our policy treats cancellations."),
    search("recurring", "Confirming the recurring charge rule in both the network rules and our policy."),
    read("05-statement-2026-03.md", "Back to the statement to confirm the exact amounts and transaction types."),
    search("482", "Confirming the disputed amount appears only once."),
    search("129.95", "And that the acknowledged charge is the 2 March one."),
    search("card not present", "Both Northgate charges are card-not-present; confirming."),
    read("08-dispute-policy.md", "Re-reading DP-4 to be exact about what is credited and what is not."),
    search("DP-4", "Where DP-4 is referenced."),
    search("DP-5", "The escalation clause, word for word."),
    search("escalat", "Any other mention of escalation."),
    search("12 months", "The look-back window for repeat disputes."),
    read("09-prior-disputes.md", "Counting disputes in the last 12 months: one, a different merchant."),
    search("signed", "Does the bulletin remove the signed statement requirement? Checking the exact words."),
    read("07-card-network-rules.md", "Re-reading the recurring charge exclusion under 10.4."),
    search("possession", "The card was in the customer's possession; confirming the wording that matters for 10.4."),
    search("12 March", "The day the dispute was received, for the deadline count."),
    search("2019", "Account opened 2019: the 10 day rule applies, not the 2 day rule."),
    [think("Every fact is confirmed. Writing a draft memo before the final."), tool("write_file", name="draft-decision.md", content=MEMO)],
    read("draft-decision.md", "Re-reading the draft."),
    read("02-customer-profile.md", "One more check on the account open date in the draft."),
    search("9 March", "Confirming the bulletin covers 9 March and the disputed charge is dated 9 March."),
    read("11-network-bulletin-2026-03.md", "The bulletin says other dates are not affected: the 2 March charge stays the customer's."),
    search("2 Mar", "Confirming the 2 March charge is the acknowledged one."),
    [think("The draft holds. Writing decision.md."), tool("write_file", name="decision.md", content=MEMO)],
    read("decision.md", "Verifying the final memo as written."),
    [think("Checking the folder once more: decision.md is in place."), tool("list_docs")],
    [think(PROMISE)],
]


def notes_from(messages: list) -> str:
    """The notebook's content as the model saw it: the first tool_result."""
    for m in messages:
        if m.get("role") == "user" and isinstance(m.get("content"), list):
            for b in m["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    c = b.get("content")
                    return c if isinstance(c, str) else "".join(x.get("text", "") for x in c or [] if isinstance(x, dict))
    return ""


def script_for(body: dict) -> list:
    messages = body.get("messages", [])
    prompt = ""
    first = messages[0]["content"] if messages else ""
    prompt = first if isinstance(first, str) else "".join(b.get("text", "") for b in first if isinstance(b, dict))
    assistant_turns = sum(1 for m in messages if m.get("role") == "assistant")
    if "notes.md is your notebook" in prompt:
        if assistant_turns == 0:
            return [think("Reading my notebook first, as the loop requires."), tool("read_doc", name="notes.md")]
        rounds_done = notes_from(messages).count("Round ")
        plan = RALPH_ROUNDS[min(rounds_done, len(RALPH_ROUNDS) - 1)]
        return plan[min(assistant_turns - 1, len(plan) - 1)]
    return REACT_STEPS[min(assistant_turns, len(REACT_STEPS) - 1)]


def estimate_tokens(obj) -> int:
    return max(1, len(json.dumps(obj, default=str)) // 4)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # one quiet line per call
        sys.stderr.write(f"scripted-model: {fmt % args}\n")

    def do_POST(self):
        if self.path.rstrip("/") != "/v1/messages":
            self.send_error(404, "the scripted model speaks only POST /v1/messages")
            return
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        content = script_for(body)
        for i, b in enumerate(content):
            if b.get("type") == "tool_use":
                b["id"] = f"toolu_{int(time.time() * 1000)}_{i}"
        has_tool = any(b.get("type") == "tool_use" for b in content)
        delay = max(0.0, DELAY * random.uniform(0.7, 1.3))
        time.sleep(delay)
        resp = {
            "id": f"msg_scripted_{int(time.time() * 1000)}",
            "type": "message",
            "role": "assistant",
            "model": body.get("model", "scripted-model"),
            "content": content,
            "stop_reason": "tool_use" if has_tool else "end_turn",
            "stop_sequence": None,
            "usage": {
                "input_tokens": estimate_tokens(body.get("system", "")) + estimate_tokens(body.get("tools", []))
                                + estimate_tokens(body.get("messages", [])),
                "output_tokens": estimate_tokens(content),
            },
        }
        data = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9911
    print(f"scripted model on http://127.0.0.1:{port}/v1/messages, {DELAY:g}s per call", file=sys.stderr)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
