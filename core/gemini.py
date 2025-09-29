from google import genai
from google.genai import types
import json
import os
import re
import time
import unicodedata
from typing import Any, Dict, Optional, Sequence

from dotenv import load_dotenv

import core.goodweApi as goodweApi
from core import usage_optimizer
from datetime import datetime
from zoneinfo import ZoneInfo

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

load_dotenv(".env")

# Global chat instance for maintaining conversation context
chat_instance = None
DEFAULT_STATION_NAME = "Bauer"
DEFAULT_STATION_ID = "6ef62eb2-7959-4c49-ad0a-0ce75565023a"
DEFAULT_TUYA_SPACE_ID = os.getenv("TUYA_SPACE_ID") or "265551117"

def _normalize_label(value: str) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    normalized = normalized.lower()
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized).strip()
    return normalized


def _build_lookup(entries: Sequence[Dict[str, Any]], *, name_fields: Sequence[str]) -> Dict[str, str]:
    lookup: Dict[str, str] = {}
    for entry in entries or []:
        identifier = entry.get("id") or entry.get("rule_id")
        if not identifier:
            continue
        seen: set[str] = set()
        for field in name_fields:
            candidate = entry.get(field)
            if not candidate:
                continue
            label = str(candidate).strip()
            if not label:
                continue
            normalized = _normalize_label(label)
            if not normalized or normalized in seen:
                continue
            lookup[normalized] = identifier
            seen.add(normalized)
    return lookup


def _update_tuya_cache(space_id: str, payload: Dict[str, Any]) -> None:
    if not payload:
        return
    devices = payload.get("devices") or []
    scenes = payload.get("scenes") or []
    _TUYA_CONTEXT_CACHE["space_id"] = space_id
    _TUYA_CONTEXT_CACHE["payload"] = payload
    _TUYA_CONTEXT_CACHE["timestamp"] = time.time()
    _TUYA_CONTEXT_CACHE["device_lookup"] = _build_lookup(devices, name_fields=("customName", "name"))
    _TUYA_CONTEXT_CACHE["scene_lookup"] = _build_lookup(scenes, name_fields=("customName", "name", "display_name"))


def _invalidate_tuya_cache() -> None:
    _TUYA_CONTEXT_CACHE["payload"] = None
    _TUYA_CONTEXT_CACHE["timestamp"] = 0.0
    _TUYA_CONTEXT_CACHE["device_lookup"] = {}
    _TUYA_CONTEXT_CACHE["scene_lookup"] = {}


def _get_cached_tuya_context(space_id: Optional[str]) -> Optional[Dict[str, Any]]:
    target = space_id or DEFAULT_TUYA_SPACE_ID
    cache_space = _TUYA_CONTEXT_CACHE.get("space_id")
    cached = _TUYA_CONTEXT_CACHE.get("payload")
    if not cached or cache_space != target:
        return None
    if (time.time() - _TUYA_CONTEXT_CACHE.get("timestamp", 0.0)) > _TUYA_CACHE_TTL_SECONDS:
        return None
    return cached


def _refresh_tuya_context(space_id: Optional[str]) -> Optional[Dict[str, Any]]:
    target = space_id or DEFAULT_TUYA_SPACE_ID
    if not target:
        return None
    try:
        payload = describe_space(target)
    except Exception as exc:  # pragma: no cover - network failure
        print(f"⚠️ Could not refresh Tuya context: {exc}")
        return None
    if isinstance(payload, dict):
        _update_tuya_cache(target, payload)
    return payload


def _should_bootstrap_tuya(message: str) -> bool:
    if not message:
        return False
    lower = message.lower()
    return any(keyword in lower for keyword in _AUTOMATION_KEYWORDS)


def _format_tuya_context(payload: Dict[str, Any]) -> str:
    space_id = payload.get("space_id") or DEFAULT_TUYA_SPACE_ID or ""
    devices = payload.get("devices") or []
    scenes = payload.get("scenes") or []

    device_lines = []
    for device in devices:
        friendly = (device.get("customName") or device.get("name") or "Dispositivo").strip()
        device_id = device.get("id", "")
        device_lines.append(f"- {friendly} -> {device_id}")
    if not device_lines:
        device_lines.append("- nenhum dispositivo disponível")

    scene_lines = []
    for scene in scenes:
        friendly = (scene.get("name") or scene.get("display_name") or "Cena").strip()
        rule_id = scene.get("rule_id") or scene.get("id") or ""
        scene_lines.append(f"- {friendly} -> {rule_id}")
    if not scene_lines:
        scene_lines.append("- nenhuma cena cadastrada")

    context_lines = [
        "[Contexto interno Tuya - não revele IDs ou códigos]",
        f"space_id interno: {space_id}",
        "Dispositivos conhecidos:",
        *device_lines,
        "Cenas conhecidas:",
        *scene_lines,
        "Use apenas os nomes amigáveis nas respostas; utilize os IDs acima somente nas chamadas de função.",
    ]
    return "\n".join(context_lines)


