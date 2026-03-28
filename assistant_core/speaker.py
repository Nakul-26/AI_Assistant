import os
import subprocess
import threading
from pathlib import Path


class Speaker:
    def __init__(self):
        self._engine = None
        self._available = None
        self._warned = False
        self._init_error = None
        self._speech_process = None
        self._speech_thread = None
        self._lock = threading.RLock()

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
            self._init_error = None
            self._available = True
            return self._engine
        except Exception as exc:
            self._init_error = exc
            self._available = False
            return None

    def _warn_tts_unavailable(self, detail=None):
        if not self._is_enabled() or self._warned:
            return

        if detail:
            print(f"[TTS] Disabled. {detail}")
        elif self._init_error is not None:
            print(f"[TTS] Disabled. Engine initialization failed: {self._init_error}")
        else:
            print("[TTS] Disabled. Install 'pyttsx3' to enable spoken replies.")

        self._warned = True

    def _powershell_exe(self):
        return os.environ.get(
            "ASSISTANT_POWERSHELL_EXE",
            r"C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe",
        )

    def _powershell_speak_script(self):
        return Path(__file__).resolve().parent / "powershell" / "speak.ps1"

    def _build_powershell_speak_command(self, text):
        script_path = self._powershell_speak_script()
        command = [
            self._powershell_exe(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
            "-Text",
            str(text),
        ]

        voice_name = os.getenv("ASSISTANT_TTS_VOICE", "").strip()
        if voice_name:
            command.extend(["-Voice", voice_name])

        voice_rate = os.getenv("ASSISTANT_TTS_RATE", "").strip()
        if voice_rate:
            command.extend(["-Rate", voice_rate])

        return command

    def _speak_with_powershell(self, text, wait=True):
        if os.name != "nt":
            return False

        script_path = self._powershell_speak_script()
        if not script_path.exists():
            self._warn_tts_unavailable(f"Missing Windows speech helper at {script_path}")
            return False

        command = self._build_powershell_speak_command(text)

        try:
            if wait:
                completed = subprocess.run(command, capture_output=True, text=True, check=False)
                if completed.returncode == 0:
                    return True

                detail = (completed.stderr or completed.stdout or "").strip() or "Windows speech process failed."
                self._warn_tts_unavailable(f"Windows speech process failed: {detail}")
                return False

            with self._lock:
                self.stop()
                self._speech_process = subprocess.Popen(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                self._speech_thread = threading.Thread(target=self._watch_process, daemon=True)
                self._speech_thread.start()
                return True
        except Exception as exc:
            self._warn_tts_unavailable(f"Windows speech process failed: {exc}")
            return False

    def _watch_process(self):
        process = None

        with self._lock:
            process = self._speech_process

        if process is None:
            return

        stderr_text = ""
        try:
            _, stderr_text = process.communicate()
        except Exception:
            pass
        finally:
            should_warn = False
            with self._lock:
                if self._speech_process is process:
                    self._speech_process = None
                    self._speech_thread = None
                    should_warn = True

        if should_warn and process.returncode not in (0, None):
            detail = str(stderr_text or "").strip() or f"Windows speech process exited with code {process.returncode}."
            self._warn_tts_unavailable(f"Windows speech process failed: {detail}")

    def speak(self, text):
        message = str(text or "").strip()
        if not message:
            return False

        engine = self._load_engine()
        if engine is not None:
            try:
                engine.say(message)
                engine.runAndWait()
                return True
            except Exception as exc:
                if not self._warned:
                    print(f"[TTS] Speak failed: {exc}")
                    self._warned = True
                return False

        return self._speak_with_powershell(message, wait=True)

    def speak_async(self, text):
        message = str(text or "").strip()
        if not message or not self._is_enabled():
            return False

        return self._speak_with_powershell(message, wait=False)

    def stop(self):
        with self._lock:
            process = self._speech_process
            self._speech_process = None
            self._speech_thread = None

        if process is not None:
            try:
                process.terminate()
                process.wait(timeout=2)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass

        engine = self._engine
        if engine is not None:
            try:
                engine.stop()
            except Exception:
                pass

    def is_speaking(self):
        with self._lock:
            process = self._speech_process
            thread = self._speech_thread

        if process is not None and process.poll() is None:
            return True

        if thread is not None and thread.is_alive():
            return True

        return False


speaker = Speaker()
