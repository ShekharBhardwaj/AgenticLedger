# Case: a disputed card transaction

The folder `docs/` is what the agent sees: a dispute form, the customer's
profile and statements, the merchant record, the card network's rules,
the bank's dispute policy, prior disputes, a travel notice, a network
bulletin, and two unrelated dispute forms as noise. `TASK.md` is the
task. Every rule and every name in here is invented for the demo.

`ANSWER-KEY.md` holds the one right answer and the wrong ones to watch
for. `check.py` judges a `decision.md` against it; the loops use it as
the only judge of done.

The answer takes several hops, and no single document has it: the form
names the merchant the customer's way; the March statement shows two
charges from that merchant, one the customer acknowledges; the merchant
record ties the trading names together and points at a network bulletin;
the bulletin and the network rules give the reason code; the policy gives
the deadline rule and the amount rule; the prior disputes decide
escalation. Forget any one fact and the memo is wrong.
