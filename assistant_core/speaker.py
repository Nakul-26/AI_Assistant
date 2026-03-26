import os


class Speaker:
    def __init__(self):
        self._engine = None
        self._available = None
        self._warned = False

    def _is_enabled(self):
        value = os.getenv("ASSISTANT_TTS", "1").strip().lower()
        return value not in {"0", "false", "off", "no"}

    def _load_engine(self):
        if self._available is not None:
            return self._engine

        if not self._is_enabled():
            self._available = False
            return None

        try:
            import pyttsx3

            self._engine = pyttsx3.init()
            self._available = True
            return self._engine
        except Exception:
            self._available = False
            return None

    def speak(self, text):
        message = str(text or "").strip()
        if not message:
            return False

        engine = self._load_engine()
        if engine is None:
            if self._is_enabled() and not self._warned:
                print("[TTS] Disabled. Install 'pyttsx3' to enable spoken replies.")
                self._warned = True
            return False

        try:
            engine.say(message)
            engine.runAndWait()
            return True
        except Exception as exc:
            if not self._warned:
                print(f"[TTS] Speak failed: {exc}")
                self._warned = True
            return False


speaker = Speaker()
