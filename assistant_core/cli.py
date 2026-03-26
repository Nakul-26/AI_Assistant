import os

from .assistant import AssistantWithMemory
from .speaker import speaker


def _drain_console_input():
    if os.name != "nt":
        return

    try:
        import msvcrt
    except Exception:
        return

    try:
        while msvcrt.kbhit():
            msvcrt.getwch()
    except Exception:
        return


def run_chat_loop():
    model_name = os.getenv("OLLAMA_MODEL", "mistral")
    assistant = AssistantWithMemory(model=model_name)
    print(f"AI Assistant ({model_name}) with Python Tool + Memory (type 'exit' to quit)\n")

    while True:
        _drain_console_input()
        msg = input("You: ")
        if msg.lower() == "exit":
            break
        reply = assistant.process_request(msg)
        print("AI:", reply)
        speaker.speak(reply)
