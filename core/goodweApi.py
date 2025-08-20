import time
import os
from urllib import response
import requests
import base64
import json

class GoodweApi:
	_instance = None

	def __new__(cls, *args, **kwargs):
		if not cls._instance:
			cls._instance = super().__new__(cls)
		return cls._instance

	def __init__(self):
		self.token = None
		self.tokenExp = None

	def TokenExpired(self):
		if self.tokenExp:
			timenow = int(time.time() * 1000)
			return self.tokenExp < timenow
		return True

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

		dataToString = json.dumps(response.json()["data"])
		bytes_data = dataToString.encode('utf-8')
		encoded_bytes = base64.b64encode(bytes_data)
		encoded_string = encoded_bytes.decode('utf-8')

		self.tokenExp = response.json()["data"]["timestamp"]
		self.tokenExp += 4 * 60 * 60 * 1000

		print(response.status_code)
		print(response.json())

		if response.status_code == 200:
			print("Login successful!")
			self.token = encoded_string
			return self.token
		else:
			print(f"Login failed with status code: {response.status_code}")
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
		token = self.GetToken()
		if not token:
			return None

		url = 'https://eu.semsportal.com/api/PowerStationMonitor/QueryPowerStationMonitor'

		headers = {
		    'Token': token,
		    'Content-Type': 'application/json',
		    'Accept': '*/*'
		}

		payload = {
			"powerstation_id":"",
			"key":"",
			"orderby":"",
			"powerstation_type":"",
			"powerstation_status":"",
			"page_index":1,
			"page_size":20,
			"adcode":"",
			"org_id":"",
			"condition":""
		}

		response = requests.post(url, json=payload, headers=headers)

		if response.status_code == 200:
			plants = self.extract_powerstations(response.text)
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

	def GetSoc(self, powerstation_id):
		token = self.GetToken()
		if not token:
			return None

		url = 'https://eu.semsportal.com/api/v3/PowerStation/GetPlantDetailByPowerstationId'

		headers = {
			'Token': token,
			'Content-Type': 'application/x-www-form-urlencoded',
			'Accept': '*/*'
		}

		payload = {
			"powerStationId": powerstation_id,
		}

		response = requests.post(url, data=payload, headers=headers)

		if response.status_code == 200:
			soc = self.extract_soc(response.text)
			print("Plants retrieved successfully!")
			return soc
		else:
			print(f"Failed to retrieve plants with status code: {response.status_code}")
			return None