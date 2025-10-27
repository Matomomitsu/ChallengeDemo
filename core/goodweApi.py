import time
import os
from urllib import response
from zoneinfo import ZoneInfo
import requests
import base64
import json
from datetime import datetime, date, timedelta
from collections import defaultdict
from core.cacheServices import CacheServices
from dotenv import load_dotenv
import sqlite3
import uuid
from collections.abc import Sized

class GoodweApi:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, "_initialized"):
            load_dotenv()
            self.token = None
            self.tokenExp = None
            self._last_alarm_context = None
            self._translations = None
            self.GetToken()
            self._initialized = True
        return

    def TokenExpired(self):
        if self.tokenExp:
            timenow = int(time.time() * 1000)
            return self.tokenExp < timenow
        return True

    def get_date(self, date: str = None) -> str:
        """Returns the current date in ISO-8601 format."""
        tz = ZoneInfo("America/Sao_Paulo")
        match (date):
            case "today" | "hoje":
                return datetime.now(tz).date().isoformat()
            case "yesterday" | "ontem":
                return (datetime.now(tz).date() - timedelta(days=1)).isoformat()
            case None:
                return datetime.now(tz).date().isoformat()
        return date

    def GetToken(self):
        if not self.TokenExpired():
            return self.token

        url = 'https://us.semsportal.com/api/v2/common/crosslogin'

        original_string = '{"uid":"","timestamp":0,"token":"","client":"web","version":"","language":"en"}'
        bytes_data = original_string.encode('utf-8')
        encoded_bytes = base64.b64encode(bytes_data)
        encoded_string = encoded_bytes.decode('utf-8')

        headers = {
            'Token': encoded_string,
            'Content-Type': 'application/json',
            'Accept': '*/*'
        }

        payload = {
            'account': os.getenv('GOODWE_ACCOUNT'),
            'pwd': os.getenv('GOODWE_PASSWORD'),
            'agreement_agreement': 0,
            'is_local': False
        }

        response = requests.post(url, json=payload, headers=headers)

        if response.status_code == 200 and response.json() and "data" in response.json() and response.json()[
            "data"] is not None:
            dataToString = json.dumps(response.json()["data"])
            bytes_data = dataToString.encode('utf-8')
            encoded_bytes = base64.b64encode(bytes_data)
            encoded_string = encoded_bytes.decode('utf-8')

            self.tokenExp = response.json()["data"]["timestamp"]
            self.tokenExp += 4 * 60 * 60 * 1000

            print("Login successful!")
            self.token = encoded_string
            return self.token
        else:
            print(
                f"Login failed or 'data' not found in response. Status Code: {response.status_code}, Response: {response.json()}")
            return None

    def extract_powerstations(self, json_str):
        dados = json.loads(json_str)
        lista = dados["data"]["list"]
        resultado = [
            {"powerstation_id": item["powerstation_id"], "stationname": item["stationname"]}
            for item in lista
        ]
        return {"plants": resultado}

    def ListPlants(self):
        cache = CacheServices.instance()
        cache_key = "lista_plantas"
        cached = cache.get(cache_key)
        if cached:
            print("Plants retrieved from cache (6h)!")
            return cached

        token = self.GetToken()
        if not token:
            return None

        url = 'https://us.semsportal.com/api/PowerStationMonitor/QueryPowerStationMonitor'

        headers = {
            'Token': token,
            'Content-Type': 'application/json',
            'Accept': '*/*'
        }

        payload = {
            "powerstation_id": "",
            "key": "",
            "orderby": "",
            "powerstation_type": "",
            "powerstation_status": "",
            "page_index": 1,
            "page_size": 20,
            "adcode": "",
            "org_id": "",
            "condition": ""
        }

        response = requests.post(url, json=payload, headers=headers)

        if response.status_code == 200:
            plants = self.extract_powerstations(response.text)
            cache.set(cache_key, plants, ttl_seconds=6 * 60 * 60)  # cache for 6 hours
            print("Plants retrieved successfully!")
            return plants
        else:
            print(f"Failed to retrieve plants with status code: {response.status_code}")
            return None

    def extract_soc(self, json_str):
        dados = json.loads(json_str)
        lista = dados["data"]["soc"]
        resultado = [
            {
                "sn": item.get("sn"),
                "local": item.get("local"),
                "battery_sn": item.get("battery_sn"),
                "power": item.get("power"),
                "status": item.get("status")
            }
            for item in lista if isinstance(item, dict)
        ]
        return {"soc": resultado}

    def GetPlantDetailByPowerstationId(self, powerstation_id):
        token = self.GetToken()
        if not token:
            return None

        url = 'https://us.semsportal.com/api/v3/PowerStation/GetPlantDetailByPowerstationId'

        headers = {
            'Token': token,
            'Content-Type': 'application/x-www-form-urlencoded',
            'Accept': '*/*'
        }

        payload = {
            "powerStationId": powerstation_id,
        }

        response = requests.post(url, data=payload, headers=headers)

        result = response.json()
        data = result.get("data")
        if data is None or (isinstance(data, Sized) and len(data) == 0):
            self.tokenExp = 0
            token = self.GetToken()
            headers['Token'] = token
            response = requests.post(url, data=payload, headers=headers)

        if response.status_code == 200:
            details = response
            print("Plants retrieved successfully!")
            return details
        else:
            print(f"Failed to retrieve plants with status code: {response.status_code}")

            return None

    def GetMonitorSummaryByPowerstationId(self, powerstation_id: str = None, event_time: datetime = None):
        """Return a minimal telemetry snapshot (day/month totals, SOC, load, etc.)."""
        token = self.GetToken()
        if not token:
            return {"hasError": True, "msg": "No token"}

        if not powerstation_id:
            powerstation_id = (
                os.getenv("GOODWE_POWERSTATION_ID")
                or os.getenv("DEFAULT_POWERSTATION_ID")
                or os.getenv("DEFAULT_POWERSTATION")
            )
        if not powerstation_id:
            return {"hasError": True, "msg": "Missing powerstation id"}

        url = f"{self._eu()}/api/v2/PowerStation/GetMonitorDetailByPowerstationId"

        headers = {
            "Token": token,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Origin": "https://www.semsportal.com",
            "Referer": "https://www.semsportal.com/",
            "User-Agent": "Mozilla/5.0",
        }

        payload = {"powerStationId": powerstation_id}

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            data = response.json().get("data")
            if data is None or (isinstance(data, Sized) and len(data) == 0):
                self.tokenExp = 0
                token = self.GetToken()
                headers['Token'] = token
                response = requests.post(url, data=payload, headers=headers)
        except requests.RequestException as exc:
            return {"hasError": True, "msg": f"Request error: {exc}"}

        if response.status_code != 200:
            return {"hasError": True, "code": response.status_code, "msg": response.text}

        try:
            raw = response.json()
        except ValueError as exc:
            return {"hasError": True, "msg": f"Invalid JSON response: {exc}"}

        data_block = raw.get("data") if isinstance(raw, dict) else {}
        inverter_block = {}
        inverter_list = data_block.get("inverter") if isinstance(data_block, dict) else None
        if isinstance(inverter_list, list) and inverter_list and isinstance(inverter_list[0], dict):
            inverter_block = inverter_list[0]
        inverter_data = inverter_block.get("invert_full") if isinstance(inverter_block, dict) else None
        if not isinstance(inverter_data, dict):
            inverter_data = {}

        info_block = data_block.get("info") if isinstance(data_block, dict) else {}
        kpi_block = data_block.get("kpi") if isinstance(data_block, dict) else {}
        powerflow_block = data_block.get("powerflow") if isinstance(data_block, dict) else {}

        def _to_percent_string(value):
            if value is None:
                return None
            try:
                number = float(value)
                return f"{int(round(number))}%"
            except (TypeError, ValueError):
                text = str(value)
                return text if text.endswith("%") else text

        eday_value = inverter_data.get("eday") if inverter_data else None
        if eday_value is None and isinstance(inverter_block, dict):
            eday_value = inverter_block.get("eday")
        if eday_value is None and isinstance(kpi_block, dict):
            eday_value = kpi_block.get("power")

        emonth_value = inverter_data.get("thismonthetotle") if inverter_data else None
        if emonth_value is None and isinstance(kpi_block, dict):
            emonth_value = kpi_block.get("month_generation")

        soc_value = None
        if isinstance(powerflow_block, dict):
            soc_value = powerflow_block.get("soc")
        if soc_value is None and inverter_data:
            soc_value = inverter_data.get("soc")

        temperature_value = inverter_data.get("tempperature") if inverter_data else None
        if temperature_value is None and isinstance(inverter_block, dict):
            temperature_value = inverter_block.get("tempperature")

        model_value = inverter_data.get("model_type") if inverter_data else None
        if model_value is None and isinstance(inverter_block, dict):
            model_value = inverter_block.get("model_type") or inverter_block.get("type")

        pv_value = powerflow_block.get("pv") if isinstance(powerflow_block, dict) else None
        battery_status_value = powerflow_block.get("betteryStatus") if isinstance(powerflow_block, dict) else None
        load_value = powerflow_block.get("load") if isinstance(powerflow_block, dict) else None

        minimal = {
            "powerstation_id": info_block.get("powerstation_id") if isinstance(info_block, dict) else None,
            "stationname": info_block.get("stationname") if isinstance(info_block, dict) else None,
            "sn": inverter_block.get("sn") if isinstance(inverter_block, dict) else None,
            "model_type": model_value,
            "day_income": kpi_block.get("day_income") if isinstance(kpi_block, dict) else None,
            "eday": eday_value,
            "emonth": emonth_value,
            "soc": _to_percent_string(soc_value),
            "tempperature": temperature_value,
            "pv": pv_value,
            "betteryStatus": battery_status_value,
            "load": load_value,
            "event_time": (event_time or datetime.utcnow()).isoformat(),
        }

        return {"hasError": False, "data": minimal}

    def GetSoc(self, powerstation_id):
        token = self.GetToken()
        if not token:
            return None

        details = self.GetPlantDetailByPowerstationId(powerstation_id)

        if details:
            soc = self.extract_soc(details.text)
            print("Plants retrieved successfully!")
            return soc
        else:
            print(f"Failed to retrieve plants with status code: {response.status_code}")

            return None

    # ------------------ helpers ------------------
    def _fmt_portal_dt(self, ymd: str, end_of_day: bool = False) -> str:
        """
        Convert 'YYYY-MM-DD' to 'MM/DD/YYYY HH:mm:ss' as the portal expects.
        """
        d = datetime.fromisoformat(ymd)
        if end_of_day:
            d = d.replace(hour=23, minute=59, second=59, microsecond=0)
        else:
            d = d.replace(hour=0, minute=0, second=0, microsecond=0)
        return d.strftime("%m/%d/%Y %H:%M:%S")

    def _eu(self) -> str:
        return "https://us.semsportal.com"

    def _get_translations(self):
        if self._translations is None:
            try:
                translations_path = os.path.join(os.path.dirname(__file__), "..", "translations_normalized.json")
                with open(translations_path, encoding="utf-8") as f:
                    self._translations = json.load(f)
            except Exception as e:
                print(f"Error loading translations: {e}")
                self._translations = {}
        return self._translations

    def _tx(self, key: str) -> str:
        if not key:
            return ""
        return self._get_translations().get(key, key)

    def _alarms_payload(
            self,
            start_date: str,
            end_date: str,
            status: str = "0",
            page_index: int = 1,
            page_size: int = 100,
            stationid: str = "",
            adcode: str = "",
            device_types=None,
            warninglevel: int = 7,
            fault_classification=None,
            standard_faultLevel=None,
            township: str = "",
            orgid: str = "",
            searchKey: str = ""
    ) -> dict:
        if device_types is None:
            device_types = []
        if fault_classification is None:
            fault_classification = []
        if standard_faultLevel is None:
            standard_faultLevel = []

        return {
            "adcode": adcode,
            "township": township,
            "orgid": orgid,
            "stationid": stationid,
            "warninglevel": warninglevel,
            "status": str(status),
            "starttime": self._fmt_portal_dt(start_date, end_of_day=False),
            "endtime": self._fmt_portal_dt(end_date, end_of_day=True),
            "page_size": page_size,
            "page_index": page_index,
            "searchKey": searchKey,
            "device_type": device_types,
            "fault_classification": fault_classification,
            "standard_faultLevel": standard_faultLevel
        }

    def GetWarningDetail(self, stationid: str, warningid: str, devicesn: str) -> dict:
        token = self.GetToken()
        if not token:
            return {"hasError": True, "msg": "No token"}
        url = f"{self._eu()}/api/SmartOperateMaintenance/GetPowerStationWariningDetailInfo"
        headers = {"Token": token, "Content-Type": "application/json", "Accept": "application/json"}
        body = {"stationid": stationid, "warningid": warningid, "devicesn": devicesn}
        r = requests.post(url, json=body, headers=headers, timeout=20)
        data = r.json().get("data")
        if data is None or (isinstance(data, Sized) and len(data) == 0):
            self.tokenExp = 0
            token = self.GetToken()
            headers['Token'] = token
            r = requests.post(url, json=body, headers=headers)
        if r.status_code != 200 or not r.headers.get("content-type", "").startswith("application/json"):
            return {"hasError": True, "code": r.status_code, "msg": r.text}
        return r.json()

    def GetWarningDetailTranslated(self, stationid: str, warningid: str, devicesn: str) -> dict:
        raw = self.GetWarningDetail(stationid, warningid, devicesn)
        data = (raw or {}).get("data", {}) or {}
        out = {
            "code": data.get("warning_code"),
            "time": data.get("time"),
            "info": self._tx(data.get("warning_info")),
            "reason": self._tx(data.get("reason")),
            "suggestion": self._tx(data.get("suggestion")),
        }
        return {"ok": not (raw or {}).get("hasError", False), "detail": out}

    def GetAlarmsByRange(
            self,
            start_date: str,  # 'YYYY-MM-DD'
            end_date: str = None,  # 'YYYY-MM-DD'
            status: str = "0",  # "0"=Happening, "1"=History
            stationname: str = None,  # OPTIONAL: post-filter by station name (exact, case-insensitive)
            stationid: str = "",  # OPTIONAL: restrict query to a specific station id
            device_types=None,  # [] or ["Total_DeviceType_inverter"] (kept for parity; default empty)
            page_size: int = 100,
            searchKey: str = ""
    ):
        """
		Strategy: call the alarms endpoint with *open* plant filters (no stationid/adcode),
		then post-filter by stationname if requested.

        Returns: {"total": int, "items": [normalized...]}
        """
        token = self.GetToken()
        if not token:
            return {"total": 0, "items": []}

        if device_types is None:
            device_types = []

        start_date = self.get_date(start_date)

        if not end_date:
            end_date = start_date

        end_date = self.get_date(end_date)

        url = f"{self._eu()}/api/SmartOperateMaintenance/GetPowerStationWariningInfoByMultiCondition"
        headers = {"Token": token, "Content-Type": "application/json", "Accept": "application/json"}

        # --- open query with pagination ---
        all_items = []
        page_index = 1
        total_expected = None

        while True:
            r = requests.post(
                url,
                json=self._alarms_payload(
                    start_date=start_date,
                    end_date=end_date,
                    status=status,
                    page_index=page_index,
                    page_size=page_size,
                    stationid=stationid or "",  # restrict if provided
                    adcode="",
                    device_types=device_types,
                    searchKey=searchKey or ""
                ),
                headers=headers,
                timeout=20
            )
            data = r.json().get("data")
            if data is None or (isinstance(data, Sized) and len(data) == 0):
                self.tokenExp = 0
                token = self.GetToken()
                headers['Token'] = token
                r = requests.post(
                    url,
                    json=self._alarms_payload(
                        start_date=start_date,
                        end_date=end_date,
                        status=status,
                        page_index=page_index,
                        page_size=page_size,
                        stationid=stationid or "",  # restrict if provided
                        adcode="",
                        device_types=device_types,
                        searchKey=searchKey or ""
                    ),
                    headers=headers,
                    timeout=20
                )
            if r.status_code != 200 or not r.headers.get("content-type", "").startswith("application/json"):
                break
            j = r.json() or {}
            data = j.get("data") or {}
            items = data.get("list") or []
            if total_expected is None:
                total_expected = data.get("record") or len(items)
            all_items.extend([x for x in items if isinstance(x, dict)])
            if not items or len(items) < page_size:
                break
            page_index += 1

        # --- optional post-filter by stationname (exact, case-insensitive) ---
        if stationname:
            sref = stationname.strip().lower()
            all_items = [it for it in all_items if (it.get("stationname") or "").strip().lower() == sref]

        # sort newest first (by happentime if available)
        from datetime import datetime as _dt

        def _parse_dt(s):
            try:
                return _dt.strptime(s, "%m/%d/%Y %H:%M:%S")
            except Exception:
                return None

        all_items.sort(key=lambda it: _parse_dt(it.get("happentime") or "") or _dt.min, reverse=True)

        return {"total": len(all_items), "items": all_items}


    def GetPowerAndIncomeByDay(
            self,
            powerstation_id: str,
            date: str,  # 'YYYY-MM-DD'
            count: int = 1  # number of days to retrieve (1=current day, 2=current+previous, etc.)
    ) -> dict:
        """
        Returns income and powerstation info for the given day.
        """
        details = self.GetPlantDetailByPowerstationId(powerstation_id)
        soc = self.extract_soc(details.text)
        id = soc["soc"][0]["sn"]

        token = self.GetToken()
        if not token:
            return {"hasError": True, "msg": "No token"}

        url = f"https://us.semsportal.com/api/PowerStationMonitor/GetPowerStationPowerAndIncomeByDay"

        date = self.get_date(date)

        headers = {
            'Token': token,
            'Content-Type': 'application/json',
            'Accept': '*/*'
        }

        payload = {
            "date": date + " 00:00:00",
            "powerstation_id": powerstation_id,
            "id": id,
            "count": count
        }

        response = requests.post(url, json=payload, headers=headers)

        data = response.json().get("data")
        if data is None or (isinstance(data, Sized) and len(data) == 0):
            self.tokenExp = 0
            token = self.GetToken()
            headers['Token'] = token
            response = requests.post(url, data=payload, headers=headers)

        if response.status_code == 200:
            data = response.json()
            dateGeneration = data.get("data", [])
            print("Income data retrieved successfully!")
            return {"datePowerandIncome": dateGeneration}
        else:
            print(f"Failed to retrieve income data with status code: {response.status_code}")
            return {"hasError": True, "code": response.status_code, "msg": response.text}


    def GetPowerAndIncomeByMonth(
            self,
            powerstation_id: str,
            date: str,  # 'YYYY-MM-DD'
            count: int = 1  # number of days to retrieve (1=current day, 2=current+previous, etc.)
    ) -> dict:
        """
        Returns income and powerstation info for the given Month.
        """

        details = self.GetPlantDetailByPowerstationId(powerstation_id)
        soc = self.extract_soc(details.text)
        sn = soc["soc"][0]["sn"]

        token = self.GetToken()
        if not token:
            return {"hasError": True, "msg": "No token"}

        date = self.get_date(date)

        url = f"https://us.semsportal.com/api/PowerStationMonitor/GetPowerStationPowerAndIncomeByMonth"

        headers = {
            'Token': token,
            'Content-Type': 'application/json',
            'Accept': '*/*'
        }

        payload = {
            "date": date + " 00:00:00",
            "id": powerstation_id,
            "sn": sn,
            "count": count
        }

        response = requests.post(url, json=payload, headers=headers)

        data = response.json().get("data")
        if data is None or (isinstance(data, Sized) and len(data) == 0):
            self.tokenExp = 0
            token = self.GetToken()
            headers['Token'] = token
            response = requests.post(url, data=payload, headers=headers)

        if response.status_code == 200:
            data = response.json()
            dateGeneration = data.get("data", [])
            print("Income data retrieved successfully!")
            return {"datePowerandIncome": dateGeneration}
        else:
            print(f"Failed to retrieve income data with status code: {response.status_code}")
            return {"hasError": True, "code": response.status_code, "msg": response.text}


    def GetPowerAndIncomeByYear(
            self,
            powerstation_id: str,
            date: str,  # 'YYYY-MM-DD'
            count: int = 1  # number of days to retrieve (1=current day, 2=current+previous, etc.)
    ) -> dict:
        """
        Returns income and powerstation info for the given Year.
        """

        details = self.GetPlantDetailByPowerstationId(powerstation_id)
        soc = self.extract_soc(details.text)
        sn = soc["soc"][0]["sn"]

        token = self.GetToken()
        if not token:
            return {"hasError": True, "msg": "No token"}

        date = self.get_date(date)

        url = f"https://us.semsportal.com/api/PowerStationMonitor/GetPowerStationPowerAndIncomeByYear"

        headers = {
            'Token': token,
            'Content-Type': 'application/json',
            'Accept': '*/*'
        }

        payload = {
            "date": date + " 00:00:00",
            "id": powerstation_id,
            "sn": sn,
            "count": count
        }

        response = requests.post(url, json=payload, headers=headers)

        data = response.json().get("data")
        if data is None or (isinstance(data, Sized) and len(data) == 0):
            self.tokenExp = 0
            token = self.GetToken()
            headers['Token'] = token
            response = requests.post(url, data=payload, headers=headers)

        if response.status_code == 200:
            data = response.json()
            dateGeneration = data.get("data", [])
            print("Income data retrieved successfully!")
            return {"datePowerandIncome": dateGeneration}
        else:
            print(f"Failed to retrieve income data with status code: {response.status_code}")
            return {"hasError": True, "code": response.status_code, "msg": response.text}


    def GetEvChargerChargingMode(
            self,
            powerstation_id: str,
    ) -> dict:
        """
        Returns income and powerstation info for the given Year.
        """

        if powerstation_id is None or powerstation_id.strip() == "":
            powerstation_id = os.getenv("DEFAULT_POWERSTATION_ID")
        conn = sqlite3.connect('./data/sqlite.db')
        cursor = conn.cursor()

        cursor.execute(
            "SELECT id, powerstation_id, charging_mode FROM ev_charger WHERE powerstation_id = ?",
            (powerstation_id,)
        )
        row = cursor.fetchone()
        if row:
            result = {"id": row[0], "powerstation_id": row[1], "charging_mode": row[2]}
        else:
            new_id = str(uuid.uuid4())
            cursor.execute(
                "INSERT INTO ev_charger (id, powerstation_id, charging_mode) VALUES (?, ?, ?)",
                (new_id, powerstation_id, 1)
            )
            conn.commit()
            result = {"id": new_id, "powerstation_id": powerstation_id, "charging_mode": 1}
        conn.close()
        return result

    def ChangeEvChargerChargingMode(
            self,
            powerstation_id: str,
            charge_mode: int
    ) -> dict:
        """
        Altera o charge_mode do ev_charger para o powerstation_id informado.
        """
        token = self.GetToken()
        if not token:
            return {"hasError": True, "msg": "No token"}

        conn = sqlite3.connect('./data/sqlite.db')
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE ev_charger SET charging_mode = ? WHERE powerstation_id = ?",
            (charge_mode, powerstation_id)
        )
        conn.commit()
        updated = cursor.rowcount
        conn.close()

        if updated:
            return {"hasError": False, "msg": "Charging mode atualizado com sucesso"}
        else:
            return {"hasError": True, "msg": "Powerstation não encontrado"}


    def GetPlantPowerChart(self, powerstation_id: str, date: str) -> dict:
        """
        Returns power chart data for the given day.
        """

        token = self.GetToken()
        if not token:
            return {"hasError": True, "msg": "No token"}

        date = self.get_date(date)

        url = f"https://us.semsportal.com/api/v2/Charts/GetPlantPowerChart"

        headers = {
            'Token': token,
            'Content-Type': 'application/json',
            'Accept': '*/*'
        }

        payload = {
            "id": powerstation_id,
            "full_script": False,
            "date": date
        }

        response = requests.post(url, json=payload, headers=headers)

        data = response.json().get("data")
        if data is None or (isinstance(data, Sized) and len(data) == 0):
            self.tokenExp = 0
            token = self.GetToken()
            headers['Token'] = token
            response = requests.post(url, data=payload, headers=headers)

        if response.status_code == 200:
            data = response.json()
            powerChart = data.get("data", {})
            print("Power chart data retrieved successfully!")
            return {"powerChart": powerChart}
        else:
            print(f"Failed to retrieve power chart data with status code: {response.status_code}")
            return {"hasError": True, "code": response.status_code, "msg": response.text}