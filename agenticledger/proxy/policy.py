"""Model and provider allow and deny lists: the fleet's rules, and each
team card's own on top. Refuse only. A call that fails a rule is turned
away with the rule named; nothing is rewritten, rerouted, or substituted.

Patterns are shell globs (``claude-*``, ``gpt-4o``, ``bedrock``) matched
case-insensitively against the model id and the provider name the proxy
resolved for the call. Deny wins over allow, and an allow list that exists
admits only what it names. The fleet lists always apply; a card can narrow
them further but never widen them (the operator's decision, recorded in
GAMEPLAN under 0.15).
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Any, Iterable, Optional

LIST_FIELDS = ("allow_models", "deny_models", "allow_providers", "deny_providers")

# How many allow-list patterns a refusal spells out before "and N more":
# the reason names the rule without turning into a wall of text.
_NAMED_PATTERNS = 6


def parse_list(raw: Any) -> tuple[str, ...]:
    """A comma-separated string (env, config file, card column) or any
    sequence of patterns, as a normalized tuple with blanks dropped."""
    if raw is None:
        return ()
    items: Iterable[Any] = raw.split(",") if isinstance(raw, str) else list(raw)
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if text:
            out.append(text)
    return tuple(out)


def _first_match(patterns: tuple[str, ...], value: str) -> Optional[str]:
    low = value.lower()
    for pattern in patterns:
        if fnmatch.fnmatchcase(low, pattern.lower()):
            return pattern
    return None


def _spell(patterns: tuple[str, ...]) -> str:
    shown = ", ".join(patterns[:_NAMED_PATTERNS])
    rest = len(patterns) - _NAMED_PATTERNS
    return f"{shown} and {rest} more" if rest > 0 else shown


@dataclass(frozen=True)
class Policy:
    """One owner's four lists. ``scope`` names the owner in a refusal
    ("the fleet", "team 'marketing'") so the agent's error and the
    ledger row both say whose rule fired."""

    allow_models: tuple[str, ...] = ()
    deny_models: tuple[str, ...] = ()
    allow_providers: tuple[str, ...] = ()
    deny_providers: tuple[str, ...] = ()
    scope: str = "the fleet"

    @classmethod
    def from_values(cls, allow_models: Any = None, deny_models: Any = None,
                    allow_providers: Any = None, deny_providers: Any = None,
                    scope: str = "the fleet") -> "Policy":
        return cls(parse_list(allow_models), parse_list(deny_models),
                   parse_list(allow_providers), parse_list(deny_providers), scope)

    @classmethod
    def from_card(cls, card: dict) -> Optional["Policy"]:
        """A team card's lists, or None when the card carries none."""
        policy = cls.from_values(*(card.get(f) for f in LIST_FIELDS),
                                 scope=f"team '{card.get('name')}'")
        return None if policy.empty() else policy

    def empty(self) -> bool:
        return not (self.allow_models or self.deny_models
                    or self.allow_providers or self.deny_providers)

    def as_dict(self) -> dict[str, list[str]]:
        return {f: list(getattr(self, f)) for f in LIST_FIELDS}

    def check(self, model_id: Optional[str], provider: Optional[str]) -> Optional[str]:
        """The reason this call is refused under these lists, or None.
        Provider before model, deny before allow: the broadest rule that
        applies is the one named."""
        model = (model_id or "").strip()
        prov = (provider or "").strip()
        hit = _first_match(self.deny_providers, prov)
        if hit:
            return f"provider '{prov}' is on {self.scope}'s deny list (rule '{hit}')"
        hit = _first_match(self.deny_models, model)
        if hit:
            return f"model '{model}' is on {self.scope}'s deny list (rule '{hit}')"
        if self.allow_providers and not _first_match(self.allow_providers, prov):
            return (f"provider '{prov}' is not on {self.scope}'s allow list "
                    f"({_spell(self.allow_providers)})")
        if self.allow_models and not _first_match(self.allow_models, model):
            return (f"model '{model}' is not on {self.scope}'s allow list "
                    f"({_spell(self.allow_models)})")
        return None


def check_policies(model_id: Optional[str], provider: Optional[str],
                   *policies: Optional[Policy]) -> Optional[str]:
    """Every policy in turn (fleet first, then the card): the first refusal
    wins, so a card can only narrow what the fleet already allows."""
    for policy in policies:
        if policy is None:
            continue
        reason = policy.check(model_id, provider)
        if reason:
            return reason
    return None


def refusal_type(reason: str) -> str:
    """The error type an agent sees for a list refusal."""
    return "provider_not_allowed" if reason.startswith("provider ") else "model_not_allowed"
