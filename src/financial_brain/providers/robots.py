"""robots.txt, matched the way the standard describes (RFC 9309).

Python's ``urllib.robotparser`` matches rule paths by plain prefix, so a wildcard rule
like ``Disallow: /stocks/company_info/*`` matches nothing and a disallowed page reads as
allowed. Under-blocking is the one failure mode that matters when fetching someone
else's site, so this module implements ``*`` and ``$``, longest-match-wins, and
Allow-beats-Disallow on equal length.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse


class Robots:
    """A robots.txt matcher that understands ``*`` and ``$``.

    Python's ``urllib.robotparser`` matches rule paths by plain prefix, so a wildcard
    rule like Moneycontrol's ``Disallow: /stocks/company_info/*`` matches nothing and the
    path is reported as allowed. Under-blocking is the one failure mode that matters
    here, so the patterns are matched as the standard describes (RFC 9309): the
    longest matching rule wins, and Allow beats Disallow when both match equally.
    """

    def __init__(self, body: str, user_agent: str):
        groups: dict[str, list[tuple[str, str]]] = {}
        delays: dict[str, float] = {}
        current: list[str] = []
        for raw in body.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            field, _, value = (x.strip() for x in line.partition(":"))
            field = field.lower()
            if field == "user-agent":
                current = ([] if current and groups.get(current[-1]) else current)
                current.append(value.lower())
                groups.setdefault(value.lower(), [])
            elif field in ("allow", "disallow") and current:
                for ua in current:
                    groups.setdefault(ua, []).append((field, value))
            elif field == "crawl-delay" and current:
                for ua in current:
                    try:
                        delays[ua] = float(value)
                    except ValueError:
                        pass
        ua = user_agent.lower()
        name = next((k for k in groups if k != "*" and k and k in ua), "*")
        self.rules = groups.get(name, groups.get("*", []))
        self.crawl_delay = delays.get(name, delays.get("*"))

    @staticmethod
    def _to_regex(pattern: str) -> re.Pattern:
        out, i = [], 0
        while i < len(pattern):
            c = pattern[i]
            if c == "*":
                out.append(".*")
            elif c == "$" and i == len(pattern) - 1:
                out.append("$")
            else:
                out.append(re.escape(c))
            i += 1
        return re.compile("".join(out))

    def can_fetch(self, url: str) -> bool:
        path = urlparse(url).path or "/"
        if urlparse(url).query:
            path += "?" + urlparse(url).query
        best: tuple[int, bool] | None = None      # (match length, allowed)
        for field, pattern in self.rules:
            if not pattern:
                continue                            # "Disallow:" empty = allow all
            m = self._to_regex(pattern).match(path)
            if m:
                length = len(pattern.rstrip("*$"))
                allowed = field == "allow"
                if best is None or length > best[0] or (length == best[0] and allowed):
                    best = (length, allowed)
        return best[1] if best else True

