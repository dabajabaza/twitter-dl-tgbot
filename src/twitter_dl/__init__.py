"""Transition shim: the bot lives in :mod:`clipivore` now.

The deployed rc.d script starts the bot as ``python -m twitter_dl``, and that
script lives in a different repository, on a host this one cannot reach. So the
old module name has to keep working for exactly as long as it takes the
automation repo to switch to ``python -m clipivore`` — then this package goes.
"""
