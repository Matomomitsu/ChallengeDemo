import os
import sys
import logging
from collections import defaultdict
from datetime import datetime, date, time, timedelta
from dotenv import load_dotenv
import pymongo
import datetime as _dt
from typing import Dict, Any, List, Tuple, Set
from core.goodweApi import GoodweApi
from integrations.tuya import TuyaAutomationWorkflow
from integrations.tuya import TuyaClient
from zoneinfo import ZoneInfo
from integrations.tuya.ai_tools import _build_workflow

if __name__ == "__main__":
    load_dotenv(".env")

    host = os.getenv("MONGO_URI")
    client = pymongo.MongoClient(host)
    db = client["fiap_challenge"]
    collection = db["scenes_sugestions"]

    api = GoodweApi()

    space_id = os.getenv("TUYA_SPACE_ID")

    tuyaAutomation, _ = _build_workflow()
    scenes = tuyaAutomation.list_scenes(space_id)
    devices = tuyaAutomation.discover_devices(space_id.split(","))
    device_ids = [(d.get("id") if isinstance(d, dict) else getattr(d, "id", None)) for d in devices]
    device_ids = [did for did in device_ids if did]
    device_properties = tuyaAutomation.inspect_properties(device_ids=device_ids) if device_ids else {}
    print(f"Discovered {len(devices)} devices with properties for space {space_id}")