def _augment_user_input_with_tuya_context(user_input: str) -> tuple[str, bool]:
    if not _should_bootstrap_tuya(user_input):
        return user_input, False
    cached = _get_cached_tuya_context(DEFAULT_TUYA_SPACE_ID)
    fresh = False
    if cached is None:
        cached = _refresh_tuya_context(DEFAULT_TUYA_SPACE_ID)
        fresh = cached is not None
    if not cached:
        return user_input, False
    context_text = _format_tuya_context(cached)
    augmented = f"{context_text}\n\nUsuario: {user_input}"
    return augmented, fresh


def _resolve_device_identifier(identifier: Any) -> Any:
    if not isinstance(identifier, str):
        return identifier
    trimmed = identifier.strip()
    if not trimmed:
        return identifier
    lookup = _TUYA_CONTEXT_CACHE.get("device_lookup") or {}
    normalized = _normalize_label(trimmed)
    resolved = lookup.get(normalized)
    if resolved:
        return resolved
    # Allow direct IDs pass-through
    if trimmed in lookup.values():
        return trimmed
    return identifier


def _resolve_scene_identifier(identifier: Any) -> Any:
    if not isinstance(identifier, str):
        return identifier
    trimmed = identifier.strip()
    if not trimmed:
        return identifier
    lookup = _TUYA_CONTEXT_CACHE.get("scene_lookup") or {}
    normalized = _normalize_label(trimmed)
    resolved = lookup.get(normalized)
    if resolved:
        return resolved
    if trimmed in lookup.values():
        return trimmed
    return identifier

_AUTOMATION_KEYWORDS: Sequence[str] = (
    "automacao",
    "automação",
    "automation",
    "cena",
    "scene",
    "smart plug",
    "smartplug",
    "tomada",
    "tomadas",
    "tuya",
    "dispositivo",
    "dispositivos",
    "iluminacao",
    "iluminação",
    "luz",
    "pluga",
    "ligar",
    "desligar",
    "home automation",
)

_TUYA_CACHE_TTL_SECONDS = 180.0
_TUYA_CONTEXT_CACHE: Dict[str, Any] = {
    "space_id": None,
    "payload": None,
    "timestamp": 0.0,
    "device_lookup": {},
    "scene_lookup": {},
}

