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
                logger.error("Failed to start audio capture: %s", e, exc_info=True)
                self._is_active = False
                self._current_mode = None
                return False

            if self.on_session_start is not None:
                try:
                    self.on_session_start(mode)
                except Exception as e:
                    logger.error("Error in on_session_start callback: %s", e, exc_info=True)

            return True

    def cancel_session(self, reason: EndReason = EndReason.ESC) -> Optional[SessionResult]:
        """Cancel the current session immediately."""
        return self.stop_session(reason)

    def stop_session(self, reason: EndReason) -> Optional[SessionResult]:
        """
        Stop the active session and process the recorded audio.
        Thread-safe: can be called from hotkeys, UI thread, or internal worker.
        """
        with self._lock:
            if not self._is_active:
                return None

            # Mark inactive immediately to stop receiving further chunks
            self._is_active = False
            mode = self._current_mode or SessionMode.COMMAND
            self._current_mode = None
            chunks_to_save = list(self._audio_chunks)
            self._audio_chunks = []
            speech_detected = self._speech_detected
            start_time = self._session_start_time

        # Finalize outside the lock
        return self._finalize_stop(reason, mode, chunks_to_save, speech_detected, start_time)

    def _finalize_stop(
        self,
        reason: EndReason,
        mode: SessionMode,
        chunks_to_save: List[np.ndarray],
        speech_detected: bool,
        start_time: float,
    ) -> Optional[SessionResult]:
        """
        Stop the audio hardware stream, write WAV, prune old files,
        log session details, and invoke UI callback.
        Guaranteed never to deadlock and never to swallow exceptions.
        """
        result: Optional[SessionResult] = None
        try:
            # 1. Stop audio capture hardware
            try:
                self._audio.stop()
            except Exception as e:
                logger.error("Error stopping audio capture stream: %s", e, exc_info=True)

            # 2. Timing and sample metrics
            now = time.monotonic()
            duration_sec = max(0.0, now - start_time)
            total_samples = sum(len(c) for c in chunks_to_save)

            # 3. Save recorded audio to WAV file
            audio_path: Optional[Path] = None
            try:
                audio_path = self._save_wav_file(chunks_to_save, mode, reason)
            except Exception as e:
                logger.error("Error saving WAV file: %s", e, exc_info=True)

            # 4. Prune old recordings to keep within quota
            try:
                self._prune_recordings()
            except Exception as e:
                logger.error("Error pruning recordings: %s", e, exc_info=True)

            # 5. Build SessionResult
            result = SessionResult(
                mode=mode,
                end_reason=reason,
                duration_sec=duration_sec,
                audio_path=audio_path,
                speech_detected=speech_detected,
                sample_rate=self._config.audio.sample_rate,
                total_samples=total_samples,
            )

            # 6. Log one structured line with full session details
            if result.audio_path and result.audio_path.exists():
                audio_info = f"Audio: {result.audio_path}"
            else:
                reason_str = "no audio captured" if not chunks_to_save else "save failed"
                audio_info = f"Audio: not saved ({reason_str})"

            logger.info(
                "[Session Ended] Mode: %s | Reason: %s | Duration: %.2fs | Speech: %s | %s",
                result.mode.value,
                result.end_reason.value,
                result.duration_sec,
                "Yes" if result.speech_detected else "No",
                audio_info,
            )
        except Exception as e:
            logger.error("Unexpected error during session finalization: %s", e, exc_info=True)
            if result is None:
                result = SessionResult(
                    mode=mode,
                    end_reason=reason,
                    duration_sec=max(0.0, time.monotonic() - start_time),
                    audio_path=None,
                    speech_detected=speech_detected,
                    sample_rate=self._config.audio.sample_rate,
                    total_samples=0,
                )
        finally:
            # 7. Always invoke UI callback, even on error, to restore idle state
            if self.on_session_end is not None and result is not None:
                try:
                    self.on_session_end(result)
                except Exception as e:
                    logger.error("Error in on_session_end callback: %s", e, exc_info=True)

        return result

    def _on_audio_chunk(self, chunk: np.ndarray) -> None:
        """
        Callback invoked from audio capture thread for each frame.
        Analyzes VAD and detects auto-stop conditions.
        NEVER calls stream.stop() directly on this thread to avoid PortAudio deadlocks.
        """
        session_to_stop: Optional[EndReason] = None
        chunks_to_save: List[np.ndarray] = []
        mode = SessionMode.COMMAND
        speech_detected = False
        start_time = 0.0

        with self._lock:
            if not self._is_active:
                return

            self._audio_chunks.append(chunk)
            now = time.monotonic()

            try:
                is_speech, prob = self._vad.is_speech(chunk)
            except Exception as e:
                logger.error("VAD evaluation error: %s", e, exc_info=True)
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

            # If auto-stopping, snapshot data and transition state immediately inside lock
            if session_to_stop is not None:
                self._is_active = False
                mode = self._current_mode or SessionMode.COMMAND
                self._current_mode = None
                chunks_to_save = list(self._audio_chunks)
                self._audio_chunks = []
                speech_detected = self._speech_detected
                start_time = self._session_start_time

        # Dispatch finalization to a separate thread to prevent PortAudio callback deadlock
        if session_to_stop is not None:
            threading.Thread(
                target=self._finalize_stop,
                args=(session_to_stop, mode, chunks_to_save, speech_detected, start_time),
                name="session-auto-stop",
                daemon=True,
            ).start()

    def _save_wav_file(
        self,
        chunks: List[np.ndarray],
        mode: SessionMode,
        reason: EndReason,
    ) -> Optional[Path]:
        """Save captured chunks as a temporary 16-bit mono WAV file."""
        if not chunks:
            logger.warning("No audio chunks to save for session.")
            return None

        try:
            self._config.storage.recordings_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"session_{timestamp}_{mode.value}_{reason.value}.wav"
            file_path = self._config.storage.recordings_dir / filename

            concatenated = np.concatenate(chunks)
            if concatenated.size == 0:
                logger.warning("Concatenated audio array is empty; not saving WAV.")
                return None

            # Clip and convert float32 in [-1.0, 1.0] to int16 PCM
            pcm_data = (np.clip(concatenated, -1.0, 1.0) * 32767.0).astype(np.int16)

            with wave.open(str(file_path), "wb") as wf:
                wf.setnchannels(self._config.audio.channels)
                wf.setsampwidth(2)  # 16 bits = 2 bytes
                wf.setframerate(self._config.audio.sample_rate)
                wf.writeframes(pcm_data.tobytes())

            logger.info(
                "Saved session audio to: %s (%.2f KB, %d frames)",
                file_path,
                file_path.stat().st_size / 1024.0,
                len(pcm_data),
            )
            return file_path
        except Exception as e:
            logger.error("Failed to write WAV file: %s", e, exc_info=True)
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
                        logger.warning("Failed to delete old recording %s: %s", file_to_delete, e, exc_info=True)
        except Exception as e:
            logger.error("Error pruning old recordings: %s", e, exc_info=True)
