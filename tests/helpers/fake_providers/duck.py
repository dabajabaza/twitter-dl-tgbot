"""Looks like a Provider but does not subclass one: discovery keys on the type."""

import re


class _Engine:
    async def download(self, url, dest, *, on_progress=None, max_bytes=None):
        return []


class NotReallyAProvider:
    name = "Duck"
    hint = "duck.example/<id>"
    post_link = re.compile(r"https://duck\.example/(?P<id>\d+)")
