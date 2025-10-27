from google import genai
from google.genai import types
import json
import os
from typing import Any, Dict, Optional

from dotenv import load_dotenv

import report.devices as devices
import core.goodweApi as goodweApi
from datetime import datetime
from zoneinfo import ZoneInfo

load_dotenv(".env")

# Global chat instance for maintaining conversation context
chat_instance = None
DEFAULT_STATION_NAME = "teste"
DEFAULT_STATION_ID = "7f9af1fc-3a9a-4779-a4c0-ca6ec87bd93a"
DEFAULT_TUYA_SPACE_ID = os.getenv("TUYA_SPACE_ID")

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

def get_system_prompt():
    """Load system prompt from file"""
    try:
        with open("./report/scenes_description.txt", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "You are BotSolar, an assistant for solar generation and battery management."

def create_function_declarations():
    """Create all function declarations for both solar and battery tools"""
    functions = []

    get_hour_devices_on = types.FunctionDeclaration(
        name="get_hour_devices_on",
        description=(
            "Retorna um mapeamento {timestamp: [dispositivo, ...]} com dispositivos 'on' "
            "entre `start_date` e `end_date`. Aceita timestamps em ISO 8601 (ex.: 2025-01-01T00:00:00Z). "
            "Suporta formatos antigos (snapshot com array `devices`) e novo (documento por dispositivo)."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "start_date": types.Schema(
                    type=types.Type.STRING,
                    description="Obrigatório. Data/hora inicial em ISO 8601 (ex.: 2025-01-01T00:00:00Z)."
                ),
                "end_date": types.Schema(
                    type=types.Type.STRING,
                    description="Obrigatório. Data/hora final em ISO 8601 (ex.: 2025-01-01T23:59:59Z)."
                )
            }
        )
    )
    functions.append(get_hour_devices_on)

    get_device_data = types.FunctionDeclaration(
        name="get_device_data",
        description=(
            "Retorna a lista de snapshots de um dispositivo entre `start_date` e `end_date`. "
            "Cada entrada inclui timestamp, id, name, customName, categoria, isOnline, properties e campo `on`."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "device_id": types.Schema(
                    type=types.Type.STRING,
                    description="ID do dispositivo (obrigatório)."
                ),
                "start_date": types.Schema(
                    type=types.Type.STRING,
                    description="Opcional. Data/hora inicial em ISO 8601. Se ausente, usar intervalo padrão do sistema."
                ),
                "end_date": types.Schema(
                    type=types.Type.STRING,
                    description="Opcional. Data/hora final em ISO 8601. Se ausente, usar intervalo padrão do sistema."
                )
            }
        )
    )
    functions.append(get_device_data)

    get_devices_last_sample = types.FunctionDeclaration(
        name="get_devices_last_sample",
        description=(
            "Retorna uma tupla contendo o timestamp do último snapshot até `date_ref` "
            "(ou `now` se ausente) e a lista de dispositivos presentes nesse snapshot. "
            "Suporta formatos antigos (documento com array `devices`) e novo (documento por dispositivo)."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "date_ref": types.Schema(
                    type=types.Type.STRING,
                    description="Opcional. Data/hora de referência em ISO 8601 (ex.: 2025-01-01T12:00:00Z). Se ausente, usa agora (UTC)."
                )
            }
        )
    )
    functions.append(get_devices_last_sample)

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
    """Execute the appropriate function based on the function call (com debug extra)"""
    function_map = {
        "get_device_data": devices.get_device_data,
        "get_hour_devices_on": devices.get_hour_devices_on,
        "get_devices_last_sample": devices.get_devices_last_sample,
    }

    # Debug: mostrar o objeto completo e seus argumentos brutos
    try:
        print("DEBUG execute_function_call - function_call repr:", repr(function_call))
    except Exception:
        print("DEBUG execute_function_call - could not repr(function_call)")

    function_name = getattr(function_call, "name", None)
    raw_args = getattr(function_call, "args", None)
    print(f"DEBUG execute_function_call - name: {function_name}, raw_args: {raw_args}")

    function_args = dict(raw_args) if raw_args else {}
    fallback_to_default = False
    used_powerstation_id = function_args.get("powerstation_id")

    if function_name in function_map:
        try:
            # Apply helpers/defaults
            if function_name == "get_alarms_by_range":
                function_args = _auto_date_range(function_args)

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
    """Main API function for processing user input (com logs extra para debug de function calls)"""
    global chat_instance

    if chat_instance is None:
        if not initialize_chat():
            return {
                "response": "❌ Error: Could not initialize the chat system."
            }

    try:
        response = chat_instance.send_message(message=user_input)
        function_executed = False
        executed_functions = []
        final_answer_chunks = []

        while True:
            function_response_parts = []
            has_function_call = False

            if hasattr(response, "candidates") and response.candidates:
                candidate = response.candidates[0]
                print("DEBUG candidate repr:", repr(candidate))
                content = getattr(candidate, "content", None)
                print("DEBUG content repr:", repr(content))

                parts = getattr(content, "parts", None) if content is not None else None
                # Proteção: garantir que parts seja iterável
                if parts is None:
                    print("DEBUG parts is None -> normalizing to empty list")
                    parts = []
                else:
                    print(f"DEBUG parts type: {type(parts)}, length (if applicable): {getattr(parts, '__len__', lambda: 'N/A')()}")

                for part in parts:
                    try:
                        print("DEBUG part repr:", repr(part))
                    except Exception:
                        print("DEBUG part (could not repr)")

                    if hasattr(part, "function_call") and part.function_call:
                        fc = part.function_call
                        print("DEBUG detected function_call - name:", getattr(fc, "name", None), "args:",
                              getattr(fc, "args", None))
                        (
                            result,
                            preview_args,
                            preview_result,
                            meta,
                        ) = execute_function_call(fc, powerstation_override=powerstation_id)

                        # Normaliza `result` para um dict (types.Part.from_function_response exige dict)
                        if isinstance(result, dict):
                            safe_result = _json_safe(result)
                        else:
                            # Common case: tuple (timestamp, devices)
                            if isinstance(result, tuple) and len(result) == 2:
                                ts, devices = result
                                try:
                                    ts_val = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
                                except Exception:
                                    ts_val = str(ts)
                                safe_result = {"timestamp": ts_val, "devices": _json_safe(devices)}
                            else:
                                safe_result = {"result": _json_safe(result)}

                        function_response_part = types.Part.from_function_response(
                            name=getattr(fc, "name", None),
                            response=safe_result,
                        )
                        function_response_parts.append(function_response_part)
                        executed_functions.append(
                            {
                                "name": getattr(fc, "name", None),
                                "args": preview_args,
                                "result": preview_result,
                            }
                        )
                        function_executed = True
                        has_function_call = True

                    elif hasattr(part, "text") and part.text:
                        final_answer_chunks.append(part.text)

            if has_function_call and function_response_parts:
                print("DEBUG sending function response parts back to chat:", function_response_parts)
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
            "response": final_answer
        }
    except Exception as e:
        print(f"❌ Error in call_geminiapi: {e}")
        return {
            "response": f"❌ Error processing your request: {str(e)}"
        }