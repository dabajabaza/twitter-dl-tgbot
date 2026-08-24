"""Import blows up: the patterns are unknowable, so its links cannot be claimed."""


class _Engine:
    async def download(self, url, dest, *, on_progress=None, max_bytes=None):
        return []


raise RuntimeError("this provider cannot be imported")
