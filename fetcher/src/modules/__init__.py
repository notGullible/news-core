"""Package root — explicit imports so each ``@register_module`` decorator fires."""

from .Reuters.module import ReutersModule, ReutersWWWModule  # noqa: F401

__all__ = ["ReutersModule", "ReutersWWWModule"]
