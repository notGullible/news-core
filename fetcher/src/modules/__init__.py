"""Package root — explicit imports so each ``@register_module`` decorator fires."""

from .Reuters.module import ReutersModule  # noqa: F401

__all__ = ["ReutersModule"]
