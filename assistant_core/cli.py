import os

from .assistant import AssistantWithMemory


def run_chat_loop():
    model_name = os.getenv("OLLAMA_MODEL", "mistral")
    assistant = AssistantWithMemory(model=model_name)
    print(f"AI Assistant ({model_name}) with Python Tool + Memory (type 'exit' to quit)\n")

    while True:
        msg = input("You: ")
        if msg.lower() == "exit":
            break
        reply = assistant.process_request(msg)
        print("AI:", reply)
