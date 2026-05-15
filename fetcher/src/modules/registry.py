"""
Module registry — maps domain names to module classes.

Site modules decorate their class with :func:`register_module` and are
explicitly imported in ``modules/__init__.py`` so the decorator fires.
The :class:`ModuleManager` uses :func:`get_module` to dispatch.
"""

from __future__ import annotations

from typing import Type

from modules.base import BaseModule

_registry: dict[str, Type[BaseModule]] = {}


def register_module(domain: str):
    """Class decorator: registers *cls* under *domain*.

    Usage::

        @register_module(domain="reuters.com")
        class ReutersModule(BaseModule):
            ...
    """

    def decorator(cls: Type[BaseModule]) -> Type[BaseModule]:
        cls.domain = domain
        _registry[domain] = cls
        return cls

    return decorator


def get_module(domain: str) -> Type[BaseModule] | None:
    """Return the registered module class for *domain*, or ``None``."""
    return _registry.get(domain)


def get_all_modules() -> dict[str, Type[BaseModule]]:
    """Return a shallow copy of the full domain→module mapping."""
    return dict(_registry)
