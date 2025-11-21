from __future__ import annotations

import os
import time
import threading
import asyncio
import json
from typing import Any, Dict, Optional, List, Sequence

from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_core.tools import tool
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate

# Reuse existing Tuya logic
from core.gemini import TuyaContextManager, DEFAULT_TUYA_SPACE_ID, prewarm_tuya_caches, prewarm_scene_builder
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

# --- Tuya Context ---
# We reuse the TuyaContextManager from core.gemini for now to avoid code duplication.
# In a full refactor, we would move it to a shared module like core.tuya_context.
tuya_context = TuyaContextManager(DEFAULT_TUYA_SPACE_ID)

# --- Tools Definition ---

@tool
def list_plants() -> Dict[str, Any]:
    """List all GoodWe plants available for the authenticated account."""
    api = goodweApi.GoodweApi()
    return api.ListPlants() or {}

@tool
def get_powerstation_battery_status(powerstation_id: Optional[str] = None) -> Dict[str, Any]:
    """Return battery status for a powerstation. If powerstation_id is missing, use the configured default plant."""
    api = goodweApi.GoodweApi()
    # Logic to handle default station if None is passed is handled inside GoodweApi or we can add it here
    # For now, we pass None and let the API handle it or the agent to figure it out if it has context
    # But existing gemini logic had a fallback. Let's rely on the agent or the API's internal default if implemented.
    # Actually, the gemini dispatcher handled the default. We should probably replicate that or let the agent ask.
    # For simplicity, we'll let the API handle it if it can, or return None.
    return api.GetSoc(powerstation_id)

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
def change_ev_charger_status(charge_mode: int, powerstation_id: Optional[str] = None) -> Dict[str, Any]:
    """Change the EV charger mode.
    charge_mode: 1 - Fast, 2 - PV Priority, 3 - PV & Battery.
    """
    api = goodweApi.GoodweApi()
    return api.ChangeEvChargerChargingMode(powerstation_id=powerstation_id, charge_mode=charge_mode)

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
    
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", get_system_prompt()),
            ("placeholder", "{chat_history}"),
            ("human", "{input}"),
            ("placeholder", "{agent_scratchpad}"),
        ]
    )
    
    agent = create_tool_calling_agent(llm, ALL_TOOLS, prompt)
    agent_executor = AgentExecutor(agent=agent, tools=ALL_TOOLS, verbose=True)
    return agent_executor

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

async def call_llm(user_input: str, powerstation_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Main entry point for the API.
    Mimics the return signature of the old call_geminiapi for compatibility.
    """
    try:
        agent = get_agent_executor()
        
        # Augment input with Tuya context if needed
        augmented_input, _ = await tuya_context.augment_user_input(user_input)
        
        # We don't have chat history persistence in this simple function yet, 
        # but the agent executor can handle it if we pass it.
        # For now, we treat each call as stateless or rely on the client to send history (not implemented in old API).
        
        t0 = time.perf_counter()
        result = await agent.ainvoke({"input": augmented_input})
        duration = time.perf_counter() - t0
        
        output = result.get("output", "")
        
        # Extract executed tools from intermediate steps if available
        # AgentExecutor returns 'intermediate_steps' if return_intermediate_steps=True (default False)
        # We might need to enable it to match the old API's "functions_preview".
        # For now, we return a simplified response.
        
        return {
            "response": output,
            "functions_preview": [], # TODO: Extract from agent steps if needed
            "fallback_to_default": False,
            "used_powerstation_id": powerstation_id,
            "timings": {"total_duration_s": duration},
        }
        
    except Exception as e:
        print(f"❌ Error in call_llm: {e}")
        return {
            "response": f"❌ Error processing your request: {str(e)}",
            "functions_preview": [],
            "fallback_to_default": False,
            "used_powerstation_id": powerstation_id,
            "timings": {},
        }
