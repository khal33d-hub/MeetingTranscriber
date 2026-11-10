"""
Meeting Transcriber - a local Whisper desktop app for Windows.
Live meeting transcripts (your mic + the other people through your speakers)
and transcription of uploaded audio/video files. Runs fully offline.

UI: HTML/CSS/JS in ./ui rendered by pywebview (Edge WebView2).
This file is the bridge: the `Api` class is callable from JavaScript, and
`emit()` pushes events back to the page.
"""

import json
import os
import queue
import subprocess
import sys
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

import numpy as np
import webview

import engine
from engine import SAMPLE_RATE, fmt_ts

APP_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = APP_DIR / "settings.json"
ICON_FILE = APP_DIR / "icon.ico"
NO_SPEAKER = "None (microphone only)"
TITLE = "Meeting Transcriber"

MODELS = {
    "large-v3-turbo": "Best - fast & accurate (recommended)",
    "large-v3": "Most accurate - slower",
    "medium": "Good - lighter on the graphics card",
    "small": "Fast - lower accuracy",
    "base": "Fastest - basic accuracy",
}
LANGUAGES = {
    "English": "en", "Auto-detect": None, "French": "fr", "Spanish": "es",
    "German": "de", "Portuguese": "pt", "Italian": "it", "Dutch": "nl",
    "Arabic": "ar", "Yoruba": "yo", "Hausa": "ha", "Swahili": "sw",
}
BEAMS = {"Fast": 1, "Balanced": 5, "Accurate": 8}
DEFAULTS = {
    "model": "large-v3-turbo",
    "device": "Auto",
    "language": "English",
    "chunk_seconds": 6,
    "beam_size": 5,
    "save_recordings": True,
    "timestamps": True,
    "appearance": "Dark",
    "output_dir": str(APP_DIR / "transcripts"),
    "mic": "",
    "speaker": "",
    "sound_volume": 70,
    "sound_muted": False,
}
AUDIO_TYPES = ("Audio and video (*.mp3;*.wav;*.m4a;*.mp4;*.mkv;*.webm;*.ogg;*.flac;"
               "*.aac;*.wma;*.mov;*.avi)", "All files (*.*)")


def load_settings():
    s = dict(DEFAULTS)
    try:
        s.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    return s


def save_settings(s):
    try:
        SETTINGS_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")
    except Exception as e:
        print("Could not save settings:", e, file=sys.stderr)