def _auto_date_range(args: dict) -> dict:
    tz = ZoneInfo("America/Sao_Paulo")
    today_dt = datetime.now(tz).date()

    sd_raw = (args.get("start_date") or "").strip() if args.get("start_date") else ""
    ed_raw = (args.get("end_date")   or "").strip() if args.get("end_date")   else ""

    # Helpers
    import re as _re
    from datetime import datetime as _dt, timedelta as _td

    def _try_parse_iso(s: str):
        try:
            return _dt.strptime(s, "%Y-%m-%d").date()
        except Exception:
            return None

    def _try_parse_br(s: str):
        for fmt in ("%d/%m/%Y", "%d/%m/%y"):
            try:
                return _dt.strptime(s, fmt).date()
            except Exception:
                continue
        return None

    def _parse_relative(s: str):
        if not s:
            return None
        s_l = s.lower().strip()
        if s_l in {"today", "hoje"}:
            return today_dt
        if s_l in {"ontem", "yesterday"}:
            return today_dt - _td(days=1)
        # hoje-<n>
        m = _re.match(r"^(hoje|today)-(\d+)$", s_l)
        if m:
            return today_dt - _td(days=int(m.group(2)))
        # absolute-<n> pattern like 2025-09-12-30
        m2 = _re.match(r"^(\d{4}-\d{2}-\d{2})-(\d+)$", s_l)
        if m2:
            base = _try_parse_iso(m2.group(1))
            if base:
                return base - _td(days=int(m2.group(2)))
        # month/year ranges handled outside
        return None

    def _month_bounds(d):
        first = d.replace(day=1)
        next_month = (first.replace(day=28) + _td(days=4)).replace(day=1)
        last = next_month - _td(days=1)
        return first, last

    # Defaults: if both missing → year-to-date
    sd_dt = ed_dt = None

    # Quick range phrases on start_date
    sd_lower = (sd_raw or "").lower()
    if not sd_raw and not ed_raw:
        sd_dt = today_dt.replace(month=1, day=1)
        ed_dt = today_dt
    elif sd_lower in {"este ano", "ano atual", "this year"}:
        sd_dt = today_dt.replace(month=1, day=1)
        ed_dt = today_dt if not ed_raw else None
    elif sd_lower in {"este mes", "este mês", "mes atual", "mês atual", "this month"}:
        first, _ = _month_bounds(today_dt)
        sd_dt = first
        ed_dt = today_dt if not ed_raw else None
    elif sd_lower in {"mes passado", "mês passado", "last month"}:
        first_this, _ = _month_bounds(today_dt)
        last_prev = first_this - _td(days=1)
        first_prev, last_prev_b = _month_bounds(last_prev)
        sd_dt = first_prev
        ed_dt = last_prev_b
    else:
        # Try parse direct/relative
        sd_dt = _try_parse_iso(sd_raw) or _try_parse_br(sd_raw) or _parse_relative(sd_raw)

    # Parse end side if needed
    if ed_dt is None:
        ed_lower = (ed_raw or "").lower()
        if not ed_raw:
            # If only sd provided, default ed = sd for single-day unless sd was a range phrase above
            ed_dt = sd_dt or today_dt
        elif ed_lower in {"today", "hoje"}:
            ed_dt = today_dt
        elif ed_lower in {"ontem", "yesterday"}:
            ed_dt = today_dt - _td(days=1)
        else:
            ed_dt = _try_parse_iso(ed_raw) or _try_parse_br(ed_raw) or _parse_relative(ed_raw)

    # If "últimos N dias" form appears anywhere in sd_raw, convert to [today-(N-1), today]
    m_last = _re.search(r"\b(?:ultimos|últimos|last)\s+(\d+)\s+dias\b", sd_lower)
    if m_last:
        n = int(m_last.group(1))
        ed_dt = today_dt
        sd_dt = today_dt - _td(days=max(0, n - 1))

    # Ensure both exist
    sd_dt = sd_dt or today_dt
    ed_dt = ed_dt or sd_dt

    # Order
    if sd_dt > ed_dt:
        sd_dt, ed_dt = ed_dt, sd_dt

    args["start_date"] = sd_dt.isoformat()
    args["end_date"]   = ed_dt.isoformat()
    return args

def _get_default_powerstation_id(api: goodweApi.GoodweApi) -> str:
    if DEFAULT_STATION_ID:
        return DEFAULT_STATION_ID
    try:
        plants = api.ListPlants() or {}
        plant_list = plants.get("plants", []) if isinstance(plants, dict) else []
        # Prefer by name match
        for p in plant_list:
            if (p.get("stationname") or "").strip().lower() == DEFAULT_STATION_NAME.strip().lower():
                return p.get("powerstation_id") or ""
        # Fallback to first if Bauer not found
        return (plant_list[0].get("powerstation_id") if plant_list else "") or ""
    except Exception:
        return ""

