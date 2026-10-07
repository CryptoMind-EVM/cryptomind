"""
Agent V4 — Agent Registry

Manages agent registration and capability-based discovery.
"""

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class AgentMetadata:
    name: str
    display_name: str
    description: str
    capabilities: List[str]
    priority: int = 0
    hidden: bool = False  # If True, excluded from LLM classify prompt


class AgentRegistry:
    def __init__(self):
        self._agents: Dict[str, object] = {}
        self._metadata: Dict[str, AgentMetadata] = {}

    def register(self, agent, metadata: AgentMetadata) -> None:
        self._agents[metadata.name] = agent
        self._metadata[metadata.name] = metadata

    def get(self, name: str):
        return self._agents.get(name)

    def list_all(self) -> List[AgentMetadata]:
        return sorted(self._metadata.values(), key=lambda m: -m.priority)