def open_folder(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(str(path))
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _dialog(kind):
    """File dialog constant across pywebview versions (OPEN / SAVE / FOLDER)."""
    fd = getattr(webview, "FileDialog", None)
    if fd is not None:
        return getattr(fd, kind)
    return getattr(webview, f"{kind}_DIALOG")


def _srt(segments):
    return "".join(f"{i}\n{fmt_ts(s, True)} --> {fmt_ts(e, True)}\n{t}\n\n"
                   for i, (s, e, t) in enumerate(segments, 1))


# ==========================================================================
class Api:
    """Every public method is callable from JS as window.pywebview.api.<name>().
    Attributes start with '_' so pywebview does not expose them."""

    def __init__(self):
        self._settings = load_settings()
        self._engine = engine.Transcriber()
        self._window = None
        self._events = queue.Queue()
        self._page_ready = threading.Event()
        self._status = {"title": "Starting…", "sub": "", "kind": "loading"}
        self._busy = False
        self._loading = False
        self._closing = False

        self._live_running = False
        self._live_thread = None
        self._live_start = None
        self._pa = None
        self._sources = []
        self._wav_writer = None
        self._file_segments = []
        self._mic_devices, self._loop_devices = {}, {NO_SPEAKER: None}

        threading.Thread(target=self._pump, daemon=True).start()

    # ------------------------------------------------------------ events -> JS
    def _emit(self, kind, data=None):
        self._events.put({"type": kind, "data": data})

    def _pump(self):
        """Single thread that delivers events (and live meters) to the page."""
        self._page_ready.wait()
        while not self._closing:
            batch = []
            try:
                batch.append(self._events.get(timeout=0.1))
                while True:
                    batch.append(self._events.get_nowait())
            except queue.Empty:
                pass
            if self._live_running and self._live_start:
                mic = self._sources[0] if self._sources else None
                loop = self._sources[1] if len(self._sources) > 1 else None
                batch.append({"type": "meters", "data": {
                    "mic": mic.level if mic else 0,
                    "spk": loop.level if loop else 0,
                    "elapsed": time.time() - self._live_start}})
            if batch and self._window is not None:
                try:
                    self._window.evaluate_js(f"window.__mt && window.__mt({json.dumps(batch)})")
                except Exception as e:
                    print("UI update failed:", e, file=sys.stderr)

    def _set_status(self, title, sub="", kind="loading"):
        self._status = {"title": title, "sub": sub, "kind": kind}
        self._emit("status", self._status)

    def _ready_status(self):
        if self._engine.ready:
            gpu = self._engine.device_desc == "GPU"
            self._set_status(f"Ready · {self._engine.name}",
                             f"Running on {self._engine.device_desc}", "ready" if gpu else "cpu")

    def _error(self, title, message):
        self._emit("error", {"title": title, "message": str(message)})

    # ------------------------------------------------------------ window hooks
    def _on_loaded(self):
        self._page_ready.set()
        self._set_window_icon()
        self._hook_drop()
        if not self._engine.ready and not self._loading:
            self._load_model_async()

    def _on_closing(self):
        if self._live_running and not self._closing:
            self._emit("confirm_close")
            return False          # cancel; the page asks first
        self._closing = True
        return True

    def _set_window_icon(self):
        if sys.platform != "win32" or not ICON_FILE.exists():
            return
        try:
            import ctypes
            u32 = ctypes.windll.user32
            hwnd = u32.FindWindowW(None, TITLE)
            for size, which in ((16, 0), (32, 1)):   # ICON_SMALL, ICON_BIG
                h = u32.LoadImageW(None, str(ICON_FILE), 1, size, size, 0x10)
                if hwnd and h:
                    u32.SendMessageW(hwnd, 0x80, which, h)   # WM_SETICON
        except Exception:
            pass

    def _hook_drop(self):
        """Drag a file onto the window -> full path via pywebview's DOM events."""
        try:
            from webview.dom import DOMEventHandler

            def on_drop(e):
                files = (e.get("dataTransfer") or {}).get("files") or []
                path = files[0].get("pywebviewFullPath") if files else None
                if path:
                    self._emit("dropped", {"path": path})

            self._window.dom.document.events.drop += DOMEventHandler(on_drop, True, True)
        except Exception as e:
            print("Drag and drop unavailable:", e, file=sys.stderr)

    # ------------------------------------------------------------ JS: setup
    def init(self):
        return {
            "settings": self._settings,
            "models": MODELS,
            "languages": list(LANGUAGES),
            "status": self._status,
            "live_running": self._live_running,
            "busy": self._busy,
        }

    def set_setting(self, key, value):
        if key not in DEFAULTS:
            return {"ok": False, "error": f"Unknown setting {key}"}
        if key in ("model", "device") and (self._live_running or self._busy):
            return {"ok": False, "error": "Finish the current transcription before changing this.",
                    "settings": self._settings}
        if key == "beam_size" and value in BEAMS:
            value = BEAMS[value]
        if key == "chunk_seconds":
            value = max(3, min(15, int(value)))
        if key == "sound_volume":
            value = max(0, min(100, int(value)))
        if key == "sound_muted":
            value = bool(value)
        self._settings[key] = value
        save_settings(self._settings)
        if key in ("model", "device"):
            self._load_model_async()
        return {"ok": True, "settings": self._settings}

    def choose_folder(self):
        r = self._window.create_file_dialog(_dialog("FOLDER"),
                                            directory=self._settings["output_dir"])
        if not r:
            return None
        path = r[0] if isinstance(r, (list, tuple)) else r
        self.set_setting("output_dir", path)
        return path

    def open_output_folder(self):
        open_folder(self._settings["output_dir"])

    def copy(self, text):
        """Clipboard fallback when the browser clipboard API is unavailable."""
        try:
            subprocess.run(["clip"], input=("﻿" + text).encode("utf-16-le"),
                           check=True, creationflags=0x08000000)
            return True
        except Exception:
            return False

    def confirm_close(self):
        """Called by the page after the user agrees to stop the recording and quit."""
        self._closing = True
        self._live_running = False
        if self._live_thread:
            self._live_thread.join(timeout=30)
        self._window.destroy()

    # ------------------------------------------------------------ model
    def _load_model_async(self):
        if self._loading:
            return
        self._loading = True
        threading.Thread(target=self._load_model, daemon=True).start()

    def _load_model(self):
        name = self._settings["model"]
        device = self._settings["device"].lower()
        self._set_status(f"Loading {name}…",
                         "First use downloads the model - this can take a few minutes.")
        try:
            self._engine.load(name, device)
            self._ready_status()
        except Exception as e:
            self._set_status("Model not loaded", "Check Settings, then try again.", "error")
            self._error("Could not load the model", f"{name}:\n\n{e}")
        finally:
            self._loading = False

    def _not_ready(self):
        if self._loading or not self._engine.ready:
            return "The speech model is still loading."
        return None

    def _language(self):
        return LANGUAGES.get(self._settings["language"])

    def _line(self, start, text):
        return f"[{fmt_ts(start)}] {text}" if self._settings["timestamps"] else text

    def _write_transcript(self, stem, segments, srt=False):
        folder = Path(self._settings["output_dir"])
        folder.mkdir(parents=True, exist_ok=True)
        txt = folder / f"{stem}.txt"
        txt.write_text("\n".join(self._line(s, t) for s, e, t in segments) + "\n",
                       encoding="utf-8")
        if srt:
            (folder / f"{stem}.srt").write_text(_srt(segments), encoding="utf-8")
        return txt

    # ------------------------------------------------------------ JS: files
    def choose_file(self):
        r = self._window.create_file_dialog(_dialog("OPEN"), file_types=AUDIO_TYPES)
        if not r:
            return None
        return r[0] if isinstance(r, (list, tuple)) else r

    def transcribe_file(self, path):
        if self._busy or self._live_running:
            return {"ok": False, "code": "busy", "error": "Another transcription is already running."}
        err = self._not_ready()
        if err:
            return {"ok": False, "code": "not_ready", "error": err}
        if not path or not Path(path).is_file():
            return {"ok": False, "error": "That file could not be found."}
        self._busy = True
        threading.Thread(target=self._transcribe_file, args=(path,), daemon=True).start()
        return {"ok": True, "name": Path(path).name}

    def _transcribe_file(self, path):
        self._file_segments = []
        name = Path(path).name
        self._emit("file_state", {"running": True, "name": name})
        self._set_status("Transcribing file…", name, "busy")
        started = time.time()
        try:
            for s, e, text, duration in self._engine.transcribe_stream(
                    path, self._language(), self._settings["beam_size"]):
                self._file_segments.append((s, e, text))
                self._emit("file_seg", {"start": s, "text": text,
                                        "progress": min(1.0, e / duration)})
            saved = self._write_transcript(Path(name).stem, self._file_segments, srt=True)
            self._emit("file_state", {"running": False, "name": name,
                                      "done": f"Done in {time.time() - started:.0f}s · saved as {saved.name}"})
        except Exception as e:
            self._emit("file_state", {"running": False, "name": name, "failed": True})
            self._error("Could not transcribe file", f"{name}:\n\n{e}")
        finally:
            self._busy = False
            self._ready_status()

    def save_as(self, text, allow_srt=False):
        text = (text or "").strip()
        if not text:
            return None
        types = ("Text file (*.txt)",)
        if allow_srt and self._file_segments:
            types += ("Subtitles (*.srt)",)
        r = self._window.create_file_dialog(_dialog("SAVE"), directory=self._settings["output_dir"],
                                            save_filename="transcript.txt", file_types=types)
        if not r:
            return None
        path = Path(r[0] if isinstance(r, (list, tuple)) else r)
        if path.suffix.lower() == ".srt":
            path.write_text(_srt(self._file_segments), encoding="utf-8")
        else:
            if not path.suffix:
                path = path.with_suffix(".txt")
            path.write_text(text + "\n", encoding="utf-8")
        return path.name

    # ------------------------------------------------------------ JS: history
    def list_history(self):
        folder = Path(self._settings["output_dir"])
        if not folder.exists():
            return []
        files = sorted(folder.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
        out = []
        for f in files:
            st = f.stat()
            out.append({"name": f.stem, "path": str(f),
                        "when": datetime.fromtimestamp(st.st_mtime).strftime("%d %b %Y, %H:%M"),
                        "size": st.st_size})
        return out

    def read_history(self, path):
        p = Path(path)
        if p.parent.resolve() != Path(self._settings["output_dir"]).resolve():
            return {"ok": False, "error": "File is outside the transcripts folder."}
        try:
            return {"ok": True, "text": p.read_text(encoding="utf-8")}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------ JS: live
    def refresh_devices(self):
        if engine.pyaudio is None:
            return {"ok": False, "error": "Audio library missing - run setup.bat",
                    "mics": [], "speakers": [NO_SPEAKER]}
        try:
            mics, loops, dmic, dloop = engine.list_devices()
        except Exception as e:
            return {"ok": False, "error": f"Could not list audio devices: {e}",
                    "mics": [], "speakers": [NO_SPEAKER]}
        self._mic_devices = {d["name"]: d for d in mics}
        self._loop_devices = {NO_SPEAKER: None}
        self._loop_devices.update({d["name"]: d for d in loops})

        saved_mic, saved_spk = self._settings.get("mic"), self._settings.get("speaker")
        mic = saved_mic if saved_mic in self._mic_devices else next(
            (n for n, d in self._mic_devices.items() if d["index"] == dmic),
            next(iter(self._mic_devices), ""))
        spk = saved_spk if saved_spk in self._loop_devices else next(
            (n for n, d in self._loop_devices.items() if d and d["index"] == dloop), NO_SPEAKER)
        return {"ok": True, "mics": list(self._mic_devices), "speakers": list(self._loop_devices),
                "mic": mic, "speaker": spk}

    def start_live(self, mic_name, speaker_name):
        if self._live_running:
            return {"ok": False, "error": "Already recording."}
        if self._busy:
            return {"ok": False, "code": "busy", "error": "A file is being transcribed. Wait for it to finish."}
        err = self._not_ready()
        if err:
            return {"ok": False, "code": "not_ready", "error": err}
        mic = self._mic_devices.get(mic_name)
        loop = self._loop_devices.get(speaker_name)
        if mic is None:
            return {"ok": False, "error": "Choose a microphone first."}
        try:
            self._pa = engine.pyaudio.PyAudio()
            self._sources = [engine.AudioSource(self._pa, mic)]
            if loop is not None:
                self._sources.append(engine.AudioSource(self._pa, loop))
            for s in self._sources:
                s.start()
        except Exception as e:
            self._stop_audio()
            return {"ok": False, "error": f"Could not open the audio device:\n\n{e}"}

        self._live_stamp = datetime.now().strftime("Meeting %Y-%m-%d %H-%M")
        self._live_wav = None
        if self._settings["save_recordings"]:
            rec_dir = Path(self._settings["output_dir"]) / "recordings"
            rec_dir.mkdir(parents=True, exist_ok=True)
            self._live_wav = rec_dir / f"{self._live_stamp}.wav"
            self._wav_writer = wave.open(str(self._live_wav), "wb")
            self._wav_writer.setnchannels(1)
            self._wav_writer.setsampwidth(2)
            self._wav_writer.setframerate(SAMPLE_RATE)

        self._live_running = True
        self._live_start = time.time()
        self._live_thread = threading.Thread(target=self._live_loop, daemon=True)
        self._live_thread.start()
        return {"ok": True, "stamp": self._live_stamp}

    def stop_live(self):
        self._live_running = False
        return True

    def _live_loop(self):
        chunk = SAMPLE_RATE * int(self._settings["chunk_seconds"])
        mic = self._sources[0]
        loop = self._sources[1] if len(self._sources) > 1 else None
        elapsed, prompt, segments = 0.0, None, []
        self._emit("live_state", {"running": True, "stamp": self._live_stamp})
        self._set_status("Recording", f"{self._engine.name} on {self._engine.device_desc}",
                         "recording")
        try:
            while True:
                stopping = not self._live_running
                if mic.available() < chunk and not stopping:
                    time.sleep(0.15)
                    continue
                audio = mic.take(chunk)
                if len(audio) == 0:
                    break
                if loop is not None:
                    # Windows sends nothing from the speakers during silence: pad with zeros.
                    other = loop.take(len(audio))
                    if len(other) < len(audio):
                        other = np.pad(other, (0, len(audio) - len(other)))
                    audio = audio + other
                    loop.trim(chunk * 2)
                    peak = float(np.max(np.abs(audio)))
                    if peak > 1.0:
                        audio = audio / peak
                if self._wav_writer is not None:
                    self._wav_writer.writeframes(
                        (np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())

                if float(np.sqrt(np.mean(audio ** 2))) > 0.003:
                    segs, _ = self._engine.transcribe(
                        audio, self._language(), self._settings["beam_size"], prompt,
                        condition_on_previous_text=False)
                    text = " ".join(t for _, _, t in segs).strip()
                    if text:
                        segments.append((elapsed, elapsed + len(audio) / SAMPLE_RATE, text))
                        self._emit("live", {"start": elapsed, "text": text})
                        self._write_transcript(self._live_stamp, segments)  # saved as you go
                        prompt = text[-200:]
                elapsed += len(audio) / SAMPLE_RATE
                if stopping:
                    break
        except Exception as e:
            self._error("Live transcription stopped", e)
        finally:
            self._stop_audio()
            self._live_running = False
            self._live_start = None
            note = f"Saved transcript: {self._live_stamp}.txt" if segments else "Nothing was transcribed."
            if self._live_wav:
                note += " · Audio saved in recordings - transcribe it as a file for a cleaner final copy."
            self._emit("live_state", {"running": False, "stamp": self._live_stamp, "note": note})
            self._ready_status()

    def _stop_audio(self):
        for s in self._sources:
            s.stop()
        self._sources = []
        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None
        if self._wav_writer is not None:
            try:
                self._wav_writer.close()
            except Exception:
                pass
            self._wav_writer = None


# ==========================================================================
def main():
    os.chdir(APP_DIR)
    # Let UI sounds (welcome, model ready) play without a click first.
    flag = "--autoplay-policy=no-user-gesture-required"
    extra = os.environ.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "")
    if flag not in extra:
        os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (extra + " " + flag).strip()
    if sys.platform == "win32":
        try:   # own taskbar group + icon instead of Python's
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MeetingTranscriber.App")
        except Exception:
            pass

    api = Api()
    dark = api._settings.get("appearance") != "Light"
    window = webview.create_window(
        TITLE, str(APP_DIR / "ui" / "index.html"), js_api=api, width=1140, height=760, min_size=(880, 600),
        background_color="#0E0E12" if dark else "#F7F7F8")
    api._window = window
    window.events.loaded += api._on_loaded
    window.events.closing += api._on_closing
    webview.start(http_server=True, debug="--debug" in sys.argv)


if __name__ == "__main__":
    main()
