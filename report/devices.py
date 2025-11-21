import os
from collections import defaultdict
from datetime import datetime
import pymongo
import datetime as _dt
from typing import Dict, Any, List, Tuple, Set, Optional

def get_hour_devices_on(date_start: _dt.datetime, date_end: _dt.datetime) -> Dict[_dt.datetime, List[Dict[str, Any]]]:
    host = os.getenv("MONGO_URI")
    client = pymongo.MongoClient(host)
    db = client["fiap_challenge"]
    collection = db["devicesInfo"]

    def _truthy(val: Any) -> bool:
        if isinstance(val, bool):
            return val
        if val is None:
            return False
        if isinstance(val, (int, float)):
            return val != 0
        if isinstance(val, str):
            return val.lower() in ("true", "1", "on", "yes")
        return bool(val)

    results: Dict[_dt.datetime, List[Dict[str, Any]]] = {}
    seen_by_ts: Dict[_dt.datetime, Set[str]] = defaultdict(set)

    cursor = collection.find({"timestamp": {"$gte": date_start, "$lte": date_end}}).sort("timestamp", 1)
    for doc in cursor:
        if isinstance(doc.get("devices"), list):
            ts = doc.get("timestamp")
            if not ts:
                continue
            for dev in doc.get("devices", []):
                dev_id = dev.get("id")
                if not dev_id:
                    continue
                if not _truthy(dev.get("isOnline")):
                    continue
                category = (dev.get("category") or "").lower()
                props = dev.get("properties", {}) or {}
                ok = False
                if category == "dj":
                    ok = _truthy(props.get("switch_led"))
                elif category == "cz":
                    ok = _truthy(props.get("switch_1"))
                if ok and dev_id not in seen_by_ts[ts]:
                    seen_by_ts[ts].add(dev_id)
                    results.setdefault(ts, []).append({
                        "timestamp": ts,
                        "id": dev_id,
                        "name": dev.get("name"),
                        "customName": dev.get("customName"),
                    })
            continue

        ts = doc.get("timestamp")
        if not ts:
            continue
        dev_id = doc.get("deviceId") or doc.get("id")
        if not dev_id:
            continue
        if not _truthy(doc.get("isOnline")):
            continue
        category = (doc.get("category") or "").lower()
        props = doc.get("properties", {}) or {}
        ok = False
        if category == "dj":
            ok = _truthy(props.get("switch_led"))
        elif category == "cz":
            ok = _truthy(props.get("switch_1"))
        if ok and dev_id not in seen_by_ts[ts]:
            seen_by_ts[ts].add(dev_id)
            results.setdefault(ts, []).append({
                "timestamp": ts,
                "id": dev_id,
                "name": doc.get("name"),
                "customName": doc.get("customName"),
            })

    for ts in list(results.keys()):
        results[ts] = sorted(results[ts], key=lambda x: x.get("id") or "")

    return results

def get_device_data(device_id: str, date_start: _dt.datetime, date_end: _dt.datetime) -> List[Dict[str, Any]]:
    host = os.getenv("MONGO_URI")
    client = pymongo.MongoClient(host)
    db = client["fiap_challenge"]
    collection = db["devicesInfo"]

    def _truthy(val: Any) -> bool:
        if isinstance(val, bool):
            return val
        if val is None:
            return False
        if isinstance(val, (int, float)):
            return val != 0
        if isinstance(val, str):
            return val.lower() in ("true", "1", "on", "yes")
        return bool(val)

    results: List[Dict[str, Any]] = []
    cursor = collection.find({"timestamp": {"$gte": date_start, "$lte": date_end}}).sort("timestamp", 1)
    for doc in cursor:
        # compat: snapshot document with devices array
        if isinstance(doc.get("devices"), list):
            ts = doc.get("timestamp")
            if not ts:
                continue
            for dev in doc.get("devices", []):
                if dev.get("id") != device_id:
                    continue
                is_online = _truthy(dev.get("isOnline"))
                category = (dev.get("category") or "").lower()
                props = dev.get("properties", {}) or {}
                on = False
                if is_online:
                    if category == "dj":
                        on = _truthy(props.get("switch_led"))
                    elif category == "cz":
                        on = _truthy(props.get("switch_1"))
                results.append({
                    "timestamp": ts,
                    "id": device_id,
                    "name": dev.get("name"),
                    "customName": dev.get("customName"),
                    "category": category,
                    "isOnline": is_online,
                    "properties": props,
                    "on": on,
                })
                break
            continue

        ts = doc.get("timestamp")
        if not ts:
            continue
        doc_dev_id = doc.get("deviceId") or doc.get("id")
        if doc_dev_id != device_id:
            continue
        is_online = _truthy(doc.get("isOnline"))
        category = (doc.get("category") or "").lower()
        props = doc.get("properties", {}) or {}
        on = False
        if is_online:
            if category == "dj":
                on = _truthy(props.get("switch_led"))
            elif category == "cz":
                on = _truthy(props.get("switch_1"))
        results.append({
            "timestamp": ts,
            "id": device_id,
            "name": doc.get("name"),
            "customName": doc.get("customName"),
            "category": category,
            "isOnline": is_online,
            "properties": props,
            "on": on,
        })

    results.sort(key=lambda x: x["timestamp"])
    return results

def get_devices_last_sample(date_ref: Optional[_dt.datetime] = None) -> Tuple[Optional[_dt.datetime], List[Dict[str, Any]]]:
    host = os.getenv("MONGO_URI")
    client = pymongo.MongoClient(host)
    db = client["fiap_challenge"]
    collection = db["devicesInfo"]

    if date_ref is None:
        date_ref = datetime.now(_dt.timezone.utc)
    elif date_ref.tzinfo is None:
        date_ref = date_ref.replace(tzinfo=_dt.timezone.utc)

    last_doc = collection.find_one({"timestamp": {"$lte": date_ref}}, sort=[("timestamp", -1)])
    if not last_doc:
        return None, []

    ts = last_doc.get("timestamp")
    if not ts:
        return None, []

    cursor = collection.find({"timestamp": ts})
    devices_by_id: Dict[str, Dict[str, Any]] = {}

    for doc in cursor:
        if isinstance(doc.get("devices"), list):
            for dev in doc.get("devices", []):
                dev_id = dev.get("id")
                if not dev_id:
                    continue
                devices_by_id[dev_id] = {
                    "timestamp": ts,
                    "id": dev_id,
                    "name": dev.get("name"),
                    "customName": dev.get("customName"),
                    "category": dev.get("category"),
                    "isOnline": dev.get("isOnline"),
                    "properties": dev.get("properties", {}) or {},
                }
            continue

        dev_id = doc.get("deviceId") or doc.get("id")
        if not dev_id:
            continue
        devices_by_id[dev_id] = {
            "timestamp": ts,
            "id": dev_id,
            "name": doc.get("name"),
            "customName": doc.get("customName"),
            "category": doc.get("category"),
            "isOnline": doc.get("isOnline"),
            "properties": doc.get("properties", {}) or {},
        }

    devices = sorted(devices_by_id.values(), key=lambda d: d.get("id") or "")
    return ts, devices