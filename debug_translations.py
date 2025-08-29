import os
import sys
import json

# Add the parent directory to the system path to allow importing ChallengeDemo
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from ChallengeDemo.core.goodweApi import GoodweApi

# --- Debugging starts here ---
print("--- Starting Translation Debug ---")

# Instantiate GoodweApi
goodwe_api = GoodweApi()

# Test _get_translations
print("\n--- Testing _get_translations ---")
translations = goodwe_api._get_translations()
if translations:
    print(f"Translations loaded. Number of keys: {len(translations)}")
    # Print a sample translation if available
    sample_key = "E-G3-0-6-0102_warning"
    print(f"Sample translation for '{sample_key}': {translations.get(sample_key, 'Key not found')}")
else:
    print("Failed to load translations.")

# Test _tx method with a known key
print("\n--- Testing _tx method ---")
test_key = "E-G3-0-6-0102_reason"
translated_text = goodwe_api._tx(test_key)
print(f"'_tx(\"{test_key}\")' returned: {translated_text}")

# Test GetWarningDetailTranslated with the provided data
print("\n--- Testing GetWarningDetailTranslated ---")
station_id = "b8f29159-70b6-414c-8782-d485da704238"
warning_id = "6f8fc062b4137cdae2fe770c8e1a983b"
device_sn = "75000ESN333WV001"

print(f"Calling GetWarningDetailTranslated for stationid={station_id}, warningid={warning_id}, devicesn={device_sn}")
result = goodwe_api.GetWarningDetailTranslated(stationid=station_id, warningid=warning_id, devicesn=device_sn)
print("\n--- Result from GetWarningDetailTranslated ---")
print(json.dumps(result, indent=2, ensure_ascii=False))

print("\n--- Debugging Finished ---")

# Instructions for the user:
print("\nTo run this script, navigate to the 'ChallengeDemo' directory in your terminal and execute:")
print("python debug_translations.py")
print("\nMake sure your GOODWE_ACCOUNT and GOODWE_PASSWORD environment variables are set.")
