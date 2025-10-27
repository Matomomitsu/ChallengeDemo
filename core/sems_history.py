import os
import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

from .goodweApi import GoodweApi

# Defaults aligned with current project usage
DEFAULT_STATION_ID = "7f9af1fc-3a9a-4779-a4c0-ca6ec87bd93a"  # Demonstração
DEFAULT_INVERTER_SN = "53600ERN238W0001"  # Demonstração


def _format_portal_ts(dt: datetime) -> str:
    """
    Format datetime to the SEMS expected "YYYY-MM-DD HH:MM" (no seconds).
    """
    return dt.strftime("%Y-%m-%d %H:%M")


def _ensure_station_context(api: GoodweApi, station_id: Optional[str], inverter_sn_override: Optional[str]) -> Tuple[str, str, str]:
    """
    Resolve station_id, station_name and inverter_sn using configured env vars
    or GoodWe endpoints when not provided.
    """
    # Prefer explicit env when available
    env_station_id = os.getenv("GOODWE_STATION_ID") or ""
    env_station_name = os.getenv("GOODWE_STATION_NAME") or ""
    station_id = (station_id or env_station_id or DEFAULT_STATION_ID or "").strip()

    # Resolve station_name from plants list if needed
    station_name = env_station_name
    inverter_sn = (inverter_sn_override or os.getenv("GOODWE_INVERTER_SN") or DEFAULT_INVERTER_SN or "").strip()

    try:
        plants = api.ListPlants() or {}
        plant_list = plants.get("plants", []) if isinstance(plants, dict) else []
        if not station_id and plant_list:
            station_id = plant_list[0].get("powerstation_id") or ""
        if not station_name and station_id:
            for p in plant_list:
                if (p.get("powerstation_id") or "") == station_id:
                    station_name = p.get("stationname") or station_name
                    break
    except Exception:
        pass

    # Resolve inverter SN through plant detail if absent
    if not inverter_sn and station_id:
        try:
            details = api.GetPlantDetailByPowerstationId(station_id)
            if details is not None:
                soc = api.extract_soc(details.text)
                if soc and soc.get("soc"):
                    inverter_sn = soc["soc"][0].get("sn") or inverter_sn
        except Exception:
            pass

    return station_id, station_name, inverter_sn