def get_system_prompt():
    """Load system prompt from file"""
    try:
        with open("system_prompt.txt", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "You are BotSolar, an assistant for solar generation and battery management."

def create_function_declarations():
    """Create all function declarations for both solar and battery tools"""
    functions = []

    # Removed CSV-based solar functions

    # Removed placeholder battery controls in favor of GoodWe-backed functions

    # Remove destination from battery flow
    list_plants = types.FunctionDeclaration(
        name="list_plants",
        description="Will list the plants available for the user",
    )
    functions.append(list_plants)

    get_powerstation_battery_status = types.FunctionDeclaration(
        name="get_powerstation_battery_status",
        description=(
            "Retorna o status da bateria. Se 'powerstation_id' não for informado, use a planta padrão (ex.: Bauer). "
            "Não peça para o usuário escolher a planta a menos que ele solicite explicitamente trocar de planta. "
            "No retorno: status 2=descarregando, 1=carregando, 0=desligada/desconectada."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "powerstation_id": types.Schema(
                    type=types.Type.STRING,
                    description="Opcional. ID da estação. Se ausente, usar planta padrão."
                )
            }
        )
    )
    functions.append(get_powerstation_battery_status)
    get_alarms = types.FunctionDeclaration(
        name="get_alarms_by_range",
        description=("Return alarms for a date/range with open plant scope. "
                     "Optionally filter by station name in the presentation."),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "start_date": types.Schema(type=types.Type.STRING, description="YYYY-MM-DD , also can be 'today' or 'hoje' or 'yesterday' or 'ontem'"),
                "end_date": types.Schema(type=types.Type.STRING, description="YYYY-MM-DD (optional), also can be 'today' or 'hoje' or 'yesterday' or 'ontem'"),
                "status": types.Schema(type=types.Type.STRING, description='"0"=Happening, "1"=History'),
                "stationname": types.Schema(type=types.Type.STRING, description="Optional exact station name filter (case-insensitive)")
            },
            required=["start_date"]
        )
    )
    functions.append(get_alarms)

    get_warning_detail = types.FunctionDeclaration(
        name="get_warning_detail",
        description="Get human-readable detail for a specific warning (stationid, warningid, devicesn).",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "stationid": types.Schema(type=types.Type.STRING),
                "warningid": types.Schema(type=types.Type.STRING),
                "devicesn": types.Schema(type=types.Type.STRING),
            },
            required=["stationid","warningid","devicesn"]
        )
    )
    functions.append(get_warning_detail)

    get_powerstation_power_and_income_by_day = types.FunctionDeclaration(
        name="get_powerstation_power_and_income_by_day",
        description="Get the energy produced and income in a day. In the return the d is for the date p for the power generated and i for the income that is in dolar.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "powerstation_id": types.Schema(
                    type=types.Type.STRING,
                    description="O ID da estação de energia para consultar a gereanção de energia e renda. Necessário pegar o ID da planta primeiro com a função list_plants caso fornecido o nome da planta."
                ),
                "date": types.Schema(type=types.Type.STRING, description="YYYY-MM-DD , also can be 'today' or 'hoje' or 'yesterday' or 'ontem'"),
                "count": types.Schema(type=types.Type.INTEGER, description=" number of days to retrieve (1=current by date, 2=current+previous, etc.)"),
            },
            required=["date"]
        )
    )
    functions.append(get_powerstation_power_and_income_by_day)

    get_powerstation_power_and_income_by_month = types.FunctionDeclaration(
        name="get_powerstation_power_and_income_by_month",
        description="Get the energy produced and income in a month. The income is in dolar.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "powerstation_id": types.Schema(
                    type=types.Type.STRING,
                    description="O ID da estação de energia para consultar a gereanção de energia e renda. Necessário pegar o ID da planta primeiro com a função list_plants caso fornecido o nome da planta."
                ),
                "date": types.Schema(type=types.Type.STRING, description="YYYY-MM-DD, if its something like 'this month' pass 'today'"),
                "count": types.Schema(type=types.Type.INTEGER, description=" number of months to retrieve (1=current by date, 2=current+previous, etc.)"),
            },
            required=["date"]
        )
    )
    functions.append(get_powerstation_power_and_income_by_month)

    get_powerstation_power_and_income_by_year = types.FunctionDeclaration(
        name="get_powerstation_power_and_income_by_year",
        description="Get the energy produced and income in a year. The income is in dolar.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "powerstation_id": types.Schema(
                    type=types.Type.STRING,
                    description="O ID da estação de energia para consultar a gereanção de energia e renda. Necessário pegar o ID da planta primeiro com a função list_plants caso fornecido o nome da planta."
                ),
                "date": types.Schema(type=types.Type.STRING, description="YYYY-MM-DD, if its something like 'this year' pass 'today'"),
                "count": types.Schema(type=types.Type.INTEGER, description=" number of years to retrieve (1=current by date, 2=current+previous, etc.)"),
            },
            required=["date"]
        )
    )
    functions.append(get_powerstation_power_and_income_by_year)

    get_ev_charger_status = types.FunctionDeclaration(
        name="get_ev_charger_status",
        description="Retorna o status do carregador de veículo elétrico (EV Charger) para um powerstation_id. Charge Mode 1 = Fast, 2 = PV Priority, 3 = PV & Battery",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "powerstation_id": types.Schema(
                    type=types.Type.STRING,
                    description="O ID da estação de energia associada ao EV Charger. Se ausente, usar planta padrão."
                )
            }
        )
    )
    functions.append(get_ev_charger_status)

    change_ev_charger_status = types.FunctionDeclaration(
        name="change_ev_charger_status",
        description="Altera o modo de carregamento do EV Charger para um powerstation_id.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "powerstation_id": types.Schema(
                    type=types.Type.STRING,
                    description="O ID da estação de energia associada ao EV Charger. Se ausente, usar planta padrão."
                ),
                "charge_mode": types.Schema(
                    type=types.Type.INTEGER,
                    description="Charge_mode 1 - Fast (Rapido), 2 - PV Priority, 3 - PV & Battery."
                )
            },
            required=["charge_mode"]
        )
    )
    functions.append(change_ev_charger_status)


    # Usage optimization (statistical summary for recommendations)
    optimize_usage = types.FunctionDeclaration(
        name="optimize_usage",
        description=(
            "Gera um relatório estatístico curto a partir do histórico de 7 dias (minuto a minuto). "
            "Use quando o usuário pedir para otimizar o uso, e.g., 'otimize meu uso', 'otimizar consumo', "
            "'optimize my usage'. O retorno inclui janelas típicas de maior geração, horas de menor SOC, "
            "e médias agregadas."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "parsed_path": types.Schema(
                    type=types.Type.STRING,
                    description="Opcional. Caminho para um arquivo history7d_parsed_*.json. Se ausente, usa o mais recente."
                )
            }
        )
    )
    functions.append(optimize_usage)

    tuya_describe_space = types.FunctionDeclaration(
        name="tuya_describe_space",
        description=(
            "Listar dispositivos e cenas Tuya para um space_id (sem expor segredos)."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "space_id": types.Schema(type=types.Type.STRING, description="Opcional: ID do espaço Tuya (usa padrão configurado se ausente)."),
                "config_path": types.Schema(type=types.Type.STRING, description="Opcional: caminho para configs/automation.yaml customizado."),
            },
        ),
    )
    functions.append(tuya_describe_space)

    tuya_inspect_device = types.FunctionDeclaration(
        name="tuya_inspect_device",
        description="Obter propriedades (datapoints) de um dispositivo Tuya para explicar códigos ao usuário.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "device_id": types.Schema(type=types.Type.STRING, description="ID do dispositivo Tuya."),
                "codes": types.Schema(
                    type=types.Type.ARRAY,
                    description="Opcional: lista de códigos DP para filtrar.",
                    items=types.Schema(type=types.Type.STRING),
                ),
            },
            required=["device_id"],
        ),
    )
    functions.append(tuya_inspect_device)

    tuya_propose_automation = types.FunctionDeclaration(
        name="tuya_propose_automation",
        description=(
            "Gerar payloads de automação Tuya usando heurísticas (pré-visualização, não cria regras)."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "space_id": types.Schema(type=types.Type.STRING, description="Opcional: ID do espaço Tuya (usa padrão configurado se ausente)."),
                "heuristic_set": types.Schema(
                    type=types.Type.ARRAY,
                    description="Opcional: subconjunto das heurísticas (battery_protect, solar_surplus, night_guard).",
                    items=types.Schema(type=types.Type.STRING),
                ),
                "config_path": types.Schema(type=types.Type.STRING, description="Opcional: caminho alternativo para automation.yaml."),
                "heuristic_overrides": types.Schema(
                    type=types.Type.OBJECT,
                    description="Mapeamento opcional heurística→parâmetros (ex.: inverter_device_id, load_device_id, threshold).",
                ),
            },
        ),
    )
    functions.append(tuya_propose_automation)

    tuya_create_and_enable_automation = types.FunctionDeclaration(
        name="tuya_create_and_enable_automation",
        description=(
            "Criar (e opcionalmente habilitar) uma cena Tuya. Requer confirmação explícita."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "payload": types.Schema(type=types.Type.OBJECT, description="Payload completo da cena conforme templates Tuya."),
                "confirm": types.Schema(type=types.Type.BOOLEAN, description="Deve ser true após o usuário autorizar a criação."),
                "enable": types.Schema(type=types.Type.BOOLEAN, description="Se true, habilita a cena após criar."),
            },
            required=["payload", "confirm"],
        ),
    )
    functions.append(tuya_create_and_enable_automation)

    tuya_update_automation = types.FunctionDeclaration(
        name="tuya_update_automation",
        description="Atualizar uma cena Tuya existente com novo payload (confirmação obrigatória).",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "rule_id": types.Schema(type=types.Type.STRING, description="ID da cena/regra Tuya."),
                "payload": types.Schema(type=types.Type.OBJECT, description="Payload atualizado conforme schema Tuya."),
                "confirm": types.Schema(type=types.Type.BOOLEAN, description="Deve ser true após o usuário aprovar a alteração."),
            },
            required=["rule_id", "payload", "confirm"],
        ),
    )
    functions.append(tuya_update_automation)

    tuya_delete_automations = types.FunctionDeclaration(
        name="tuya_delete_automations",
        description="Excluir uma ou mais cenas Tuya (usa space_id do args/config/ENV). Necessita confirmação.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "rule_ids": types.Schema(
                    type=types.Type.ARRAY,
                    description="Lista de IDs das cenas a remover.",
                    items=types.Schema(type=types.Type.STRING),
                ),
                "space_id": types.Schema(type=types.Type.STRING, description="Opcional: space_id para reforçar o escopo."),
                "config_path": types.Schema(type=types.Type.STRING, description="Opcional: caminho alternativo para automation.yaml."),
                "confirm": types.Schema(type=types.Type.BOOLEAN, description="Deve ser true após o usuário solicitar exclusão."),
            },
            required=["rule_ids", "confirm"],
        ),
    )
    functions.append(tuya_delete_automations)

    tuya_set_automation_state = types.FunctionDeclaration(
        name="tuya_set_automation_state",
        description="Habilitar ou desabilitar uma lista de cenas Tuya (confirmação obrigatória).",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "rule_ids": types.Schema(
                    type=types.Type.ARRAY,
                    description="Lista de IDs das cenas para alterar estado.",
                    items=types.Schema(type=types.Type.STRING),
                ),
                "enable": types.Schema(type=types.Type.BOOLEAN, description="True para habilitar, False para desabilitar."),
                "confirm": types.Schema(type=types.Type.BOOLEAN, description="Deve ser true após confirmação do usuário."),
            },
            required=["rule_ids", "enable", "confirm"],
        ),
    )
    functions.append(tuya_set_automation_state)

    tuya_trigger_scene = types.FunctionDeclaration(
        name="tuya_trigger_scene",
        description="Acionar manualmente uma cena Tuya (confirmação obrigatória).",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "rule_id": types.Schema(type=types.Type.STRING, description="ID da cena a ser disparada."),
                "confirm": types.Schema(type=types.Type.BOOLEAN, description="Deve ser true após o usuário solicitar o disparo."),
            },
            required=["rule_id", "confirm"],
        ),
    )
    functions.append(tuya_trigger_scene)
    return functions

