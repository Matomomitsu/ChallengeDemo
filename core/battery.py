def get_batery_usage():
    return {"battery_level": 80,"battery_usage": 500,"is_charging":False}

def check_battery_energy_flow():
    return {"energy_destinations": ["Home", "Car charger"]}

def add_destination_to_battery_flow(destinations):
    return {"message": f"Started sending energy to: {', '.join(destinations)}"}

def remove_destination_from_battery_flow(destinations):
    return {"message": f"Stopped sending energy to: {', '.join(destinations)}"} 