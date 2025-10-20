import os
from collections import defaultdict
from datetime import datetime, date, time, timedelta
from dotenv import load_dotenv
import pymongo
import datetime as _dt
from typing import Dict, Any, List, Tuple, Set
from core.goodweApi import GoodweApi

FIELD_MAP = {
    "PCurve_Power_PV": "PV",
    "PCurve_Power_Battery": "Battery",
    "PCurve_Power_Meter": "Meter(Grid)",
    "PCurve_Power_Load": "Load",
    "PCurve_Power_GensetPower": "GensetPower",
}

class PlantPowerChart:
    def __init__(self, api_client: Any):
        self.api = api_client

    def _normalize_powerchart(self, api_result: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(api_result, dict):
            return {}
        for key in ("powerChart", "data"):
            if key in api_result and isinstance(api_result[key], dict):
                return api_result[key]
        return api_result

    def _assign_hour_dt(self, hour: int, minute: int, base_date: date) -> datetime:
        """
        Regras de bucket:
        - minute == 0:
          - se hour == 0 -> pertence a 01:00 (00:00 é parte de 1:00)
          - caso contrário -> hour:00
        - minute > 0:
          - se hour < 23 -> (hour+1):00
          - se hour == 23 -> 23:59 (manter no mesmo dia)
        """
        if minute == 0:
            if hour == 0:
                return datetime.combine(base_date, time(hour=1, minute=0, second=0))
            return datetime.combine(base_date, time(hour=hour, minute=0, second=0))
        # minute > 0
        if hour < 23:
            return datetime.combine(base_date, time(hour=hour + 1, minute=0, second=0))
        return datetime.combine(base_date, time(hour=23, minute=59, second=0))

    def aggregate_hourly(self, powerchart: Dict[str, Any], date_str: str) -> Tuple[
        List[Tuple[datetime, Dict[str, float]]], Set[Tuple[int, int, int, int]]]:
        sums_by_dt: Dict[datetime, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
        raw_presence: Set[Tuple[int, int, int, int]] = set()
        try:
            base_date = date.fromisoformat(date_str)
        except Exception:
            base_date = date.today()

        lines = powerchart.get("lines", []) if isinstance(powerchart, dict) else []

        for line in lines:
            key = line.get("key")
            if key not in FIELD_MAP:
                continue
            field_name = FIELD_MAP[key]
            for point in line.get("xy", []):
                x = point.get("x")
                y = point.get("y") or 0.0
                if not x:
                    continue
                try:
                    parts = str(x).split(":")
                    orig_hour = int(parts[0])
                    orig_minute = int(parts[1]) if len(parts) > 1 else 0
                except Exception:
                    continue

                # registra presença somente se minuto >= 55
                if orig_minute >= 55:
                    raw_presence.add((base_date.year, base_date.month, base_date.day, orig_hour))

                dt_hour = self._assign_hour_dt(orig_hour, orig_minute, base_date)
                try:
                    sums_by_dt[dt_hour][field_name] += float(y)
                except Exception:
                    continue

        result: List[Tuple[datetime, Dict[str, float]]] = []
        for dt_hour in sorted(sums_by_dt.keys()):
            hour_values: Dict[str, float] = {}
            for mapped in FIELD_MAP.values():
                hour_values[mapped] = float(sums_by_dt[dt_hour].get(mapped, 0.0))
            result.append((dt_hour, hour_values))
        return result, raw_presence

    def _db_prev_hour_has_min55(self, prev_dt: datetime, collection: pymongo.collection.Collection) -> bool:
        start = datetime(prev_dt.year, prev_dt.month, prev_dt.day, prev_dt.hour, 55, 0)
        end = datetime(prev_dt.year, prev_dt.month, prev_dt.day, prev_dt.hour, 59, 59, 999999)
        found = collection.find_one({"timestamp": {"$gte": start, "$lte": end}})
        return found is not None

    def _raw_presence_has_min55(self, prev_dt: datetime, raw_presence: Set[Tuple[int, int, int, int]]) -> bool:
        """
        `raw_presence` guarda (year, month, day, hour) apenas quando orig_minute >= 55.
        Aqui checamos membership direta da chave correspondente a prev_dt.hour.
        """
        key = (prev_dt.year, prev_dt.month, prev_dt.day, prev_dt.hour)
        return key in raw_presence

    def fetch_and_insert(self, powerstation_id: str, date_str: str, collection: pymongo.collection.Collection) -> int:
        try:
            base_date = date.fromisoformat(date_str)
        except Exception:
            base_date = date.today()

        day_start = datetime.combine(base_date, time(0, 0, 0))
        day_end = datetime.combine(base_date, time(23, 59, 59, 999999))

        last_docs = list(
            collection.find({"timestamp": {"$gte": day_start, "$lte": day_end}})
            .sort("timestamp", -1)
            .limit(1)
        )
        last_timestamp_for_day = None
        if last_docs:
            last_timestamp_for_day = last_docs[0].get("timestamp")
            if last_timestamp_for_day.hour == 23 and last_timestamp_for_day.minute >= 55:
                return 0

        api_result = self.api.GetPlantPowerChart(powerstation_id, date_str)
        if not isinstance(api_result, dict):
            return 0
        if api_result.get("hasError"):
            return 0

        powerchart = self._normalize_powerchart(api_result)
        hourly, raw_presence = self.aggregate_hourly(powerchart, date_str)

        to_insert = []
        for dt_hour, values in hourly:
            if collection.find_one({"timestamp": dt_hour}):
                continue
            if last_timestamp_for_day is not None and dt_hour <= last_timestamp_for_day:
                continue

            if dt_hour.hour == 23 and dt_hour.minute == 59:
                prev_dt_for_check = datetime(dt_hour.year, dt_hour.month, dt_hour.day, 23, 0, 0)
            else:
                prev_dt_for_check = dt_hour - timedelta(hours=1)

            prev_complete = False
            if self._raw_presence_has_min55(prev_dt_for_check, raw_presence):
                prev_complete = True
            elif self._db_prev_hour_has_min55(prev_dt_for_check, collection):
                prev_complete = True

            if not prev_complete:
                continue

            doc = {"timestamp": dt_hour}
            doc.update(values)
            to_insert.append(doc)

        if to_insert:
            collection.insert_many(to_insert)
        return len(to_insert)

    def fetch_and_insert_days(self, powerstation_id: str, end_date_str: str, days: int, collection: pymongo.collection.Collection) -> int:
        try:
            end_date = date.fromisoformat(end_date_str)
        except Exception:
            end_date = date.today()
        total_inserted = 0
        for i in range(days - 1, -1, -1):
            day = end_date - timedelta(days=i)
            day_str = day.isoformat()
            inserted = self.fetch_and_insert(powerstation_id, day_str, collection)
            total_inserted += inserted
        return total_inserted

if __name__ == "__main__":
    load_dotenv(".env")

    host = os.getenv("MONGO_URI")
    client = pymongo.MongoClient(host)
    db = client["fiap_challenge"]
    collection = db["hourly_data"]

    api = GoodweApi()
    charter = PlantPowerChart(api)

    import time as _time

    while True:
        try:
            date_str = str(_dt.date.today())
            inserted = charter.fetch_and_insert_days(
                os.getenv("DEFAULT_POWERSTATION_ID"),
                date_str,
                days=6,
                collection=collection,
            )
            print(f"Inserted {inserted} hourly documents for last 5 days ending {date_str}")
        except Exception as e:
            print(f"Error during fetch: {e}")

        now = datetime.now()
        next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        sleep_seconds = (next_hour - now).total_seconds()
        print("Sleeping for %.2f seconds until %s" % (sleep_seconds, next_hour.isoformat()))
        _time.sleep(max(0, sleep_seconds))