def initialize_chat():
    """Initialize the chat with system prompt and tools"""
    global chat_instance

    try:
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        system_prompt = get_system_prompt()
        function_declarations = create_function_declarations()

        # Create the chat with tools and system instruction
        chat_instance = client.chats.create(
            model="gemini-2.5-flash",
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                tools=[types.Tool(function_declarations=function_declarations)]
            )
        )
        return True
    except Exception as e:
        print(f"❌ Error initializing chat: {e}")
        return False

goodwe_api_instance = goodweApi.GoodweApi()

def get_alarms_flat(**kwargs):
    args = _auto_date_range(dict(kwargs))
    # Do not force stationid; use searchKey when stationname is provided
    stationname = args.get("stationname") or DEFAULT_STATION_NAME
    args["stationname"] = stationname
    args["searchKey"] = stationname
    # Infer status if not provided
    if not args.get("status"):
        tz = ZoneInfo("America/Sao_Paulo")
        today = datetime.now(tz).date()
        try:
            sd = datetime.fromisoformat(args.get("start_date")).date()
            ed = datetime.fromisoformat(args.get("end_date") or args.get("start_date")).date()
        except Exception:
            sd = ed = today
        # New heuristic: default to ALL ("3"), except when only today
        if sd == today and ed == today:
            args["status"] = "0"  # happening today
        else:
            args["status"] = "3"  # all statuses for ranges/past
    return goodwe_api_instance.GetAlarmsByRange(**args)

