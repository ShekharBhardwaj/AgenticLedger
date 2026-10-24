# Meridian card network: dispute rules summary (invented for this demo)

These are not real network rules. They exist only for this exercise.

Reason codes
- 10.4 Unauthorized card-not-present transaction: the cardholder states
  the transaction was not made or authorized by them, and the card was in
  the cardholder's possession at the time.
- 13.1 Goods or services not received.
- 13.3 Not as described or defective.
- 12.6 Duplicate processing.

Timing
- The cardholder must report within 60 days of the statement date on which
  the transaction appeared.
- For reason code 10.4 the issuer may charge back the full transaction
  amount. Recurring transactions the cardholder set up are not covered by
  10.4 and must be handled as a cancellation request.

Evidence
- A network bulletin naming the merchant for compromised card-not-present
  processing on the transaction date counts as supporting evidence for
  10.4 and removes the need for a signed cardholder statement.
