"""
Module F: Strictly CPU-isolated STT (faster-whisper int8) and TTS (piper-tts / pyttsx3).
Zero GPU VRAM allocation. Provides both synchronous capture and non-blocking listener queues.
"""

import logging
import queue
import shutil
import subprocess
import threading
import time
from typing import Callable, Optional

import numpy as np

from ares.config import (
    AUDIO_MAX_RECORD_S,
    AUDIO_SAMPLE_RATE,
    AUDIO_SILENCE_DURATION_S,
    WHISPER_COMPUTE_TYPE,
    WHISPER_MODEL_SIZE,
)

logger = logging.getLogger("ares.voice.interface")

try:
    import sounddevice as sd

    HAS_SOUNDDEVICE = True
except Exception:
    HAS_SOUNDDEVICE = False

try:
    from faster_whisper import WhisperModel

    HAS_WHISPER = True
except Exception:
    HAS_WHISPER = False

try:
    import pyttsx3

    HAS_PYTTSX3 = True
except Exception:
    HAS_PYTTSX3 = False


class VoiceEngine:
    """
    CPU-only voice interface:
      - STT: faster-whisper ("base.en", device="cpu", compute_type="int8")
      - TTS: piper binary (if installed on PATH) or offline pyttsx3 synthesizer
    """

    def __init__(self, energy_threshold: float = 0.015) -> None:
        self.energy_threshold: float = energy_threshold
        self.whisper_model: Optional[Any] = None
        self.transcript_queue: "queue.Queue[str]" = queue.Queue()
        self._stop_event = threading.Event()
        self._listener_thread: Optional[threading.Thread] = None
        self._tts_lock = threading.Lock()

        if HAS_WHISPER:
            try:
                logger.info(
                    "Loading faster-whisper (%s, device=cpu, compute_type=%s)...",
                    WHISPER_MODEL_SIZE,
                    WHISPER_COMPUTE_TYPE,
                )
                self.whisper_model = WhisperModel(
                    WHISPER_MODEL_SIZE,
                    device="cpu",
                    compute_type=WHISPER_COMPUTE_TYPE,
                    cpu_threads=4,
                )
            except Exception as exc:
                logger.error("Failed to initialize faster-whisper on CPU: %s", exc)

    def record_utterance(self) -> np.ndarray:
        """
        Captures mono 16kHz float32 audio from the default microphone using RMS voice activity
        detection. Stops automatically after AUDIO_SILENCE_DURATION_S of silence.
        """
        if not HAS_SOUNDDEVICE:
            raise RuntimeError("sounddevice is not installed or no audio input device is present.")

        chunk_duration = 0.1  # 100ms frames
        chunk_samples = int(AUDIO_SAMPLE_RATE * chunk_duration)
        max_chunks = int(AUDIO_MAX_RECORD_S / chunk_duration)
        silence_chunks_limit = int(AUDIO_SILENCE_DURATION_S / chunk_duration)

        frames: list[np.ndarray] = []
        speaking_started = False
        silent_chunks = 0

        with sd.InputStream(
            samplerate=AUDIO_SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=chunk_samples,
        ) as stream:
            for _ in range(max_chunks):
                if self._stop_event.is_set():
                    break
                data, _ = stream.read(chunk_samples)
                mono = data[:, 0].copy()
                rms = float(np.sqrt(np.mean(np.square(mono))))

                if rms >= self.energy_threshold:
                    speaking_started = True
                    silent_chunks = 0
                    frames.append(mono)
                elif speaking_started:
                    frames.append(mono)
                    silent_chunks += 1
                    if silent_chunks >= silence_chunks_limit:
                        break

        if not frames:
            return np.zeros((0,), dtype=np.float32)
        return np.concatenate(frames, axis=0)

    def transcribe_audio(self, audio_buffer: np.ndarray) -> str:
        """Transcribes an in-memory float32 numpy audio buffer on CPU via faster-whisper."""
        if audio_buffer.size == 0 or self.whisper_model is None:
            return ""

        t0 = time.perf_counter()
        segments, _ = self.whisper_model.transcribe(
            audio_buffer,
            beam_size=1,
            language="en",
            without_timestamps=True,
            vad_filter=True,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        logger.debug("CPU STT transcription completed in %.1f ms: '%s'", elapsed_ms, text)
        return text

    def listen_once(self) -> str:
        """Captures a single spoken utterance and returns the clean transcript string."""
        audio_data = self.record_utterance()
        return self.transcribe_audio(audio_data)

    def start_background_listener(
        self, callback: Optional[Callable[[str], None]] = None
    ) -> None:
        """
        Starts a non-blocking daemon thread that continuously listens for speech
        and pushes clean transcripts into `self.transcript_queue` (and optional callback).
        """
        if self._listener_thread and self._listener_thread.is_alive():
            return

        self._stop_event.clear()

        def _worker() -> None:
            while not self._stop_event.is_set():
                try:
                    transcript = self.listen_once()
                    if transcript:
                        self.transcript_queue.put(transcript)
                        if callback:
                            callback(transcript)
                except Exception as exc:
                    logger.warning("Voice listener loop error: %s", exc)
                    time.sleep(0.5)

        self._listener_thread = threading.Thread(target=_worker, daemon=True, name="ARES-VoiceListener")
        self._listener_thread.start()

    def stop_background_listener(self) -> None:
        """Signals the background listener thread to stop cleanly."""
        self._stop_event.set()
        if self._listener_thread and self._listener_thread.is_alive():
            self._listener_thread.join(timeout=2.0)

    def get_next_transcript(self, timeout: Optional[float] = None) -> Optional[str]:
        """Retrieves the next transcribed utterance from the non-blocking queue."""
        try:
            return self.transcript_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def speak(self, text: str, block: bool = True) -> None:
        """
        Synthesizes speech strictly on CPU using piper-tts (if present) or offline pyttsx3.
        """
        if not text.strip():
            return

        def _synthesize() -> None:
            with self._tts_lock:
                piper_bin = shutil.which("piper")
                aplay_bin = shutil.which("aplay")
                if piper_bin and aplay_bin:
                    try:
                        p1 = subprocess.Popen(
                            [piper_bin, "--output-raw"],
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL,
                        )
                        p2 = subprocess.Popen(
                            [aplay_bin, "-r", "22050", "-f", "S16_LE", "-t", "raw", "-"],
                            stdin=p1.stdout,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        if p1.stdin:
                            p1.stdin.write(text.encode("utf-8"))
                            p1.stdin.close()
                        p2.wait(timeout=30)
                        return
                    except Exception as exc:
                        logger.debug("Piper TTS fallback to pyttsx3: %s", exc)

                if HAS_PYTTSX3:
                    try:
                        engine = pyttsx3.init()
                        engine.setProperty("rate", 185)
                        engine.say(text)
                        engine.runAndWait()
                        engine.stop()
                    except Exception as exc:
                        logger.warning("pyttsx3 TTS error: %s", exc)

        if block:
            _synthesize()
        else:
            threading.Thread(target=_synthesize, daemon=True, name="ARES-TTS").start()