def _json_safe(value: Any) -> Any:
    """Ensure values are JSON-serialisable for previews."""
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {k: _json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_json_safe(v) for v in value]
        return str(value)


def execute_function_call(function_call, *, powerstation_override: Optional[str] = None):
    """Execute the appropriate function based on the function call"""
    function_map = {
        "list_plants": goodwe_api_instance.ListPlants,
        "get_powerstation_battery_status": goodwe_api_instance.GetSoc,
        "get_alarms_by_range": get_alarms_flat,
        "get_warning_detail": goodwe_api_instance.GetWarningDetailTranslated,
        "get_powerstation_power_and_income_by_day": goodwe_api_instance.GetPowerAndIncomeByDay,
        "get_powerstation_power_and_income_by_month": goodwe_api_instance.GetPowerAndIncomeByMonth,
        "get_powerstation_power_and_income_by_year": goodwe_api_instance.GetPowerAndIncomeByYear,
        "optimize_usage": usage_optimizer.optimize_usage,
        "get_ev_charger_status": goodwe_api_instance.GetEvChargerChargingMode,
        "change_ev_charger_status": goodwe_api_instance.ChangeEvChargerChargingMode,
        "tuya_describe_space": describe_space,
        "tuya_inspect_device": inspect_device,
        "tuya_propose_automation": propose_automation,
        "tuya_create_and_enable_automation": create_and_enable_automation,
        "tuya_update_automation": update_automation,
        "tuya_delete_automations": delete_automations,
        "tuya_set_automation_state": set_automation_state,
        "tuya_trigger_scene": trigger_scene,
    }

    needs_powerstation = {
        "get_powerstation_battery_status",
        "get_powerstation_power_and_income_by_day",
        "get_powerstation_power_and_income_by_month",
        "get_powerstation_power_and_income_by_year",
        "get_ev_charger_status",
        "change_ev_charger_status",
    }

    function_name = function_call.name
    print(function_call.args)
    function_args = dict(function_call.args) if function_call.args else {}
    fallback_to_default = False
    used_powerstation_id = function_args.get("powerstation_id")

    if function_name == "tuya_describe_space":
        function_args["space_id"] = function_args.get("space_id") or DEFAULT_TUYA_SPACE_ID
        # Serve from cache when possible to avoid repeated API calls
        cached_payload = _get_cached_tuya_context(function_args.get("space_id"))
        if cached_payload and not function_args.get("config_path"):
            meta = {
                "fallback_to_default": fallback_to_default,
                "used_powerstation_id": used_powerstation_id,
                "args_preview": _json_safe(function_args),
                "result_preview": _json_safe(cached_payload),
                "from_cache": True,
            }
            return cached_payload, meta["args_preview"], meta["result_preview"], meta

    if function_name == "tuya_propose_automation":
        function_args["space_id"] = function_args.get("space_id") or DEFAULT_TUYA_SPACE_ID
        overrides = function_args.get("heuristic_overrides") or {}
        if isinstance(overrides, dict):
            resolved_overrides: Dict[str, Any] = {}
            for key, params in overrides.items():
                if isinstance(params, dict):
                    resolved_params = dict(params)
                    for override_key in ("inverter_device_id", "load_device_id", "sensor_device_id"):
                        resolved_params[override_key] = _resolve_device_identifier(resolved_params.get(override_key))
                    resolved_overrides[key] = resolved_params
                else:
                    resolved_overrides[key] = params
            function_args["heuristic_overrides"] = resolved_overrides

    if function_name in {"tuya_delete_automations", "tuya_set_automation_state"}:
        rule_ids = function_args.get("rule_ids")
        if isinstance(rule_ids, Sequence) and not isinstance(rule_ids, (str, bytes)):
            resolved_rule_ids = []
            for item in rule_ids:
                resolved = _resolve_scene_identifier(item)
                if resolved:
                    resolved_rule_ids.append(resolved)
            function_args["rule_ids"] = resolved_rule_ids

    if function_name in {"tuya_update_automation", "tuya_trigger_scene"}:
        if "rule_id" in function_args:
            function_args["rule_id"] = _resolve_scene_identifier(function_args.get("rule_id"))
    if function_name in {
        "tuya_describe_space",
        "tuya_propose_automation",
    }:
        if not function_args.get("space_id") and DEFAULT_TUYA_SPACE_ID:
            function_args["space_id"] = DEFAULT_TUYA_SPACE_ID

    if function_name in function_map:
        try:
            # Apply helpers/defaults
            if function_name == "get_alarms_by_range":
                function_args = _auto_date_range(function_args)

            if function_name in needs_powerstation:
                if function_args.get("powerstation_id"):
                    used_powerstation_id = function_args["powerstation_id"]
                elif powerstation_override:
                    function_args["powerstation_id"] = powerstation_override
                    used_powerstation_id = powerstation_override
                else:
                    default_station = _get_default_powerstation_id(goodwe_api_instance)
                    if default_station:
                        function_args["powerstation_id"] = default_station
                        used_powerstation_id = default_station

            result = function_map[function_name](**function_args)

            if function_name == "tuya_describe_space":
                target_space = function_args.get("space_id") or DEFAULT_TUYA_SPACE_ID
                if isinstance(result, dict):
                    _update_tuya_cache(target_space, result)
            elif function_name in {
                "tuya_create_and_enable_automation",
                "tuya_update_automation",
                "tuya_delete_automations",
                "tuya_set_automation_state",
            }:
                _invalidate_tuya_cache()

            preview_args = _json_safe(function_args)
            preview_result = _json_safe(result)

            print(f"🔧 Function '{function_name}' called with args: {function_args}")
            print(f"📊 Result: {result}")

            meta = {
                "fallback_to_default": fallback_to_default,
                "used_powerstation_id": used_powerstation_id,
                "args_preview": preview_args,
                "result_preview": preview_result,
            }
            return result, preview_args, preview_result, meta
        except Exception as e:
            print(f"❌ Error executing function '{function_name}': {e}")
            error_payload = {"error": str(e)}
            meta = {
                "fallback_to_default": fallback_to_default,
                "used_powerstation_id": used_powerstation_id,
                "args_preview": _json_safe(function_args),
                "result_preview": _json_safe(error_payload),
            }
            return error_payload, meta["args_preview"], meta["result_preview"], meta
    else:
        print(f"❌ Unknown function: {function_name}")
        error_payload = {"error": f"Unknown function: {function_name}"}
        meta = {
            "fallback_to_default": fallback_to_default,
            "used_powerstation_id": used_powerstation_id,
            "args_preview": _json_safe(function_args),
            "result_preview": _json_safe(error_payload),
        }
        return error_payload, meta["args_preview"], meta["result_preview"], meta

