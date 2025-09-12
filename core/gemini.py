from google import genai
from google.genai import types
import os
import core.goodweApi as goodweApi
from core import usage_optimizer
from datetime import datetime
from zoneinfo import ZoneInfo

# Global chat instance for maintaining conversation context
chat_instance = None
DEFAULT_STATION_NAME = "Bauer"
DEFAULT_STATION_ID = "6ef62eb2-7959-4c49-ad0a-0ce75565023a"

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

def get_today_date():
    """Returns the current date in ISO-8601 format."""
    tz = ZoneInfo("America/Sao_Paulo")
    return datetime.now(tz).date().isoformat()

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
                "start_date": types.Schema(type=types.Type.STRING, description="YYYY-MM-DD"),
                "end_date": types.Schema(type=types.Type.STRING, description="YYYY-MM-DD (optional)"),
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
        description="Get the energy produced and income in a day. In the return the d is for the date p for the power generated and i for the income.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "powerstation_id": types.Schema(
                    type=types.Type.STRING,
                    description="O ID da estação de energia para consultar a gereanção de energia e renda. Necessário pegar o ID da planta primeiro com a função list_plants"
                ),
                "date": types.Schema(type=types.Type.STRING, description="YYYY-MM-DD"),
                "count": types.Schema(type=types.Type.INTEGER, description=" number of days to retrieve (1=current by date, 2=current+previous, etc.)"),
            },
            required=["powerstation_id","date"]
        )
    )
    functions.append(get_powerstation_power_and_income_by_day)

    get_powerstation_power_and_income_by_month = types.FunctionDeclaration(
        name="get_powerstation_power_and_income_by_month",
        description="Get the energy produced and income in a month.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "powerstation_id": types.Schema(
                    type=types.Type.STRING,
                    description="O ID da estação de energia para consultar a gereanção de energia e renda. Necessário pegar o ID da planta primeiro com a função list_plants."
                ),
                "date": types.Schema(type=types.Type.STRING, description="YYYY-MM-DD"),
                "count": types.Schema(type=types.Type.INTEGER, description=" number of months to retrieve (1=current by date, 2=current+previous, etc.)"),
            },
            required=["powerstation_id","date"]
        )
    )
    functions.append(get_powerstation_power_and_income_by_month)

    get_powerstation_power_and_income_by_year = types.FunctionDeclaration(
        name="get_powerstation_power_and_income_by_year",
        description="Get the energy produced and income in a year.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "powerstation_id": types.Schema(
                    type=types.Type.STRING,
                    description="O ID da estação de energia para consultar a gereanção de energia e renda. Necessário pegar o ID da planta primeiro com a função list_plants"
                ),
                "date": types.Schema(type=types.Type.STRING, description="YYYY-MM-DD"),
                "count": types.Schema(type=types.Type.INTEGER, description=" number of years to retrieve (1=current by date, 2=current+previous, etc.)"),
            },
            required=["powerstation_id","date"]
        )
    )
    functions.append(get_powerstation_power_and_income_by_year)

    get_today_date_func = types.FunctionDeclaration(
        name="get_today_date",
        description="Returns the current date in ISO-8601 format (YYYY-MM-DD)."
    )
    functions.append(get_today_date_func)

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
            model="gemini-2.5-flash-preview-05-20",
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

def execute_function_call(function_call):
    """Execute the appropriate function based on the function call"""
    function_map = {
        "list_plants": goodwe_api_instance.ListPlants,
        "get_powerstation_battery_status": goodwe_api_instance.GetSoc,
        "get_alarms_by_range": get_alarms_flat,
        "get_warning_detail": goodwe_api_instance.GetWarningDetailTranslated,
        "get_today_date": get_today_date,
        "get_powerstation_power_and_income_by_day": goodwe_api_instance.GetPowerAndIncomeByDay,
        "get_powerstation_power_and_income_by_month": goodwe_api_instance.GetPowerAndIncomeByMonth,
        "get_powerstation_power_and_income_by_year": goodwe_api_instance.GetPowerAndIncomeByYear,
        "optimize_usage": usage_optimizer.optimize_usage,
    }
    
    function_name = function_call.name
    print(function_call.args)
    function_args = dict(function_call.args) if function_call.args else {}
    
    if function_name in function_map:
        try:
            # Apply helpers/defaults
            if function_name == "get_alarms_by_range":
                function_args = _auto_date_range(function_args)
            if function_name == "get_powerstation_battery_status" and not function_args.get("powerstation_id"):
                function_args["powerstation_id"] = _get_default_powerstation_id(goodwe_api_instance)
            result = function_map[function_name](**function_args)

            # Ensure get_today_date result is a dictionary
            if function_name == "get_today_date" and not isinstance(result, dict):
                result = {"today_date": result}

            print(f"🔧 Function '{function_name}' called with args: {function_args}")
            print(f"📊 Result: {result}")
            return result
        except Exception as e:
            print(f"❌ Error executing function '{function_name}': {e}")
            return {"error": str(e)}
    else:
        print(f"❌ Unknown function: {function_name}")
        return {"error": f"Unknown function: {function_name}"}

async def call_geminiapi(user_input: str):
    """Main API function for processing user input"""
    global chat_instance

    if chat_instance is None:
        if not initialize_chat():
            return "❌ Error: Could not initialize the chat system."

    try:
        response = chat_instance.send_message(message=user_input)
        function_executed = False

        while True:
            function_response_parts = []
            has_function_call = False

            if hasattr(response, 'candidates') and response.candidates:
                candidate = response.candidates[0] if getattr(response, "candidates", None) else None
                content = getattr(candidate, "content", None)
                parts = getattr(content, "parts", []) if content else []

                for part in parts:
                    if hasattr(part, "function_call") and part.function_call:
                        result = execute_function_call(part.function_call)
                        function_response_part = types.Part.from_function_response(
                            name=part.function_call.name,
                            response=result
                        )
                        function_response_parts.append(function_response_part)
                        function_executed = True
                        has_function_call = True
                    elif hasattr(part, "text") and part.text:
                        print(part.text)
                        return part.text


            if has_function_call and function_response_parts:
                response = chat_instance.send_message(message=function_response_parts)
            else:
                break

        if function_executed:
            return response.text if response.text else "Funções executadas com sucesso."
        return response.text if response.text else "Processamento concluído, mas sem resposta textual."
    except Exception as e:
        print(f"❌ Error in call_geminiapi: {e}")
        return f"❌ Error processing your request: {str(e)}"