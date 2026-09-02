"""Discovery and guarded execution for Team B and Team C plugins."""

from __future__ import annotations

import importlib
import os
from types import ModuleType
from typing import Any, Callable

from .plugin_contracts import (
    PLUGIN_API_VERSION,
    ContractError,
    candidate_to_dict,
    normalize_plugin_result,
    to_builtin,
    unavailable_result,
)


DEFAULT_SECURITY_MODULE = "team_plugins.security_evaluator"
DEFAULT_ENGINEERING_MODULE = "team_plugins.engineering_evaluator"


class PluginLoadError(RuntimeError):
    """Raised when a configured plugin cannot be imported or lacks its entrypoint."""


class PluginExecutionError(RuntimeError):
    """Raised in strict mode when a plugin call fails or returns invalid data."""


def _compatible_version(module: ModuleType) -> bool:
    version = str(getattr(module, "PLUGIN_API_VERSION", ""))
    return version.split(".", 1)[0] == PLUGIN_API_VERSION.split(".", 1)[0]


def _load_module(
    module_name: str,
    required_functions: tuple[str, ...],
    fallback_module: str,
    allow_fallback: bool,
    reload_module: bool,
) -> ModuleType:
    requested_name = module_name
    try:
        module = importlib.import_module(requested_name)
        if reload_module:
            module = importlib.reload(module)
    except (ImportError, ModuleNotFoundError) as exc:
        if not allow_fallback or requested_name == fallback_module:
            raise PluginLoadError(f"Unable to import plugin {requested_name!r}") from exc
        module = importlib.import_module(fallback_module)

    if not _compatible_version(module):
        raise PluginLoadError(
            f"Plugin {module.__name__!r} uses API version "
            f"{getattr(module, 'PLUGIN_API_VERSION', None)!r}; expected {PLUGIN_API_VERSION!r}"
        )
    missing = [name for name in required_functions if not callable(getattr(module, name, None))]
    if missing:
        raise PluginLoadError(
            f"Plugin {module.__name__!r} is missing callable entrypoints: {', '.join(missing)}"
        )
    return module


def load_security_evaluator(
    module_name: str | None = None,
    allow_fallback: bool = True,
    reload_module: bool = False,
) -> ModuleType:
    """Load the configured Team B module and validate its handshake."""

    configured = module_name or os.getenv("UKNIT_SECURITY_PLUGIN", DEFAULT_SECURITY_MODULE)
    return _load_module(
        configured,
        ("evaluate_security",),
        DEFAULT_SECURITY_MODULE,
        allow_fallback,
        reload_module,
    )


def load_engineering_evaluator(
    module_name: str | None = None,
    allow_fallback: bool = True,
    reload_module: bool = False,
) -> ModuleType:
    """Load the configured Team C module and validate its handshake."""

    configured = module_name or os.getenv("UKNIT_ENGINEERING_PLUGIN", DEFAULT_ENGINEERING_MODULE)
    return _load_module(
        configured,
        ("validate_candidate", "evaluate_performance"),
        DEFAULT_ENGINEERING_MODULE,
        allow_fallback,
        reload_module,
    )


def _context_dict(context: dict[str, Any] | None) -> dict[str, Any]:
    if context is None:
        return {}
    if not isinstance(context, dict):
        raise ContractError("Plugin context must be a dictionary or None")
    return to_builtin(context)


