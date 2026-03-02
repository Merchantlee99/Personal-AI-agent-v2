import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

@dataclass(frozen=True)
class AgentSpec:
    id: str
    display_name: str
    role: str


def _candidate_config_paths() -> list[Path]:
    candidates: list[Path] = []
    env_path = os.getenv("AGENT_CONFIG_PATH")
    if env_path:
        candidates.append(Path(env_path))
    candidates.extend(
        [
            Path("/app/config/agents.json"),
            Path(__file__).resolve().parents[2] / "config" / "agents.json",
            Path.cwd() / "config" / "agents.json",
            Path.cwd().parent / "config" / "agents.json",
        ]
    )
    return candidates


def _load_config() -> dict[str, object]:
    for candidate in _candidate_config_paths():
        if not candidate.is_file():
            continue
        try:
            with candidate.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
    raise RuntimeError("agents config not found or invalid. expected config/agents.json")


def _parse_config(raw_config: dict[str, object]) -> tuple[tuple[str, ...], dict[str, str], dict[str, AgentSpec]]:
    canonical_source = raw_config.get("canonical_ids")
    if not isinstance(canonical_source, list):
        raise RuntimeError("agents config must include canonical_ids list")
    canonical_ids: list[str] = []
    for value in canonical_source:
        token = str(value).strip().lower()
        if token and token not in canonical_ids:
            canonical_ids.append(token)
    if not canonical_ids:
        raise RuntimeError("agents config canonical_ids cannot be empty")

    canonical_set = set(canonical_ids)

    aliases_source = raw_config.get("aliases")
    aliases: dict[str, str] = {}
    if isinstance(aliases_source, dict):
        for key, value in aliases_source.items():
            alias = str(key).strip().lower()
            target = str(value).strip().lower()
            if alias and target in canonical_set:
                aliases[alias] = target

    agents_source = raw_config.get("agents")
    agents_map = agents_source if isinstance(agents_source, dict) else {}
    registry: dict[str, AgentSpec] = {}
    for canonical_id in canonical_ids:
        details = agents_map.get(canonical_id, {})
        details_dict = details if isinstance(details, dict) else {}
        display_name = str(details_dict.get("display_name") or canonical_id.title())
        role = str(details_dict.get("role") or "unspecified")
        registry[canonical_id] = AgentSpec(id=canonical_id, display_name=display_name, role=role)

    return tuple(canonical_ids), aliases, registry


_config = _load_config()
_canonical_ids, _aliases, _registry = _parse_config(_config)
CANONICAL_AGENT_IDS: Final[tuple[str, ...]] = tuple(_canonical_ids)
LEGACY_ALIASES: Final[dict[str, str]] = dict(_aliases)
AGENT_REGISTRY: Final[dict[str, AgentSpec]] = dict(_registry)


def normalize_agent_id(raw_agent_id: str) -> str | None:
    normalized = raw_agent_id.strip().lower()
    if normalized in AGENT_REGISTRY:
        return normalized
    if normalized in LEGACY_ALIASES:
        return LEGACY_ALIASES[normalized]
    return None
