"""SystemExit from a plugin import must not take the process with it."""

raise SystemExit("bad plugin")
