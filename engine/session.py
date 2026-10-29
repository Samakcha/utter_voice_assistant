"""Session manager coordinating audio capture, VAD state, and session lifecycles."""

from datetime import datetime
import logging
from pathlib import Path
import threading
import time
from typing import Callable, List, Optional
import wave
import numpy as np

from .audio_capture import AudioCapture
from .config import AppConfig
from .models import EndReason, SessionMode, SessionResult
from .vad import SileroVAD

logger = logging.getLogger(__name__)


class SessionManager:
    """
    Coordinates audio capture, VAD processing, silence auto-stops,
    and recording retention. Pure engine component with NO UI code.
    """

    def __init__(
        self,
        config: AppConfig,
        vad: Optional[SileroVAD] = None,
        audio_capture: Optional[AudioCapture] = None,
        on_session_start: Optional[Callable[[SessionMode], None]] = None,
        on_session_end: Optional[Callable[[SessionResult], None]] = None,
    ):
        self._config = config
        self._vad = vad or SileroVAD(
            sample_rate=config.audio.sample_rate,
            threshold=config.vad.speech_threshold,
        )
        self._audio = audio_capture or AudioCapture(
            sample_rate=config.audio.sample_rate,
            chunk_size=config.audio.chunk_size,
            channels=config.audio.channels,
        )

        self.on_session_start = on_session_start
        self.on_session_end = on_session_end

        # Session state
        self._lock = threading.RLock()
        self._current_mode: Optional[SessionMode] = None
        self._is_active: bool = False
        self._speech_detected: bool = False
        self._consecutive_speech_chunks: int = 0
        self._session_start_time: float = 0.0
        self._last_speech_time: Optional[float] = None
        self._audio_chunks: List[np.ndarray] = []

        # Ensure recordings directory exists
        self._config.storage.recordings_dir.mkdir(parents=True, exist_ok=True)

    @property
    def is_listening(self) -> bool:
        """True if a session is currently active and capturing audio."""
        with self._lock:
            return self._is_active

    @property
    def current_mode(self) -> Optional[SessionMode]:
        """Currently active session mode, or None if idle."""
        with self._lock:
            return self._current_mode

    def start_session(self, mode: SessionMode) -> bool:
        """
        Start a listening session in the given mode.

        If a dictation session is already running and dictation mode is triggered again,
        it stops the session (hotkey toggle behavior).

        Returns:
            True if a new session started (or dictation stopped via toggle), False if ignored.
        """
        with self._lock:
            if self._is_active:
                if self._current_mode == SessionMode.DICTATION and mode == SessionMode.DICTATION:
                    logger.info("Dictation hotkey pressed again; ending session.")
                    self.stop_session(EndReason.HOTKEY)
                    return True
                logger.info("Session already active in mode %s; ignoring start request for %s.", self._current_mode, mode)
                return False

            logger.info("Starting new voice session: mode=%s", mode.value)
            self._current_mode = mode
            self._is_active = True
            self._speech_detected = False
            self._consecutive_speech_chunks = 0
            self._audio_chunks = []
            self._session_start_time = time.monotonic()
            self._last_speech_time = None

            # Reset VAD internal states
            self._vad.reset()

            try:
                self._audio.start(self._on_audio_chunk)
            except Exception as e:
                logger.error("Failed to start audio capture: %s", e)
                self._is_active = False
                self._current_mode = None
                return False

            if self.on_session_start is not None:
                try:
                    self.on_session_start(mode)
                except Exception as e:
                    logger.error("Error in on_session_start callback: %s", e)

            return True

    def cancel_session(self, reason: EndReason = EndReason.ESC) -> Optional[SessionResult]:
        """Cancel the current session immediately."""
        with self._lock:
            if not self._is_active:
                return None
            return self.stop_session(reason)

    def stop_session(self, reason: EndReason) -> Optional[SessionResult]:
        """Stop the active session and process the recorded audio."""
        with self._lock:
            if not self._is_active:
                return None

            self._is_active = False
            mode = self._current_mode or SessionMode.COMMAND
            self._current_mode = None

            # Immediately stop the microphone stream
            self._audio.stop()

            now = time.monotonic()
            duration_sec = max(0.0, now - self._session_start_time)
            speech_detected = self._speech_detected
            chunks_to_save = list(self._audio_chunks)
            self._audio_chunks = []

            # Save recorded audio to WAV file
            audio_path = self._save_wav_file(chunks_to_save, mode, reason)

            # Prune old recordings to stay within limit
            self._prune_recordings()

            total_samples = sum(len(c) for c in chunks_to_save)
            result = SessionResult(
                mode=mode,
                end_reason=reason,
                duration_sec=duration_sec,
                audio_path=audio_path,
                speech_detected=speech_detected,
                sample_rate=self._config.audio.sample_rate,
                total_samples=total_samples,
            )

            logger.info(
                "[Session Ended] Mode: %s | Reason: %s | Duration: %.2fs | Speech: %s | Audio: %s",
                result.mode.value,
                result.end_reason.value,
                result.duration_sec,
                result.speech_detected,
                result.audio_path.name if result.audio_path else "None",
            )

            if self.on_session_end is not None:
                try:
                    self.on_session_end(result)
                except Exception as e:
                    logger.error("Error in on_session_end callback: %s", e)

            return result

    def _on_audio_chunk(self, chunk: np.ndarray) -> None:
        """Callback invoked from audio capture thread for each frame."""
        session_to_stop: Optional[EndReason] = None

        with self._lock:
            if not self._is_active:
                return

            self._audio_chunks.append(chunk)
            now = time.monotonic()

            try:
                is_speech, prob = self._vad.is_speech(chunk)
            except Exception as e:
                logger.error("VAD evaluation error: %s", e)
                is_speech, prob = False, 0.0

            if self._current_mode == SessionMode.COMMAND:
                if not self._speech_detected:
                    if is_speech:
                        self._consecutive_speech_chunks += 1
                        if self._consecutive_speech_chunks >= self._config.vad.min_speech_chunks:
                            self._speech_detected = True
                            self._last_speech_time = now
                            logger.info("Command mode: speech onset detected (prob=%.3f).", prob)
                    else:
                        self._consecutive_speech_chunks = 0
                        # Check initial silence timeout (accidental trigger safety net)
                        elapsed = now - self._session_start_time
                        if elapsed >= self._config.timeouts.command_no_speech_sec:
                            logger.info("Command mode: no speech within %.1fs; canceling quietly.", elapsed)
                            session_to_stop = EndReason.NO_SPEECH
                else:
                    # Speech already detected: track trailing silence
                    if is_speech:
                        self._last_speech_time = now
                    else:
                        if self._last_speech_time is not None:
                            silence_duration = now - self._last_speech_time
                            if silence_duration >= self._config.timeouts.command_silence_sec:
                                logger.info("Command mode: trailing silence of %.2fs; auto-stopping.", silence_duration)
                                session_to_stop = EndReason.SILENCE

            elif self._current_mode == SessionMode.DICTATION:
                # Dictation mode: continuous recording through pauses under dictation_silence_sec
                if is_speech:
                    if not self._speech_detected:
                        self._consecutive_speech_chunks += 1
                        if self._consecutive_speech_chunks >= self._config.vad.min_speech_chunks:
                            self._speech_detected = True
                            self._last_speech_time = now
                            logger.info("Dictation mode: speech onset detected (prob=%.3f).", prob)
                    else:
                        self._last_speech_time = now
                else:
                    self._consecutive_speech_chunks = 0
                    reference_time = self._last_speech_time if self._last_speech_time is not None else self._session_start_time
                    silence_duration = now - reference_time
                    if silence_duration >= self._config.timeouts.dictation_silence_sec:
                        logger.info("Dictation mode: silence threshold of %.2fs reached; auto-stopping.", silence_duration)
                        session_to_stop = EndReason.SILENCE

        # If a stop condition was met, stop the session outside the lock
        if session_to_stop is not None:
            self.stop_session(session_to_stop)

    def _save_wav_file(
        self,
        chunks: List[np.ndarray],
        mode: SessionMode,
        reason: EndReason,
    ) -> Optional[Path]:
        """Save captured chunks as a temporary 16-bit mono WAV file."""
        if not chunks:
            return None

        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"session_{timestamp}_{mode.value}_{reason.value}.wav"
            file_path = self._config.storage.recordings_dir / filename

            concatenated = np.concatenate(chunks)
            # Clip and convert float32 in [-1.0, 1.0] to int16 PCM
            pcm_data = (np.clip(concatenated, -1.0, 1.0) * 32767.0).astype(np.int16)

            with wave.open(str(file_path), "wb") as wf:
                wf.setnchannels(self._config.audio.channels)
                wf.setsampwidth(2)  # 16 bits = 2 bytes
                wf.setframerate(self._config.audio.sample_rate)
                wf.writeframes(pcm_data.tobytes())

            return file_path
        except Exception as e:
            logger.error("Failed to write WAV file: %s", e)
            return None

    def _prune_recordings(self) -> None:
        """Keep only the most recent max_recordings_kept WAV files."""
        try:
            directory = self._config.storage.recordings_dir
            if not directory.is_dir():
                return

            wav_files = sorted(
                directory.glob("*.wav"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )

            max_kept = self._config.storage.max_recordings_kept
            if len(wav_files) > max_kept:
                for file_to_delete in wav_files[max_kept:]:
                    try:
                        file_to_delete.unlink()
                        logger.debug("Pruned old recording: %s", file_to_delete.name)
                    except OSError as e:
                        logger.warning("Failed to delete old recording %s: %s", file_to_delete, e)
        except Exception as e:
            logger.warning("Error pruning old recordings: %s", e)
