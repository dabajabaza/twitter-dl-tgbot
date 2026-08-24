"""An adapters package exercising discovery: one good module per failure mode.

Discovery imports every non-underscore module here, so each file is one test
case; ``test.py`` is the only Adapter that loads READY.
"""
