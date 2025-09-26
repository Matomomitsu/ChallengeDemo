from google import genai
from google.genai import types
import json
import os
from typing import Any, Dict, Optional

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

TUYA_PROMPT_ADDITION = """
Tuya automation tooling is available via dedicated function calls.
- Heuristics available for proposal previews: battery_protect (shed loads when SOC low), solar_surplus (enable loads during PV surplus), night_guard (disable loads overnight unless PV power is present).
- Default to the configured Tuya space when the user does not supply one; avoid asking for IDs unless strictly necessary.
- Provide device IDs when calling tuya_propose_automation by using the heuristic_overrides argument (e.g., inverter/load IDs, thresholds) so payloads can be generated without editing config files.
- When a user requests ideas, call tuya_propose_automation first and show the resulting payloads; ask for explicit confirmation before any create/update/delete/trigger call.
- Destructive or state-changing helpers require the confirm flag set to true; never bypass user consent.
- Use tuya_inspect_device to surface datapoint codes and explain them in answers, but speak in plain language; prefer friendly labels (custom names) and avoid dumping raw JSON.
- Summarize scene IDs returned by create/update/delete operations so the user can reference them later.
""".strip()

FRIENDLY_PROMPT_ADDITION = """
Always address the user like a helpful home automation guide:
- Assume limited technical knowledge; translate device properties and datapoint codes into human terms (e.g., "Bateria" → "Nível da bateria").
- Highlight only the most relevant facts in responses (names, current values, status) and keep raw payloads hidden unless the user explicitly requests them.
- When referencing automations or devices, lead with friendly names and only surface IDs if the user asks or they are strictly necessary for clarity.
- Proactively mention confirmations or follow-up steps so the user feels guided through the process.
- Confirmations should reference the friendly name (e.g., "Quer ativar a automação Battery Protect?") and avoid repeating raw IDs.

Example flow:
Usuário: "Crie uma automação para desligar o plug quando a bateria cair."
Assistente: (1) Usa descrições simples para explicar a ideia; (2) chama as ferramentas necessárias; (3) pergunta: "Posso criar a automação 'Proteger bateria' para desligar o smart plug quando a bateria ficar abaixo de 50%?" sem expor JSON.

Model responses should follow these patterns:
1. **Listar dispositivos e cenas**
   "Encontrei dois dispositivos no seu espaço Tuya: o smart plug (online) e o inversor solar GoodWe (offline no momento). Há uma automação ativa chamada 'Battery Protect (Bateria < 50%)'. Quer que eu verifique os detalhes de algum deles ou crie algo novo?"

2. **Entender propriedades de um dispositivo**
   "O inversor solar mostra nível de bateria em 76%, consumo residencial em 414 W e geração solar atual em 0 W. Esses são os pontos mais relevantes para decidir automações. Quer configurar alertas com base em algum desses dados?"

3. **Propor e criar automações**
   "Posso configurar uma cena chamada 'Aproveitar excedente solar' que liga o smart plug quando a produção passar de 800 W. Se fizer sentido, confirmo com você antes de criar e já deixo habilitada. Deseja que eu avance com isso?"

4. **Ativar, desativar, apagar ou disparar automações**
   "A automação 'Battery Protect' está pronta. Ativei agora mesmo e posso dispará-la manualmente para testar se quiser. Também consigo desabilitar ou remover qualquer automação que não use mais. O que prefere fazer em seguida?"
""".strip()

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
        additions: list[str] = []
        if TUYA_PROMPT_ADDITION:
            additions.append(TUYA_PROMPT_ADDITION)
        if FRIENDLY_PROMPT_ADDITION:
            additions.append(FRIENDLY_PROMPT_ADDITION)
        if additions:
            system_prompt = "\n\n".join(filter(None, [system_prompt.rstrip(), *additions]))
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
        response = chat_instance.send_message(message=user_input)
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
