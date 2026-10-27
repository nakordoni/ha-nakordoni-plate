"""switch per gate: "gate automation enabled" (kill switch, restored on restart)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_OFF
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import NakordoniConfigEntry
from .const import CONF_GATE_ENTITY, CONF_GATE_ID, CONF_GATE_NAME


async def async_setup_entry(
    hass: HomeAssistant, entry: NakordoniConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    ctrl = entry.runtime_data.gates
    if ctrl is not None:
        async_add_entities(GateEnabledSwitch(entry, g) for g in ctrl.gates)


class GateEnabledSwitch(SwitchEntity, RestoreEntity):
    _attr_should_poll = False
    _attr_icon = "mdi:gate"

    def __init__(self, entry: NakordoniConfigEntry, gate: dict[str, Any]) -> None:
        self._entry = entry
        self._gid = gate[CONF_GATE_ID]
        label = gate.get(CONF_GATE_NAME) or gate[CONF_GATE_ENTITY]
        self._attr_name = f"nakordoni gate automation enabled {label}"
        self._attr_unique_id = f"{entry.entry_id}_gate_{self._gid}_enabled"
        self._attr_extra_state_attributes = {"gate_entity": gate[CONF_GATE_ENTITY]}

    @property
    def is_on(self) -> bool:
        return self._entry.runtime_data.gates.enabled.get(self._gid, True)

    async def async_added_to_hass(self) -> None:
        last = await self.async_get_last_state()
        if last is not None and last.state == STATE_OFF:
            self._entry.runtime_data.gates.enabled[self._gid] = False

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._entry.runtime_data.gates.enabled[self._gid] = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._entry.runtime_data.gates.enabled[self._gid] = False
        self.async_write_ha_state()
