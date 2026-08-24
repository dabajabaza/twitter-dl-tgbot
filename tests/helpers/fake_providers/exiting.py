"""A module calling sys.exit must not take the whole bot down with it."""

import sys

sys.exit("provider decided to exit at import")