class PluginLoader:
    """Load and call Team B/C providers through a contract-checked boundary."""

    def __init__(
        self,
        security_module: str | None = None,
        engineering_module: str | None = None,
        *,
        strict: bool = False,
        allow_fallback: bool = True,
        reload_modules: bool = False,
    ) -> None:
        self.security_module_name = security_module
        self.engineering_module_name = engineering_module
        self.strict = strict
        self.allow_fallback = allow_fallback
        self.reload_modules = reload_modules
        self.security = load_security_evaluator(
            security_module,
            allow_fallback=allow_fallback,
            reload_module=reload_modules,
        )
        self.engineering = load_engineering_evaluator(
            engineering_module,
            allow_fallback=allow_fallback,
            reload_module=reload_modules,
        )

    def __getstate__(self) -> dict[str, Any]:
        # Module objects are not picklable.  Workers reload the configured names.
        return {
            "security_module": self.security_module_name,
            "engineering_module": self.engineering_module_name,
            "strict": self.strict,
            "allow_fallback": self.allow_fallback,
            "reload_modules": False,
        }

    def __setstate__(self, state: dict[str, Any]) -> None:
        self.__init__(**state)

    @staticmethod
    def _plugin_name(module: ModuleType) -> str:
        return str(getattr(module, "PLUGIN_NAME", module.__name__))

    def _call(
        self,
        module: ModuleType,
        function_name: str,
        result_type: str,
        candidate: Any,
        context: dict[str, Any] | None,
        *,
        prevalidate_candidate: bool,
    ) -> dict[str, Any]:
        candidate_id = None
        try:
            payload = candidate_to_dict(candidate, validate=prevalidate_candidate)
            candidate_id = payload.get("candidate_id")
            function: Callable[..., Any] = getattr(module, function_name)
            raw_result = function(payload, _context_dict(context))
            return normalize_plugin_result(raw_result, result_type, candidate_id=candidate_id)
        except Exception as exc:
            if self.strict:
                raise PluginExecutionError(
                    f"{self._plugin_name(module)}.{function_name} failed: {exc}"
                ) from exc
            return unavailable_result(
                result_type,
                candidate_id,
                f"{self._plugin_name(module)}.{function_name} failed: {exc}",
                self._plugin_name(module),
            )

    def evaluate_security(
        self, candidate: Any, context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return self._call(
            self.security,
            "evaluate_security",
            "security",
            candidate,
            context,
            prevalidate_candidate=True,
        )

    def validate_candidate(
        self, candidate: Any, context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return self._call(
            self.engineering,
            "validate_candidate",
            "validation",
            candidate,
            context,
            prevalidate_candidate=False,
        )

    def validate(
        self, candidate: Any, context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return self.validate_candidate(candidate, context)

    def evaluate_performance(
        self, candidate: Any, context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return self._call(
            self.engineering,
            "evaluate_performance",
            "performance",
            candidate,
            context,
            prevalidate_candidate=True,
        )

    def describe_plugins(self) -> dict[str, dict[str, str]]:
        return {
            "security": {
                "module": self.security.__name__,
                "name": self._plugin_name(self.security),
                "api_version": str(getattr(self.security, "PLUGIN_API_VERSION", "")),
            },
            "engineering": {
                "module": self.engineering.__name__,
                "name": self._plugin_name(self.engineering),
                "api_version": str(getattr(self.engineering, "PLUGIN_API_VERSION", "")),
            },
        }


_default_loader: PluginLoader | None = None


def get_default_loader() -> PluginLoader:
    global _default_loader
    if _default_loader is None:
        _default_loader = PluginLoader()
    return _default_loader


def evaluate_security(candidate: Any, context: dict[str, Any] | None = None) -> dict[str, Any]:
    return get_default_loader().evaluate_security(candidate, context)


def validate_candidate(candidate: Any, context: dict[str, Any] | None = None) -> dict[str, Any]:
    return get_default_loader().validate_candidate(candidate, context)


def evaluate_performance(candidate: Any, context: dict[str, Any] | None = None) -> dict[str, Any]:
    return get_default_loader().evaluate_performance(candidate, context)


__all__ = [
    "DEFAULT_SECURITY_MODULE",
    "DEFAULT_ENGINEERING_MODULE",
    "PluginLoadError",
    "PluginExecutionError",
    "PluginLoader",
    "load_security_evaluator",
    "load_engineering_evaluator",
    "get_default_loader",
    "evaluate_security",
    "validate_candidate",
    "evaluate_performance",
]
