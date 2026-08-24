"""A module that dies on import must break only itself, not the bot."""

raise RuntimeError("adapter import failed")
