import argparse
import os

from assistant_core.cli import run_chat_loop
from assistant_core.voice import run_voice_chat_loop


def _parse_args():
    parser = argparse.ArgumentParser(description="Local AI assistant")
    parser.add_argument(
        "--mode",
        choices=["text", "voice"],
        default=os.getenv("ASSISTANT_MODE", "text").strip().lower() or "text",
        help="Run the text terminal chat loop or the Windows voice conversation loop.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if args.mode == "voice":
        run_voice_chat_loop()
    else:
        run_chat_loop()
