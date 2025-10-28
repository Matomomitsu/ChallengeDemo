"""Mappings between GoodWe status codes and Tuya status enums."""

STATUS_MAP = {
    0: "idle",
    1: "carregando",
    2: "descarregando",
    3: "generating",
    4: "grid_export",
    5: "fault",
}

DEFAULT_STATUS = "idle"
