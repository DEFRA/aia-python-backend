import os

from app.config import config


class PipelineConfig:
    def __init__(self, max_priority_actions: int = 10) -> None:
        ordered: list[str] = []
        for agent_types in config.templates.values():
            for agent_type in agent_types:
                if agent_type not in ordered:
                    ordered.append(agent_type)

        self.agent_types: list[str] = ordered or [config.orchestrator.default_agent_type]
        self.section_labels: dict[str, str] = {
            agent_type: agent_type.replace("_", " ").title()
            for agent_type in self.agent_types
        }

        env_value = os.getenv("MAX_PRIORITY_ACTIONS")
        if env_value and env_value.isdigit():
            self.max_priority_actions = int(env_value)
        else:
            self.max_priority_actions = max_priority_actions