def _build_history_payload(
    station_id: str,
    station_name: str,
    inverter_sn: str,
    start_dt: datetime,
    end_dt: datetime,
    data_type: int = 0,
    times_type: int = 1,
    targets: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Build payload for HistoryData/GetStationHistoryDataChart.
    By default focuses on Cbattery1 (SOC) and Pmeter (solar power).
    """
    if targets is None:
        targets = [
            {"target_key": "Cbattery1", "target_index": 44},
            {"target_key": "Pmeter", "target_index": 38},
        ]

    return {
        "data_type": data_type,
        "times_type": times_type,
        "qry_time_start": _format_portal_ts(start_dt),
        "qry_time_end": _format_portal_ts(end_dt),
        "pws_historys": [
            {
                "id": station_id,
                "pw_name": station_name or station_id,
                "status": 1,
                "inverters": [
                    {
                        "sn": inverter_sn,
                        "name": inverter_sn or "inverter",
                        "change_num": 0,
                        "change_type": 0,
                        "relation_sn": None,
                        "relation_name": None,
                        "status": 1,
                    }
                ],
                # address is optional for this endpoint
                "pw_address": "",
            }
        ],
        "qry_status": 1,
        "targets": targets,
        "times": 5,
    }


def _request_history(api: GoodweApi, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Perform the POST request to the SEMS history endpoint using GoodweApi token.
    """
    token = api.GetToken()
    if not token:
        raise RuntimeError("No GoodWe token available")

    url = "https://eu.semsportal.com/api/HistoryData/GetStationHistoryDataChart"
    headers = {
        "Content-Type": "application/json",
        "Origin": "https://www.semsportal.com",
        "Referer": "https://www.semsportal.com/",
        "accept": "application/json, text/javascript, */*; q=0.01",
        "User-Agent": "Mozilla/5.0",
        "token": token,
    }
    r = requests.post(url, headers=headers, data=json.dumps(payload), timeout=30)
    r.raise_for_status()
    return r.json()


def parse_history_generic(response_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generic parser: builds a list of points per timestamp with values for all targets.
    """
    from datetime import datetime as _dt

    parsed: Dict[str, Any] = {
        "metadata": {
            "success": not response_data.get("hasError", True),
            "message": response_data.get("msg", ""),
            "code": response_data.get("code", -1),
            "generated_at": datetime.utcnow().isoformat() + "Z",
        },
        "data_points": [],
    }

    data = response_data.get("data", {})
    stations = data.get("list", [])
    if not stations:
        return parsed

    station = stations[0]
    inverters = station.get("inverters", [])
    if not inverters:
        return parsed

    inverter = inverters[0]
    targets = inverter.get("targets", [])

    by_time: Dict[str, Dict[str, Any]] = {}
    for target in targets:
        tkey = target.get("target_key")
        tunit = target.get("target_unit")
        datas = target.get("datas", []) or []
        for entry in datas:
            ts = entry.get("stat_date")
            if not ts:
                continue
            if ts not in by_time:
                by_time[ts] = {"timestamp": ts, "values": {}}
            try:
                val = float(entry.get("value"))
            except Exception:
                val = entry.get("value")
            by_time[ts]["values"][tkey] = {"value": val, "unit": tunit}

    # Sort timestamps ascending
    def _parse_ts(s: str):
        try:
            return _dt.strptime(s, "%Y-%m-%d %H:%M:%S")
        except Exception:
            try:
                return _dt.strptime(s, "%Y-%m-%d %H:%M")
            except Exception:
                return _dt.min

    points = list(by_time.values())
    points.sort(key=lambda x: _parse_ts(x["timestamp"]))
    parsed["data_points"] = points
    parsed["metadata"]["total_points"] = len(points)
    return parsed


def parse_battery_solar_focus(response_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Focused parser: battery SOC (Cbattery1) and solar generation (Pmeter).
    """
    result = {
        "status": {"api_success": not response_data.get("hasError", True), "message": response_data.get("msg", "")},
        "readings": [],
        "summary": {"total_readings": 0, "avg_battery_soc": None, "max_solar_generation": None, "generating_periods": 0},
    }

    data = response_data.get("data", {})
    stations = data.get("list", [])
    if not stations:
        return result

    station = stations[0]
    inverters = station.get("inverters", [])
    if not inverters:
        return result

    inverter = inverters[0]
    targets = inverter.get("targets", [])

    readings_by_time: Dict[str, Dict[str, Any]] = {}
    for target in targets:
        tkey = target.get("target_key")
        if tkey not in {"Cbattery1", "Pmeter"}:
            continue
        for entry in target.get("datas", []) or []:
            ts = entry.get("stat_date", "")
            try:
                val = float(entry.get("value"))
            except Exception:
                val = 0.0
            if ts not in readings_by_time:
                readings_by_time[ts] = {
                    "time": ts,
                    "battery_soc_percent": None,
                    "solar_generation_w": None,
                    "is_generating": False,
                    "battery_status": "unknown",
                }
            if tkey == "Cbattery1":
                readings_by_time[ts]["battery_soc_percent"] = val
                if val >= 80:
                    readings_by_time[ts]["battery_status"] = "high"
                elif val >= 50:
                    readings_by_time[ts]["battery_status"] = "medium"
                elif val >= 20:
                    readings_by_time[ts]["battery_status"] = "low"
                else:
                    readings_by_time[ts]["battery_status"] = "critical"
            elif tkey == "Pmeter":
                readings_by_time[ts]["solar_generation_w"] = val
                readings_by_time[ts]["is_generating"] = val > 0

    readings = sorted(readings_by_time.values(), key=lambda x: x["time"]) if readings_by_time else []
    result["readings"] = readings
    result["summary"]["total_readings"] = len(readings)
    if readings:
        bs = [r["battery_soc_percent"] for r in readings if r["battery_soc_percent"] is not None]
        sg = [r["solar_generation_w"] for r in readings if r["solar_generation_w"] is not None]
        if bs:
            result["summary"]["avg_battery_soc"] = round(sum(bs) / len(bs), 1)
        if sg:
            result["summary"]["max_solar_generation"] = max(sg)
            result["summary"]["generating_periods"] = len([r for r in readings if r["is_generating"]])
    return result


def fetch_and_parse_7d(
    station_id: Optional[str] = None,
    inverter_sn: Optional[str] = None,
    save_files: bool = True,
    targets: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Fetch last 7 days (inclusive) history and return both raw and parsed views.
    Does not integrate with Gemini; designed to be called on-demand.
    """
    load_dotenv()
    api = GoodweApi()

    # Define date range: from 7 days ago 00:00 to today 23:59
    today = datetime.now()
    start_dt = (today - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
    end_dt = today.replace(hour=23, minute=59, second=0, microsecond=0)

    resolved_station_id, station_name, resolved_sn = _ensure_station_context(api, station_id, inverter_sn)
    if not resolved_station_id or not resolved_sn:
        raise RuntimeError("Missing station_id or inverter serial number (sn) to request history")

    payload = _build_history_payload(
        station_id=resolved_station_id,
        station_name=station_name,
        inverter_sn=resolved_sn,
        start_dt=start_dt,
        end_dt=end_dt,
        targets=targets,
    )

    raw = _request_history(api, payload)
    parsed_generic = parse_history_generic(raw)
    parsed_focus = parse_battery_solar_focus(raw)

    files = {}
    if save_files:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        os.makedirs("data", exist_ok=True)
        raw_path = os.path.join("data", f"history7d_raw_{ts}.json")
        parsed_path = os.path.join("data", f"history7d_parsed_{ts}.json")
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
        with open(parsed_path, "w", encoding="utf-8") as f:
            json.dump(parsed_generic, f, ensure_ascii=False, indent=2)
        files = {"raw": raw_path, "parsed": parsed_path}

    return {"raw": raw, "parsed_generic": parsed_generic, "parsed_focus": parsed_focus, "files": files}


