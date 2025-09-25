"""AI integration helpers (legacy import shim)."""

from integrations.tuya.ai_tools import (
    create_and_enable_automation,
    delete_automations,
    describe_space,
    inspect_device,
    propose_automation,
    set_automation_state,
    trigger_scene,
    update_automation,
)

__all__ = [
    "create_and_enable_automation",
    "delete_automations",
    "describe_space",
    "inspect_device",
    "propose_automation",
    "set_automation_state",
    "trigger_scene",
    "update_automation",
]
