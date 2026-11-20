"""Shared infrastructure for the command-line entry points.

Modules in this package sit *below* ``scripts/`` in the dependency direction:
scripts may import them, but they do not import ``autoplay`` or
``newstar_sdk``.  Keeping it that way lets any script reuse configuration
helpers and the message-reporting channel without pulling in game logic.
"""
