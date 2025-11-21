import asyncio
import argparse
import json
import os
from core.gemini import call_geminiapi, initialize_chat
from core.sems_history import fetch_and_parse_7d
from dotenv import load_dotenv

SHOW_FUNCTION_PREVIEW = os.getenv("BOT_SOLAR_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}


def chat_interface():
    """CLI chat interface for testing"""
    load_dotenv() 
    print("⚡ BotSolar is ready. Ask about solar generation or battery management.")
    print("Type 'exit', 'quit', or 'bye' to leave.\n")
    
    if not initialize_chat():
        print("❌ Could not initialize chat. Exiting.")
        return
    
    while True:
        try:
            user_input = input("📨 You: ").strip()
            if not user_input:
                continue
            if user_input.lower() in {"exit", "quit", "bye"}:
                print("🤖 BotSolar: Goodbye!")
                break
            
            result = asyncio.run(call_geminiapi(user_input))
            if isinstance(result, dict):
                response_text = result.get("response", "")
                print(f"🤖 BotSolar: {response_text}\n")
                functions_preview = result.get("functions_preview") or []
                if SHOW_FUNCTION_PREVIEW and functions_preview:
                    print("🔍 Functions executed:")
                    print(json.dumps(functions_preview, ensure_ascii=False, indent=2))
                    print()
                used_station = result.get("used_powerstation_id")
                if result.get("fallback_to_default") and used_station:
                    print(
                        f"⚠️  No data for powerstation_id {used_station}. Fell back to the default plant.\n"
                    )
            else:
                print(f"🤖 BotSolar: {result}\n")
            
        except KeyboardInterrupt:
            print("\n🤖 BotSolar: Goodbye!")
            break
        except Exception as e:
            print(f"❌ Error: {e}\n")

def main():
    parser = argparse.ArgumentParser(description="BotSolar CLI")
    sub = parser.add_subparsers(dest="command")

    # Default chat
    sub.add_parser("chat", help="Interactive chat interface")

    # 7-day history fetch/parse (standalone, no Gemini involvement)
    p_hist = sub.add_parser("history7d", help="Fetch and parse last 7 days of GoodWe history")
    p_hist.add_argument("--station-id", dest="station_id", default=None, help="Optional powerstation id (defaults from env or first plant)")
    p_hist.add_argument("--inverter-sn", dest="inverter_sn", default=None, help="Optional inverter serial number (defaults to Bauer SN if missing)")
    p_hist.add_argument("--no-save", action="store_true", help="Do not save output files to data/")

    args = parser.parse_args()

    if args.command == "history7d":
        load_dotenv()
        res = fetch_and_parse_7d(station_id=args.station_id, inverter_sn=args.inverter_sn, save_files=not args.no_save)
        # Print only lightweight confirmation to avoid large dumps
        out = {
            "saved": bool(res.get("files")),
            "files": res.get("files"),
            "points": res.get("parsed_generic", {}).get("metadata", {}).get("total_points"),
            "avg_battery_soc": res.get("parsed_focus", {}).get("summary", {}).get("avg_battery_soc"),
            "max_solar_generation": res.get("parsed_focus", {}).get("summary", {}).get("max_solar_generation"),
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    # default to chat
    chat_interface()


if __name__ == "__main__":
    main()
