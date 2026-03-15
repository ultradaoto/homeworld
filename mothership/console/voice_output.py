"""
voice_output.py — Text-to-speech for Mothership responses.

The Mothership speaks back to the Commander.

Priority:
  1. ElevenLabs API (if ELEVENLABS_API_KEY set) — cinematic quality
  2. macOS `say` command (zero config on Mac)
  3. pyttsx3 (cross-platform, functional)
  4. Disabled
"""

import os
import subprocess
import threading

VOICE_ENABLED = os.environ.get("HOMEWORLD_VOICE", "0") == "1"


class VoiceOutput:
    def __init__(self):
        self.enabled      = VOICE_ENABLED
        self._backend     = self._detect_backend()
        self._el_voice_id = os.environ.get(
            "ELEVENLABS_VOICE_ID", "N2lVS1w4EtoT3dr4eOWO"
        )

    def _detect_backend(self) -> str:
        if not self.enabled:
            return "disabled"
        if os.environ.get("ELEVENLABS_API_KEY"):
            return "elevenlabs"
        # macOS say
        try:
            if subprocess.run(
                ["which", "say"], capture_output=True
            ).returncode == 0:
                return "macos_say"
        except FileNotFoundError:
            pass
        # Windows SAPI via pyttsx3
        try:
            import pyttsx3  # noqa
            return "pyttsx3"
        except ImportError:
            pass
        return "disabled"

    def speak(self, text: str):
        """Non-blocking. Fires TTS in a background thread."""
        if self._backend == "disabled" or not text:
            return
        threading.Thread(
            target=self._speak_sync, args=(text,), daemon=True
        ).start()

    def _speak_sync(self, text: str):
        try:
            if self._backend == "elevenlabs":
                self._speak_elevenlabs(text)
            elif self._backend == "macos_say":
                subprocess.run(["say", "-v", "Samantha", text], check=False)
            elif self._backend == "pyttsx3":
                import pyttsx3
                engine = pyttsx3.init()
                engine.say(text)
                engine.runAndWait()
        except Exception as e:
            print(f"[voice out] TTS error: {e}")

    def _speak_elevenlabs(self, text: str):
        import urllib.request
        import tempfile
        api_key = os.environ["ELEVENLABS_API_KEY"]
        # Escape quotes in text for inline JSON
        safe_text = text.replace('"', '\\"').replace("\n", " ")
        payload = (
            f'{{"text":"{safe_text}","model_id":"eleven_monolingual_v1",'
            f'"voice_settings":{{"stability":0.5,"similarity_boost":0.75}}}}'
        ).encode()
        req = urllib.request.Request(
            f"https://api.elevenlabs.io/v1/text-to-speech/{self._el_voice_id}",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "xi-api-key":   api_key,
                "Accept":       "audio/mpeg",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            audio = resp.read()
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio)
            mp3_path = f.name
        # macOS/Linux player
        for player in ["afplay", "mpg123", "ffplay"]:
            try:
                subprocess.run([player, mp3_path], check=False, capture_output=True)
                break
            except FileNotFoundError:
                continue
        os.unlink(mp3_path)

    def status(self) -> str:
        return (f"Voice output: {self._backend} "
                f"({'enabled' if self.enabled else 'disabled'})")