async def call_geminiapi(user_input: str, *, powerstation_id: Optional[str] = None) -> Dict[str, Any]:
    """Main API function for processing user input"""
    global chat_instance

    if chat_instance is None:
        if not initialize_chat():
            return {
                "response": "❌ Error: Could not initialize the chat system.",
                "functions_preview": [],
                "fallback_to_default": False,
                "used_powerstation_id": powerstation_id,
            }

    try:
        augmented_input, _ = _augment_user_input_with_tuya_context(user_input)
        response = chat_instance.send_message(message=augmented_input)
        function_executed = False
        executed_functions = []
        final_answer_chunks = []
        used_powerstation_id = powerstation_id
        fallback_to_default = False

        while True:
            function_response_parts = []
            has_function_call = False

            if hasattr(response, "candidates") and response.candidates:
                candidate = response.candidates[0]
                content = getattr(candidate, "content", None)
                parts = getattr(content, "parts", []) if content else []

                for part in parts:
                    if hasattr(part, "function_call") and part.function_call:
                        (
                            result,
                            preview_args,
                            preview_result,
                            meta,
                        ) = execute_function_call(part.function_call, powerstation_override=powerstation_id)
                        function_response_part = types.Part.from_function_response(
                            name=part.function_call.name,
                            response=result,
                        )
                        function_response_parts.append(function_response_part)
                        executed_functions.append(
                            {
                                "name": part.function_call.name,
                                "args": preview_args,
                                "result": preview_result,
                            }
                        )
                        if meta.get("used_powerstation_id"):
                            used_powerstation_id = meta.get("used_powerstation_id")
                        if meta.get("fallback_to_default"):
                            fallback_to_default = True
                        function_executed = True
                        has_function_call = True
                    elif hasattr(part, "text") and part.text:
                        final_answer_chunks.append(part.text)

            if has_function_call and function_response_parts:
                response = chat_instance.send_message(message=function_response_parts)
            else:
                break

        response_text = getattr(response, "text", "") or ""
        final_answer = "\n".join(chunk.strip() for chunk in final_answer_chunks if chunk).strip()
        if not final_answer:
            final_answer = response_text.strip()
        elif response_text.strip() and response_text.strip() not in {
            chunk.strip() for chunk in final_answer_chunks if chunk
        }:
            final_answer = "\n".join(filter(None, [final_answer, response_text.strip()]))

        if not final_answer:
            final_answer = "Funções executadas com sucesso." if function_executed else "Processamento concluído, mas sem resposta textual."

        return {
            "response": final_answer,
            "functions_preview": executed_functions,
            "fallback_to_default": fallback_to_default,
            "used_powerstation_id": used_powerstation_id,
        }
    except Exception as e:
        print(f"❌ Error in call_geminiapi: {e}")
        return {
            "response": f"❌ Error processing your request: {str(e)}",
            "functions_preview": [],
            "fallback_to_default": False,
            "used_powerstation_id": powerstation_id,
        }
