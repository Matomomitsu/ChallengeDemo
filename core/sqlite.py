import os
import sqlite3

def start_sqlite():
    conn = sqlite3.connect('./data/sqlite.db')
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS ev_charger (
        id TEXT PRIMARY KEY,
        powerstation_id TEXT NOT NULL,
        charging_mode INTEGER
    )
    ''')
    conn.commit()
    conn.close()