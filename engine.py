"""Transcription engine: Whisper model + Windows audio capture (no UI code here)."""

import os
import sys
import threading
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000  # Whisper works at 16 kHz mono


def _add_cuda_dll_dirs():
    """Make the pip-installed NVIDIA CUDA libraries visible to Windows."""
    try:
        import nvidia
        for base in list(nvidia.__path__):
            for sub in ("cublas/bin", "cudnn/bin", "cuda_runtime/bin"):
                p = Path(base) / sub
                if p.exists():
                    if hasattr(os, "add_dll_directory"):
                        os.add_dll_directory(str(p))
                    os.environ["PATH"] = str(p) + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass


_add_cuda_dll_dirs()

try:
    from faster_whisper import WhisperModel
except ImportError:  # lets the UI open and show a clear error
    WhisperModel = None

try:
    import pyaudiowpatch as pyaudio
except ImportError:
    pyaudio = None


# --------------------------------------------------------------------------
def fmt_ts(seconds, srt=False):
    seconds = max(0.0, float(seconds))
    h, m, s = int(seconds // 3600), int(seconds % 3600 // 60), int(seconds % 60)
    if srt:
        ms = min(999, int(round((seconds - int(seconds)) * 1000)))
        return f"{h:02}:{m:02}:{s:02},{ms:03}"
    return f"{h:02}:{m:02}:{s:02}"


def to_mono_16k(raw_bytes, channels, rate):
    """int16 interleaved bytes -> float32 mono at 16 kHz."""
    audio = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != SAMPLE_RATE and len(audio) > 0:
        n_out = int(round(len(audio) * SAMPLE_RATE / rate))
        if n_out <= 0:
            return np.zeros(0, dtype=np.float32)
        x_old = np.linspace(0.0, 1.0, num=len(audio), endpoint=False)
        x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
        audio = np.interp(x_new, x_old, audio).astype(np.float32)
    return audio


def load_audio(path):
    """Decode any audio/video file to 16 kHz mono float32.

    Done here rather than inside faster-whisper because its built-in decoder
    passes an option ('metadata_errors') that newer PyAV versions removed.
    Works with old and new PyAV, and skips damaged packets instead of failing.
    """
    import av
    try:
        container = av.open(str(path), mode="r", metadata_errors="ignore")
    except TypeError:            # newer PyAV: option no longer exists
        container = av.open(str(path), mode="r")
    chunks = []
    try:
        if not container.streams.audio:
            raise RuntimeError("This file has no audio track.")
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)

        def add(frames):
            for f in frames if isinstance(frames, list) else [frames]:
                if f is not None:
                    chunks.append(f.to_ndarray().reshape(-1))

        for packet in container.demux(stream):
            try:
                for frame in packet.decode():
                    add(resampler.resample(frame))
            except Exception:     # damaged packet: skip it, keep going
                continue
        try:
            add(resampler.resample(None))   # flush
        except Exception:
            pass
    finally:
        container.close()
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32) / 32768.0


# --------------------------------------------------------------------------
class Transcriber:
    """Owns the Whisper model. One transcription at a time."""

    def __init__(self):
        self.model = None
        self.name = None
        self.device_desc = ""
        self.lock = threading.Lock()

    def load(self, name, device="auto"):
        """device: 'auto' | 'gpu' | 'cpu'. Raises on failure."""
        if WhisperModel is None:
            raise RuntimeError("faster-whisper is not installed. Run setup.bat again.")
        with self.lock:
            self.model = None
            last_err = None
            if device in ("auto", "gpu"):
                try:
                    m = WhisperModel(name, device="cuda", compute_type="float16")
                    # CUDA library problems only appear on first use, so warm up.
                    m.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), language="en")
                    self.model, self.name, self.device_desc = m, name, "GPU"
                    return
                except Exception as e:
                    last_err = e
                    print("GPU load failed:", e, file=sys.stderr)
                    if device == "gpu":
                        raise RuntimeError(f"Could not use the graphics card:\n{e}")
            m = WhisperModel(name, device="cpu", compute_type="int8")
            self.model, self.name = m, name
            self.device_desc = "CPU" if device == "cpu" else "CPU (GPU unavailable)"
            if last_err:
                print("Running on CPU instead.", file=sys.stderr)

    @property
    def ready(self):
        return self.model is not None

    def transcribe(self, audio, language=None, beam_size=5, prompt=None, **kw):
        with self.lock:
            segments, info = self.model.transcribe(
                audio, language=language, beam_size=beam_size,
                vad_filter=True, initial_prompt=prompt, **kw)
            # segments is a generator: consume it while holding the lock
            return [(s.start, s.end, s.text.strip()) for s in segments], info

    def transcribe_stream(self, path, language=None, beam_size=5):
        """Yield (start, end, text, duration) as each segment is decoded."""
        audio = load_audio(path)
        if len(audio) == 0:
            raise RuntimeError("No audio could be read from this file.")
        with self.lock:
            segments, info = self.model.transcribe(
                audio, language=language, beam_size=beam_size,
                vad_filter=True, vad_parameters={"min_silence_duration_ms": 500})
            for s in segments:
                yield s.start, s.end, s.text.strip(), info.duration or 1


# --------------------------------------------------------------------------
class AudioSource:
    """One WASAPI input stream feeding a buffer of 16 kHz mono audio."""

    def __init__(self, pa, device_info):
        self.pa = pa
        self.info = device_info
        self.channels = max(1, int(device_info["maxInputChannels"]))
        self.rate = int(device_info["defaultSampleRate"])
        self.lock = threading.Lock()
        self.buffer = np.zeros(0, dtype=np.float32)
        self.level = 0.0  # 0..1 for the UI meter
        self.stream = None

    def _callback(self, in_data, frame_count, time_info, status):
        chunk = to_mono_16k(in_data, self.channels, self.rate)
        if len(chunk):
            rms = float(np.sqrt(np.mean(chunk ** 2)))
            self.level = min(1.0, rms * 8)
        with self.lock:
            self.buffer = np.concatenate([self.buffer, chunk])
        return (None, pyaudio.paContinue)

    def start(self):
        self.stream = self.pa.open(
            format=pyaudio.paInt16, channels=self.channels, rate=self.rate,
            input=True, input_device_index=self.info["index"],
            frames_per_buffer=1024, stream_callback=self._callback)
        self.stream.start_stream()

    def take(self, n):
        with self.lock:
            out, self.buffer = self.buffer[:n], self.buffer[n:]
        return out

    def available(self):
        with self.lock:
            return len(self.buffer)

    def trim(self, max_samples):
        with self.lock:
            if len(self.buffer) > max_samples:
                self.buffer = self.buffer[-max_samples:]

    def stop(self):
        if self.stream is not None:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        self.level = 0.0


def list_devices():
    """Return (mics, loopbacks, default_mic_index, default_loopback_index)."""
    if pyaudio is None:
        return [], [], None, None
    pa = pyaudio.PyAudio()
    try:
        wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        mics, loops = [], []
        for i in range(pa.get_device_count()):
            d = pa.get_device_info_by_index(i)
            if d["hostApi"] != wasapi["index"] or d["maxInputChannels"] <= 0:
                continue
            (loops if d.get("isLoopbackDevice") else mics).append(d)
        default_loop = None
        try:
            default_loop = pa.get_default_wasapi_loopback()["index"]
        except Exception:
            pass
        return mics, loops, wasapi.get("defaultInputDevice"), default_loop
    finally:
        pa.terminate()
