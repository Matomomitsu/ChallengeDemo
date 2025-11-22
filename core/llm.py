from __future__ import annotations

import os
import time
import threading
from pydantic import BaseModel
import asyncio
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Dict, Optional, List, Sequence

from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_core.tools import tool
from langchain.agents import create_agent
from langchain_core.prompts import ChatPromptTemplate

# Reuse existing Tuya logic
# Reuse existing Tuya logic
from core.tuya_context import TuyaContextManager, DEFAULT_TUYA_SPACE_ID, prewarm_tuya_caches
from core.tuya_scene_builder import prewarm_scene_builder
import core.goodweApi as goodweApi
from core import usage_optimizer
from integrations.tuya.ai_tools import (
    build_scene_payload_from_instructions,
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

# --- Configuration ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = "openai/gpt-oss-120b"  # User requested model

if not GROQ_API_KEY:
    print("⚠️ GROQ_API_KEY not found in .env. Please add it.")

DEFAULT_STATION_NAME = os.getenv("DEFAULT_STATION_NAME", "").strip()
DEFAULT_STATION_ID = os.getenv("DEFAULT_STATION_ID", "").strip()
DEFAULT_POWERSTATION_ID = os.getenv("DEFAULT_POWERSTATION_ID", "").strip()
GOODWE_POWERSTATION_ID = os.getenv("GOODWE_POWERSTATION_ID", "").strip()

def _get_default_powerstation_id() -> str:
    """
    Helper to resolve the default powerstation ID from env vars or by querying the API.
    Ported from core/gemini.py logic.
    """
    # Check all possible env vars for the ID
    candidate = DEFAULT_STATION_ID or DEFAULT_POWERSTATION_ID or GOODWE_POWERSTATION_ID
    if candidate:
        return candidate
    
    try:
        api = goodweApi.GoodweApi()
        plants = api.ListPlants() or {}
        plant_list = plants.get("plants", []) if isinstance(plants, dict) else []
        
        # Try to match by name if provided
        if DEFAULT_STATION_NAME:
            for p in plant_list:
                if (p.get("stationname") or "").strip().lower() == DEFAULT_STATION_NAME.strip().lower():
                    return p.get("powerstation_id") or ""
        
        # Fallback to the first plant found
        return (plant_list[0].get("powerstation_id") if plant_list else "") or ""
    except Exception as e:
        print(f"⚠️ Error resolving default powerstation ID: {e}")
        return ""




# --- Tuya Context ---
# We reuse the TuyaContextManager from core.gemini for now to avoid code duplication.
# In a full refactor, we would move it to a shared module like core.tuya_context.
tuya_context = TuyaContextManager(DEFAULT_TUYA_SPACE_ID)

# --- Tools Definition ---

@tool
def get_today_date() -> Dict[str, str]:
    """Return today's date in ISO format (America/Sao_Paulo)."""
    tz = ZoneInfo("America/Sao_Paulo")
    now = datetime.now(tz)
    return {"today": now.date().isoformat()}

@tool
def list_plants() -> Dict[str, Any]:
    """List all GoodWe plants available for the authenticated account."""
    api = goodweApi.GoodweApi()
    return api.ListPlants() or {}

@tool
def get_powerstation_battery_status(powerstation_id: Optional[str] = None) -> Dict[str, Any]:
    """Return battery status for a powerstation. If powerstation_id is missing, use the configured default plant."""
    api = goodweApi.GoodweApi()
    
    target_id = powerstation_id or _get_default_powerstation_id()
    if not target_id:
        return {"error": "No powerstation_id provided and could not resolve default."}
        
    return api.GetSoc(target_id) or {"error": "Failed to retrieve battery status (API returned None)."}

@tool
def get_alarms_by_range(start_date: str, end_date: Optional[str] = None, status: str = "3", stationname: Optional[str] = None) -> Dict[str, Any]:
    """Return alarms for a date/range. 
    start_date: YYYY-MM-DD
    end_date: YYYY-MM-DD (optional)
    status: "0"=Active, "1"=History, "3"=All
    stationname: Optional case-insensitive station name filter
    """
    api = goodweApi.GoodweApi()
    # Note: The gemini version had complex date parsing logic (_auto_date_range).
    # We assume the LLM is smart enough to provide ISO dates, or we might need to port that helper.
    # For now, we expect the LLM to follow the docstring.
    return api.GetAlarmsByRange(start_date=start_date, end_date=end_date, status=status, stationname=stationname)

@tool
def get_warning_detail(stationid: str, warningid: str, devicesn: str) -> Dict[str, Any]:
    """Get human-readable detail for a specific warning."""
    api = goodweApi.GoodweApi()
    return api.GetWarningDetailTranslated(stationid, warningid, devicesn)

@tool
def get_powerstation_power_and_income_by_day(powerstation_id: str, date: str) -> Dict[str, Any]:
    """Get daily energy generation and income.
    powerstation_id: Powerstation ID.
    date: YYYY-MM-DD
    """
    api = goodweApi.GoodweApi()
    return api.GetPowerAndIncomeByDay(powerstation_id=powerstation_id, date=date)

@tool
def get_powerstation_power_and_income_by_month(powerstation_id: str, date: str) -> Dict[str, Any]:
    """Get monthly energy generation and income.
    powerstation_id: Powerstation ID.
    date: YYYY-MM-DD (any day in the month)
    """
    api = goodweApi.GoodweApi()
    return api.GetPowerAndIncomeByMonth(powerstation_id=powerstation_id, date=date)

@tool
def get_powerstation_power_and_income_by_year(powerstation_id: str, date: str) -> Dict[str, Any]:
    """Get yearly energy generation and income.
    powerstation_id: Powerstation ID.
    date: YYYY-MM-DD (any day in the year)
    """
    api = goodweApi.GoodweApi()
    return api.GetPowerAndIncomeByYear(powerstation_id=powerstation_id, date=date)

@tool
def get_ev_charger_status(powerstation_id: Optional[str] = None) -> Dict[str, Any]:
    """Return EV charger status for a powerstation_id."""
    api = goodweApi.GoodweApi()
    return api.GetEvChargerChargingMode(powerstation_id)

@tool
def change_ev_charger_status(charge_mode: int) -> Dict[str, Any]:
    """Change the EV charger mode.
    charge_mode: 1 - Fast, 2 - PV Priority, 3 - PV & Battery.
    """
    api = goodweApi.GoodweApi()
    return api.ChangeEvChargerChargingMode(powerstation_id=DEFAULT_POWERSTATION_ID, charge_mode=charge_mode)

@tool
def optimize_usage(parsed_path: Optional[str] = None) -> Dict[str, Any]:
    """Generate a short statistical report from the last 7 days of minute-level history for optimization tips."""
    return usage_optimizer.optimize_usage(parsed_path)

# --- Tuya Tools ---

@tool
def tuya_describe_space(space_id: Optional[str] = None, config_path: Optional[str] = None) -> Dict[str, Any]:
    """List Tuya devices and scenes for a space."""
    sid = space_id or tuya_context.default_space_id
    res = describe_space(sid, config_path=config_path)
    if isinstance(res, dict):
        tuya_context._update_cache(sid, res)
    return res

@tool
def tuya_inspect_device(device_id: str, codes: Optional[List[str]] = None) -> Dict[str, Any]:
    """Fetch datapoints for a Tuya device to explain codes and values."""
    return inspect_device(device_id, codes=codes)

@tool
def tuya_propose_automation(space_id: Optional[str] = None, heuristic_set: Optional[List[str]] = None, config_path: Optional[str] = None, heuristic_overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Generate Tuya automation payloads using heuristics (preview only)."""
    sid = space_id or tuya_context.default_space_id
    return propose_automation(sid, heuristic_set, config_path=config_path, heuristic_overrides=heuristic_overrides)

@tool
def tuya_build_scene_payload(instructions: str, space_id: Optional[str] = None, device_ids: Optional[List[str]] = None, name_hint: Optional[str] = None, decision_expr_hint: Optional[str] = None, effective_time_hint: Optional[Dict[str, Any]] = None, type_hint: Optional[str] = None) -> Dict[str, Any]:
    """Generate and create a Tuya automation or tap-to-run from natural language instructions."""
    sid = space_id or tuya_context.default_space_id
    return build_scene_payload_from_instructions(
        instructions=instructions,
        space_id=sid,
        device_ids=device_ids,
        name_hint=name_hint,
        decision_expr_hint=decision_expr_hint,
        effective_time_hint=effective_time_hint,
        type_hint=type_hint
    )

@tool
def tuya_create_and_enable_automation(payload: Dict[str, Any], confirm: bool = True, enable: bool = True) -> Dict[str, Any]:
    """Create (and optionally enable) a Tuya scene. Requires explicit confirmation."""
    res = create_and_enable_automation(payload, confirm=confirm, enable=enable)
    tuya_context.invalidate()
    return res

@tool
def tuya_update_automation(rule_id: str, payload: Dict[str, Any], confirm: bool = True) -> Dict[str, Any]:
    """Update an existing Tuya scene with a new payload."""
    res = update_automation(rule_id, payload, confirm=confirm)
    tuya_context.invalidate()
    return res

@tool
def tuya_delete_automations(rule_ids: List[str], space_id: Optional[str] = None, config_path: Optional[str] = None, confirm: bool = True) -> Dict[str, Any]:
    """Delete one or more Tuya scenes."""
    res = delete_automations(rule_ids, space_id=space_id, config_path=config_path, confirm=confirm)
    tuya_context.invalidate()
    return res

@tool
def tuya_set_automation_state(rule_ids: List[str], enable: bool, confirm: bool = True) -> Dict[str, Any]:
    """Enable or disable a list of Tuya scenes."""
    res = set_automation_state(rule_ids, enable=enable, confirm=confirm)
    tuya_context.invalidate()
    return res

@tool
def tuya_trigger_scene(rule_id: str, confirm: bool = True) -> Dict[str, Any]:
    """Trigger a Tuya scene manually."""
    return trigger_scene(rule_id, confirm=confirm)


ALL_TOOLS = [
    get_today_date,
    list_plants,
    get_powerstation_battery_status,
    get_alarms_by_range,
    get_warning_detail,
    get_powerstation_power_and_income_by_day,
    get_powerstation_power_and_income_by_month,
    get_powerstation_power_and_income_by_year,
    get_ev_charger_status,
    change_ev_charger_status,
    optimize_usage,
    tuya_describe_space,
    tuya_inspect_device,
    tuya_propose_automation,
    tuya_build_scene_payload,
    tuya_create_and_enable_automation,
    tuya_update_automation,
    tuya_delete_automations,
    tuya_set_automation_state,
    tuya_trigger_scene,
]

# --- Agent Setup ---

def get_system_prompt() -> str:
    try:
        with open("system_prompt.txt", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "You are BotSolar, a GoodWe and Tuya assistant."

def initialize_agent():
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY is missing.")
    
    llm = ChatGroq(
        temperature=0,
        model_name=GROQ_MODEL,
        groq_api_key=GROQ_API_KEY
    )

    # prompt = ChatPromptTemplate.from_messages(
    #     [
    #         ("system", get_system_prompt()),
    #         ("placeholder", "{chat_history}"),
    #         ("human", "{input}"),
    #         ("placeholder", "{agent_scratchpad}"),
    #     ]
    # )


    agent = create_agent(model=llm, tools=ALL_TOOLS, system_prompt=get_system_prompt())
    # agent_executor = AgentExecutor(agent=agent, tools=ALL_TOOLS, verbose=True)
    return agent

# --- Main Interface ---

_agent_executor = None

def get_agent_executor():
    global _agent_executor
    if _agent_executor is None:
        _agent_executor = initialize_agent()
        # Warmup background tasks
        if DEFAULT_TUYA_SPACE_ID:
            threading.Thread(target=prewarm_tuya_caches, args=(DEFAULT_TUYA_SPACE_ID,), daemon=True).start()
        threading.Thread(target=prewarm_scene_builder, daemon=True).start()
    return _agent_executor


_last_responses_by_ip: Dict[str, Dict[str, Any]] = {}

async def call_llm(user_input: str, powerstation_id: Optional[str] = None, user_ip: Optional[str] = None) -> Dict[
    str, Any]:
    """
    Main entry point for the API.
    Saves last response per IP and prepends start messages (system + last assistant response for IP).
    """
    global _last_responses_by_ip
    try:
        agent = get_agent_executor()

        # Augment input with Tuya context if needed
        augmented_input, _ = await tuya_context.augment_user_input(user_input)

        # Build start messages: system prompt, optional last assistant response for this IP, then user message
        messages = []
        system_text = get_system_prompt()
        messages.append({"role": "system", "content": system_text})

        if user_ip:
            last = _last_responses_by_ip.get(user_ip)
            if last and last.get("response"):
                messages.append({"role": "assistant", "content": last["response"]})

        # (optionally add placeholders for chat_history / agent_scratchpad if needed)
        messages.append({"role": "user", "content": augmented_input})

        t0 = time.perf_counter()
        result = await agent.ainvoke({"messages": messages})
        duration = time.perf_counter() - t0

        # Extract output robustly
        output = ""
        try:
            if isinstance(result, dict) and "messages" in result and isinstance(result["messages"], list) and result[
                "messages"]:
                last_msg = result["messages"][-1]
                if isinstance(last_msg, dict):
                    output = last_msg.get("content", "")
                else:
                    output = getattr(last_msg, "content", str(last_msg))
            elif isinstance(result, dict):
                output = result.get("output", "") or str(result)
            else:
                output = str(result)
        except Exception:
            output = str(result)

        # Save last response by IP
        key = user_ip or "unknown"
        _last_responses_by_ip[key] = {
            "response": output,
            "timestamp": datetime.utcnow().isoformat(),
            "duration_s": duration,
        }

        return {
            "response": output,
            "functions_preview": [],  # TODO: Extract from agent steps if needed
            "fallback_to_default": False,
            "used_powerstation_id": powerstation_id,
            "timings": {"total_duration_s": duration},
        }

    except Exception as e:
        print(f"❌ Error in call_llm: {e}")
        import traceback
        traceback.print_exc()
        return {
            "response": f"❌ Error processing your request: {str(e)}",
            "functions_preview": [],
            "fallback_to_default": False,
            "used_powerstation_id": powerstation_id,
            "timings": {},
        }
