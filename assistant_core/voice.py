import os
import subprocess
import threading
import time
from pathlib import Path

from .assistant import AssistantWithMemory
from .speaker import speaker


def _normalized_text(value):
    text = str(value or "").strip().lower()
    return " ".join(text.split())


def _parse_keywords(env_name, default_value):
    raw_value = os.getenv(env_name, default_value)
    keywords = []
    for item in str(raw_value or "").split(","):
        normalized = _normalized_text(item)
        if normalized:
            keywords.append(normalized)
    return keywords


class WindowsSpeechRecognizer:
    def __init__(self):
        self._warned = False
        self._powershell_exe = os.environ.get(
            "ASSISTANT_POWERSHELL_EXE",
            r"C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe",
        )
        self._script_path = Path(__file__).resolve().parent / "powershell" / "listen_once.ps1"
        self._culture = os.getenv("ASSISTANT_STT_CULTURE", "en-US").strip() or "en-US"

    def listen_once(
        self,
        timeout_seconds=8,
        babble_timeout_seconds=3,
        end_silence_timeout_seconds=0.8,
        choices=None,
    ):
        if os.name != "nt":
            self._warn("Voice input currently supports Windows only.")
            return ""

        if not self._script_path.exists():
            self._warn(f"Missing Windows speech helper at {self._script_path}")
            return ""

        command = [
            self._powershell_exe,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self._script_path),
            "-TimeoutSeconds",
            str(timeout_seconds),
            "-BabbleTimeoutSeconds",
            str(babble_timeout_seconds),
            "-EndSilenceTimeoutSeconds",
            str(end_silence_timeout_seconds),
            "-Culture",
            self._culture,
        ]

        normalized_choices = []
        for choice in choices or []:
            normalized = str(choice or "").strip()
            if normalized:
                normalized_choices.append(normalized)

        if normalized_choices:
            command.extend(["-Choices", "||".join(normalized_choices)])

        try:
            completed = subprocess.run(command, capture_output=True, text=True, check=False)
        except Exception as exc:
            self._warn(f"Voice input helper failed to start: {exc}")
            return ""

        transcript = str(completed.stdout or "").strip()
        if completed.returncode == 0:
            return transcript

        detail = (completed.stderr or transcript or "").strip()
        if detail:
            self._warn(f"Voice input is unavailable: {detail}")
        return ""

    def _warn(self, message):
        if self._warned:
            return
        print(f"[Voice] {message}")
        self._warned = True


class VoiceConversation:
    def __init__(self, assistant):
        self.assistant = assistant
        self.recognizer = WindowsSpeechRecognizer()
        self._should_exit = False
        self.interrupt_keywords = _parse_keywords(
            "ASSISTANT_INTERRUPT_KEYWORDS",
            "stop assistant, stop speaking, be quiet",
        )
        self.exit_keywords = _parse_keywords(
            "ASSISTANT_EXIT_KEYWORDS",
            "exit voice mode, quit voice mode, goodbye assistant",
        )
        self._text_fallback_enabled = os.getenv("ASSISTANT_TEXT_FALLBACK", "1").strip().lower() not in {
            "0",
            "false",
            "off",
            "no",
        }

    def run(self):
        model_name = getattr(self.assistant, "model", os.getenv("OLLAMA_MODEL", "mistral"))
        print(f"AI Assistant ({model_name}) voice mode")
        print("Say something after the listening prompt. Use your interrupt keyword while audio is playing.")
        print(f"Interrupt keywords: {', '.join(self.interrupt_keywords)}")
        print(f"Exit keywords: {', '.join(self.exit_keywords)}\n")

        while True:
            if self._should_exit:
                print("Voice mode stopped.")
                break

            print("Listening...")
            transcript = self.recognizer.listen_once(
                timeout_seconds=float(os.getenv("ASSISTANT_LISTEN_TIMEOUT", "12")),
                babble_timeout_seconds=float(os.getenv("ASSISTANT_BABBLE_TIMEOUT", "4")),
                end_silence_timeout_seconds=float(os.getenv("ASSISTANT_END_SILENCE_TIMEOUT", "0.8")),
            )
            transcript = str(transcript or "").strip()
            if not transcript:
                transcript = self._read_text_fallback("You (text fallback): ")
            if not transcript:
                continue

            print("You (voice):", transcript)
            normalized = _normalized_text(transcript)

            if any(keyword in normalized for keyword in self.exit_keywords):
                speaker.stop()
                print("Voice mode stopped.")
                break

            reply = self.assistant.process_request(transcript)
            print("AI:", reply)
            self._speak_with_interrupts(reply)

    def _speak_with_interrupts(self, reply):
        if not speaker.speak_async(reply):
            speaker.speak(reply)
            return

        stop_event = threading.Event()
        interrupted = threading.Event()
        monitor = threading.Thread(
            target=self._interrupt_monitor,
            args=(stop_event, interrupted),
            daemon=True,
        )
        monitor.start()

        try:
            while speaker.is_speaking():
                time.sleep(0.2)
        finally:
            stop_event.set()
            monitor.join(timeout=1.5)

        if interrupted.is_set():
            print("[Voice] Playback interrupted.")

    def _interrupt_monitor(self, stop_event, interrupted):
        while not stop_event.is_set():
            if not speaker.is_speaking():
                return

            transcript = self.recognizer.listen_once(
                timeout_seconds=float(os.getenv("ASSISTANT_INTERRUPT_LISTEN_TIMEOUT", "2.5")),
                babble_timeout_seconds=float(os.getenv("ASSISTANT_INTERRUPT_BABBLE_TIMEOUT", "1.5")),
                end_silence_timeout_seconds=float(os.getenv("ASSISTANT_INTERRUPT_END_SILENCE_TIMEOUT", "0.5")),
                choices=self.interrupt_keywords + self.exit_keywords,
            )
            normalized = _normalized_text(transcript)
            if not normalized:
                continue

            if any(keyword in normalized for keyword in self.interrupt_keywords):
                interrupted.set()
                speaker.stop()
                return

            if any(keyword in normalized for keyword in self.exit_keywords):
                interrupted.set()
                self._should_exit = True
                speaker.stop()
                return

    def _read_text_fallback(self, prompt):
        if not self._text_fallback_enabled:
            return ""

        try:
            return str(input(prompt) or "").strip()
        except EOFError:
            return ""


def run_voice_chat_loop():
    model_name = os.getenv("OLLAMA_MODEL", "mistral")
    assistant = AssistantWithMemory(model=model_name)
    VoiceConversation(assistant).run()
