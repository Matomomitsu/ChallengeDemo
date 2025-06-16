import pandas as pd
import os

# Load the solar generation data
try:
    DATA = pd.read_csv("solar_generation.csv", parse_dates=["date"])
    print("✅ Solar generation data loaded successfully!")
except FileNotFoundError:
    print("❌ Solar generation data not found!")
    DATA = pd.DataFrame()

def query_generation(start_date: str, end_date: str | None = None) -> dict:
    """
    Retorna o total de kWh gerados entre start_date e end_date, inclusive.
    Se end_date for omitido, assuma um único dia.
    """
    if DATA.empty:
        return {"kwh": 0, "error": "No data available"}
    
    try:
        sd = pd.to_datetime(start_date)
        ed = pd.to_datetime(end_date) if end_date else sd
        mask = (DATA["date"] >= sd) & (DATA["date"] <= ed)
        total = DATA.loc[mask, "energy_kwh"].sum().round(2)
        return {"kwh": total, "period": f"{start_date} to {end_date or start_date}"}
    except Exception as e:
        return {"kwh": 0, "error": str(e)}

def get_solar_stats() -> dict:
    """
    Retorna estatísticas gerais sobre a geração solar
    """
    if DATA.empty:
        return {"error": "No data available"}
    
    try:
        total_generation = DATA["energy_kwh"].sum().round(2)
        avg_daily = DATA["energy_kwh"].mean().round(2)
        max_daily = DATA["energy_kwh"].max().round(2)
        min_daily = DATA["energy_kwh"].min().round(2)
        
        return {
            "total_kwh": total_generation,
            "average_daily_kwh": avg_daily,
            "max_daily_kwh": max_daily,
            "min_daily_kwh": min_daily,
            "total_days": len(DATA)
        }
    except Exception as e:
        return {"error": str(e)} 