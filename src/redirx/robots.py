"""
robots.txt path rules.

Only the generic link-following crawl consults these. Sitemaps and platform
APIs are publishing endpoints — a site that ships a sitemap is asking to be
read from it — and a URL the user pasted is a deliberate request. Page-by-page
crawling is the one discovery strategy that behaves like a bot, so it is the
one that has to honour Disallow.

Crawl-delay lives in rate_limit.py, which already parses robots.txt for
pacing; this module is only about which paths may be fetched.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse

DEFAULT_USER_AGENT = "redirxbot"


@dataclass
class Rule:
    allow: bool
    path: str
    pattern: re.Pattern = field(compare=False, repr=False)

    @property
    def specificity(self) -> int:
        """Length of the raw path, which is how robots.txt breaks ties."""
        return len(self.path)


def _compile(path: str) -> re.Pattern:
    """
    A robots path into a regex.

    Supports the two wildcards every major crawler honours: `*` for any run of
    characters and `$` for end-of-URL. Everything else is literal.
    """
    anchored_end = path.endswith("$")
    if anchored_end:
        path = path[:-1]
    parts = [re.escape(segment) for segment in path.split("*")]
    body = ".*".join(parts)
    return re.compile(f"^{body}{'$' if anchored_end else ''}")


def _agent_matches(token: str, user_agent: str) -> bool:
    token = token.strip().lower()
    return token == "*" or token in user_agent.lower()


def parse_rules(robots_txt: str, user_agent: str = DEFAULT_USER_AGENT) -> list[Rule]:
    """
    Allow/Disallow rules for `user_agent`.

    A group naming our agent wins outright over the wildcard group, per the
    spec: the most specific matching group applies and the others are ignored,
    rather than being merged.
    """
    specific: list[Rule] = []
    wildcard: list[Rule] = []
    # Consecutive User-agent lines share one group of rules.
    current_agents: list[str] = []
    starting_group = False

    for raw in robots_txt.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field_name, _, value = line.partition(":")
        field_name = field_name.strip().lower()
        value = value.strip()

        if field_name == "user-agent":
            if not starting_group:
                current_agents = []
                starting_group = True
            current_agents.append(value)
            continue

        if field_name not in ("allow", "disallow"):
            continue
        starting_group = False
        if not current_agents:
            continue
        # "Disallow:" with an empty value means allow everything.
        if field_name == "disallow" and not value:
            continue
        if not value.startswith("/"):
            continue

        rule = Rule(allow=field_name == "allow", path=value, pattern=_compile(value))
        for agent in current_agents:
            if agent.strip() == "*":
                wildcard.append(rule)
            elif _agent_matches(agent, user_agent):
                specific.append(rule)

    return specific or wildcard


class RobotsPolicy:
    """Whether a URL may be crawled, per robots.txt."""

    def __init__(self, rules: list[Rule]):
        self.rules = rules

    @classmethod
    def from_txt(cls, robots_txt: str, user_agent: str = DEFAULT_USER_AGENT) -> "RobotsPolicy":
        return cls(parse_rules(robots_txt, user_agent))

    @classmethod
    def allow_all(cls) -> "RobotsPolicy":
        """Used when robots.txt is missing or unreadable.

        A 404 means no restrictions were published; refusing to crawl because
        we could not read a file that does not exist would be wrong.
        """
        return cls([])

    def allows(self, url: str) -> bool:
        path = urlparse(url).path or "/"
        query = urlparse(url).query
        target = unquote(path) + (f"?{query}" if query else "")

        best: Rule | None = None
        for rule in self.rules:
            if not rule.pattern.match(target):
                continue
            if (
                best is None
                or rule.specificity > best.specificity
                # Longest match wins; Allow wins an exact-length tie.
                or (rule.specificity == best.specificity and rule.allow and not best.allow)
            ):
                best = rule
        return best.allow if best else True

    @property
    def blocks_everything(self) -> bool:
        """True for the `Disallow: /` that temporary hosts often force."""
        return not self.allows("/")
