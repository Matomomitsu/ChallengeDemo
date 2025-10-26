import os
from collections import defaultdict
from datetime import datetime, date, time, timedelta
from dotenv import load_dotenv
import pymongo
import datetime as _dt
from typing import Dict, Any, List, Tuple, Set
from core.goodweApi import GoodweApi

if __name__ == "__main__":
    load_dotenv(".env")

    host = os.getenv("MONGO_URI")
    client = pymongo.MongoClient(host)
    db = client["fiap_challenge"]
    collection = db["hourly_data"]

    api = GoodweApi()

    import time as _time

    while True:
        try:
            date_str = str(_dt.date.today())
            print(f"Inserted {inserted} hourly documents for last 5 days ending {date_str}")
        except Exception as e:
            print(f"Error during fetch: {e}")

        now = datetime.now()
        next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        sleep_seconds = (next_hour - now).total_seconds()
        print("Sleeping for %.2f seconds until %s" % (sleep_seconds, next_hour.isoformat()))
        _time.sleep(max(0, sleep_seconds))
