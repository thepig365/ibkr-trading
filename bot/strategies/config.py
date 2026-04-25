"""Loader + dataclasses for ``config/strategies.yaml``.

This module is intentionally small and dependency-light:

* ``yaml`` is imported lazily so the module remains importable even
  when PyYAML is missing (a missing config falls back to a deterministic
  empty config).
* No broker / IBKR imports.
* Never raises on a missing or malformed file. Returns
  :class:`StrategyRuntimeConfig` with safe defaults instead so the UI
  stays fully renderable.

Hard invariants (validated in :py:meth:`StrategyRuntimeConfig.from_dict`):

* ``defaults.paper_only`` is always coerced to ``True``.
* ``strategies[*].paper_execution_allowed`` is always coerced to
  ``False`` at this stage of the project. The on-disk YAML may say
  ``true`` and we simply ignore it — there is no order placement code
  path wired to this file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StrategyDefaults:
    paper_only: bool = True
    paper_execution_allowed: bool = False
    research_only: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_only": self.paper_only,
            "paper_execution_allowed": self.paper_execution_allowed,
            "research_only": self.research_only,
        }


@dataclass(frozen=True)
class StrategyEntryConfig:
    """Per-strategy runtime config (independent of the strategy adapter)."""

    key: str
    enabled: bool = False
    paper_execution_allowed: bool = False  # always coerced to False
    params: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "enabled": self.enabled,
            "paper_execution_allowed": self.paper_execution_allowed,
            "params": dict(self.params),
        }


@dataclass(frozen=True)
class StrategyRuntimeConfig:
    """Parsed contents of ``config/strategies.yaml``."""

    source_path: str | None = None
    defaults: StrategyDefaults = field(default_factory=StrategyDefaults)
    strategies: dict[str, StrategyEntryConfig] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    @classmethod
    def empty(cls, source_path: str | None = None, *, note: str = "") -> "StrategyRuntimeConfig":
        notes = [note] if note else []
        return cls(source_path=source_path, notes=notes)

    # ------------------------------------------------------------------
    @classmethod
    def from_dict(
        cls,
        raw: Any,
        *,
        source_path: str | None = None,
    ) -> "StrategyRuntimeConfig":
        if not isinstance(raw, dict):
            return cls.empty(source_path=source_path, note="strategies.yaml: not a mapping")

        defaults_raw = raw.get("defaults") if isinstance(raw.get("defaults"), dict) else {}
        defaults = StrategyDefaults(
            paper_only=True,  # invariant
            paper_execution_allowed=False,  # invariant at this stage
            research_only=bool(defaults_raw.get("research_only", True)),
        )

        out: dict[str, StrategyEntryConfig] = {}
        notes: list[str] = []
        strategies_raw = raw.get("strategies")
        if isinstance(strategies_raw, dict):
            for key, value in strategies_raw.items():
                k = str(key).strip()
                if not k:
                    notes.append("strategies.yaml: empty key skipped")
                    continue
                if not isinstance(value, dict):
                    notes.append(
                        f"strategies.yaml: entry {k!r} is not a mapping; skipped"
                    )
                    continue
                params_raw = value.get("params") if isinstance(value.get("params"), dict) else {}
                out[k] = StrategyEntryConfig(
                    key=k,
                    enabled=bool(value.get("enabled", False)),
                    # invariant: paper_execution_allowed is False at this stage
                    paper_execution_allowed=False,
                    params=dict(params_raw),
                )
        else:
            notes.append("strategies.yaml: 'strategies' missing or wrong type")

        return cls(
            source_path=source_path,
            defaults=defaults,
            strategies=out,
            notes=notes,
        )

    # ------------------------------------------------------------------
    def get(self, key: str) -> StrategyEntryConfig:
        if key in self.strategies:
            return self.strategies[key]
        return StrategyEntryConfig(key=key, enabled=False)

    def enabled_keys(self) -> list[str]:
        return [k for k, v in self.strategies.items() if v.enabled]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "defaults": self.defaults.to_dict(),
            "strategies": {k: v.to_dict() for k, v in self.strategies.items()},
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_strategies_config(path: Path | str | None = None) -> StrategyRuntimeConfig:
    """Load ``config/strategies.yaml`` (or ``path``) gracefully.

    Returns an empty :class:`StrategyRuntimeConfig` if:

    * the file is missing,
    * PyYAML is not installed,
    * the file is malformed.

    Never raises.
    """
    p = Path(path) if path else Path("config/strategies.yaml")
    if not p.exists() or not p.is_file():
        return StrategyRuntimeConfig.empty(
            source_path=str(p),
            note=f"strategies.yaml: not found at {p}",
        )
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        return StrategyRuntimeConfig.empty(
            source_path=str(p),
            note="strategies.yaml: PyYAML not installed; using defaults",
        )
    try:
        with p.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as exc:  # type: ignore[attr-defined]
        return StrategyRuntimeConfig.empty(
            source_path=str(p),
            note=f"strategies.yaml: failed to parse ({exc})",
        )
    return StrategyRuntimeConfig.from_dict(data, source_path=str(p))


__all__ = [
    "StrategyDefaults",
    "StrategyEntryConfig",
    "StrategyRuntimeConfig",
    "load_strategies_config",
]
