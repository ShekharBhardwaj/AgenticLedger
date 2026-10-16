"""Small text helpers. Some of these have bugs; the tests say which."""

import re


def slugify(text):
    """'Hello, World!' -> 'hello-world'."""
    words = re.split(r"[^A-Za-z0-9]+", text)
    return "-".join(words)


def word_count(text):
    """Number of words, where words are separated by any whitespace."""
    return len(text.split(" "))


def truncate(text, limit):
    """Cut text so the result is at most `limit` characters long, ending
    in '...' when anything was cut."""
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def parse_duration(spec):
    """'1h30m15s' -> 5415 seconds. Any of the three parts may be missing."""
    total = 0
    for amount, unit in re.findall(r"(\d+)([hms])", spec):
        if unit == "h":
            total += int(amount) * 3600
        elif unit == "s":
            total += int(amount)
    return total
