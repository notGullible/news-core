"""Shared infrastructure for the NG pipeline.

Re-exports the most commonly-used symbols so consumers can do::

    from common import config
    from common import MyRedis, MyPostgres
    from common.logging_config import setup_logging
"""

# No re-exports needed — consumers import submodules directly.
