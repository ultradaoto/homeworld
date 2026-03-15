"""
voice_input.py — Speech-to-text for the Commander console.

Priority order:
  1. OpenAI Whisper API (if OPENAI_API_KEY is set)
  2. whisper.cpp local (if installed at ~/whisper.cpp)
  3. System speech recognition (SpeechRecognition + pyaudio)
  4. Disabled (graceful fallback)

Activated by: HOMEWORLD_VOICE=1 homeworld
"""

import os
import sys
import threading
import json
import wave
import tempfile

VOICE_ENABLED   = os.environ.get("HOMEWORLD_VOICE", "0") == "1"
MOTHERSHIP_PORT = int(os.environ.get("MOTHERSHIP_PORT", "3000"))


class VoiceInput:
    def __init__(self, api_base_url: str = None):
        self.api_base   = api_base_url or f"http://127.0.0.1:{MOTHERSHIP_PORT}"
        self.enabled    = VOICE_ENABLED
        self._recording = False
        self._thread    = None
        self._backend   = self._detect_backend()

    def _detect_backend(self) -> str:
        if not self.enabled:
            return "disabled"
        if os.environ.get("OPENAI_API_KEY"):
            return "whisper_api"
        whisper_cpp = os.path.expanduser("~/whisper.cpp/main")
        if os.path.exists(whisper_cpp):
            return "whisper_cpp"
        try:
            import speech_recognition  # noqa
            return "system"
        except ImportError:
            pass
        print("[voice] No STT backend found. "
              "Set OPENAI_API_KEY or install openai-whisper.")
        return "disabled"

    def start_listening(self, ship_id: str, ship_type: str):
        """Called when the Commander presses the mic button."""
        if self._backend == "disabled" or self._recording:
            return
        self._recording = True
        self._thread = threading.Thread(
            target=self._record_and_transcribe,
            args=(ship_id, ship_type),
            daemon=True,
        )
        self._thread.start()

    def stop_listening(self):
        self._recording = False

    def _record_and_transcribe(self, ship_id: str, ship_type: str):
        try:
            import pyaudio
            CHUNK, RATE, CHANNELS = 1024, 16000, 1
            p = pyaudio.PyAudio()
            stream = p.open(
                format=pyaudio.paInt16, channels=CHANNELS, rate=RATE,
                input=True, frames_per_buffer=CHUNK,
            )
            frames = []
            while self._recording:
                frames.append(stream.read(CHUNK, exception_on_overflow=False))
            stream.stop_stream()
            stream.close()
            p.terminate()

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                wav_path = f.name
            with wave.open(wav_path, "wb") as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(p.get_sample_size(pyaudio.paInt16))
                wf.setframerate(RATE)
                wf.writeframes(b"".join(frames))

            text = self._transcribe(wav_path)
            os.unlink(wav_path)

            if text:
                self._post_as_message(ship_id, ship_type, text)

        except Exception as e:
            print(f"[voice] Recording error: {e}")

    def _transcribe(self, wav_path: str) -> str:
        if self._backend == "whisper_api":
            return self._transcribe_whisper_api(wav_path)
        if self._backend == "whisper_cpp":
            return self._transcribe_whisper_cpp(wav_path)
        if self._backend == "system":
            return self._transcribe_system(wav_path)
        return ""

    def _transcribe_whisper_api(self, wav_path: str) -> str:
        import openai
        with open(wav_path, "rb") as f:
            result = openai.audio.transcriptions.create(model="whisper-1", file=f)
        return result.text.strip()

    def _transcribe_whisper_cpp(self, wav_path: str) -> str:
        import subprocess
        model = os.path.expanduser("~/whisper.cpp/models/ggml-base.bin")
        result = subprocess.run(
            [os.path.expanduser("~/whisper.cpp/main"),
             "-m", model, "-f", wav_path, "--output-txt"],
            capture_output=True, text=True,
        )
        return result.stdout.strip()

    def _transcribe_system(self, wav_path: str) -> str:
        import speech_recognition as sr
        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_path) as source:
            audio = recognizer.record(source)
        try:
            return recognizer.recognize_google(audio)
        except Exception:
            return ""

    def _post_as_message(self, ship_id: str, ship_type: str, text: str):
        import urllib.request
        payload = json.dumps({
            "ship_id":   ship_id,
            "ship_type": ship_type,
            "message":   text,
        }).encode()
        req = urllib.request.Request(
            f"{self.api_base}/api/console",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=30)
        except Exception as e:
            print(f"[voice] API post failed: {e}")

    def status(self) -> str:
        return (f"Voice input:  {self._backend} "
                f"({'enabled' if self.enabled else 'disabled'})")
