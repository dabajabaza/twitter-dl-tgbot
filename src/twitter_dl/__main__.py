"""``python -m twitter_dl`` — forwards to :mod:`clipivore`. See the package docstring."""

import contextlib

from clipivore.__main__ import main

if __name__ == "__main__":
    # SystemExit from the single-instance guard is deliberately not suppressed.
    with contextlib.suppress(KeyboardInterrupt):
        main()
