import asyncio
from core.gemini import call_geminiapi, initialize_chat
from dotenv import load_dotenv

def chat_interface():
    """CLI chat interface for testing"""
    load_dotenv() 
    print("⚡ BotSolar está pronto. Pergunte sobre geração solar ou gerenciamento de bateria.")
    print("Digite 'exit', 'quit' ou 'bye' para sair.\n")
    
    if not initialize_chat():
        print("❌ Could not initialize chat. Exiting.")
        return
    
    while True:
        try:
            user_input = input("📨 Você: ").strip()
            if not user_input:
                continue
            if user_input.lower() in {"exit", "quit", "bye"}:
                print("🤖 BotSolar: Adeus!")
                break
            
            response = asyncio.run(call_geminiapi(user_input))
            print(f"🤖 BotSolar: {response}\n")
            
        except KeyboardInterrupt:
            print("\n🤖 BotSolar: Adeus!")
            break
        except Exception as e:
            print(f"❌ Erro: {e}\n")

if __name__ == "__main__":
    chat_interface() 