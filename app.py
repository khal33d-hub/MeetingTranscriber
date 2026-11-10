"""
Meeting Transcriber - a local Whisper desktop app for Windows.
Live meeting transcripts (your mic + the other people through your speakers)
and transcription of uploaded audio/video files. Runs fully offline.
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
from tkinter import filedialog, messagebox

import numpy as np
import customtkinter as ctk

import engine
from engine import SAMPLE_RATE, fmt_ts

APP_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = APP_DIR / "settings.json"
ICON_FILE = APP_DIR / "icon.ico"
NO_SPEAKER = "None (microphone only)"

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
}

ACCENT = ("#3B6FE0", "#4C82F7")
ACCENT_HOVER = ("#2F5BC0", "#3A6EE0")
RED = ("#D64545", "#E5534B")
RED_HOVER = ("#B83838", "#C94540")
MUTED = ("gray40", "gray65")
CARD = ("gray97", "gray17")
SIDEBAR = ("gray91", "gray11")


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


# ==========================================================================
class App(ctk.CTk):
    def __init__(self):
        self.settings = load_settings()
        ctk.set_appearance_mode(self.settings["appearance"])
        ctk.set_default_color_theme("blue")
        super().__init__()

        self.title("Meeting Transcriber")
        self.geometry("1100x720")
        self.minsize(900, 600)
        if ICON_FILE.exists() and sys.platform == "win32":
            try:
                self.iconbitmap(str(ICON_FILE))
            except Exception:
                pass

        self.engine = engine.Transcriber()
        self.ui_queue = queue.Queue()
        self.busy = False
        self.loading = False

        # live state
        self.live_running = False
        self.live_thread = None
        self.live_start = None
        self.pa = None
        self.sources = []
        self.wav_writer = None

        self.file_segments = []
        self.mic_devices, self.loop_devices = {}, {NO_SPEAKER: None}

        self.f_title = ctk.CTkFont(size=24, weight="bold")
        self.f_head = ctk.CTkFont(size=15, weight="bold")
        self.f_body = ctk.CTkFont(size=13)
        self.f_small = ctk.CTkFont(size=12)
        self.f_text = ctk.CTkFont(family="Segoe UI", size=14)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        self.pages = {
            "live": self._build_live_page(),
            "file": self._build_file_page(),
            "history": self._build_history_page(),
            "settings": self._build_settings_page(),
        }
        self.show_page("live")
        self._refresh_devices()
        self._load_model_async()
        self.after(100, self._tick)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------ layout
    def _build_sidebar(self):
        bar = ctk.CTkFrame(self, width=220, corner_radius=0, fg_color=SIDEBAR)
        bar.grid(row=0, column=0, sticky="nsw")
        bar.grid_propagate(False)
        bar.grid_rowconfigure(6, weight=1)

        ctk.CTkLabel(bar, text="🎙  Transcriber", font=ctk.CTkFont(size=20, weight="bold")
                     ).grid(row=0, column=0, padx=20, pady=(26, 2), sticky="w")
        ctk.CTkLabel(bar, text="Private • runs on your PC", font=self.f_small,
                     text_color=MUTED).grid(row=1, column=0, padx=22, pady=(0, 22), sticky="w")

        self.nav_buttons = {}
        for row, (key, label) in enumerate(
                [("live", "●  Live meeting"), ("file", "📄  Transcribe file"),
                 ("history", "🕘  History"), ("settings", "⚙  Settings")], start=2):
            b = ctk.CTkButton(bar, text=label, anchor="w", height=40, corner_radius=8,
                              font=self.f_body, fg_color="transparent",
                              text_color=("gray15", "gray85"),
                              hover_color=("gray80", "gray20"),
                              command=lambda k=key: self.show_page(k))
            b.grid(row=row, column=0, padx=12, pady=3, sticky="ew")
            self.nav_buttons[key] = b

        status = ctk.CTkFrame(bar, fg_color=("gray85", "gray16"), corner_radius=10)
        status.grid(row=7, column=0, padx=12, pady=16, sticky="ew")
        self.status_dot = ctk.CTkLabel(status, text="●", text_color="orange",
                                       font=ctk.CTkFont(size=16))
        self.status_dot.grid(row=0, column=0, padx=(12, 6), pady=(10, 0))
        self.status_title = ctk.CTkLabel(status, text="Loading model…", font=self.f_small,
                                         anchor="w")
        self.status_title.grid(row=0, column=1, sticky="w", pady=(10, 0), padx=(0, 10))
        self.status_sub = ctk.CTkLabel(status, text="", font=ctk.CTkFont(size=11),
                                       text_color=MUTED, anchor="w", justify="left",
                                       wraplength=170)
        self.status_sub.grid(row=1, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 10))

    def _page(self, title, subtitle):
        page = ctk.CTkFrame(self, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(page, text=title, font=self.f_title).grid(
            row=0, column=0, sticky="w", padx=28, pady=(24, 0))
        ctk.CTkLabel(page, text=subtitle, font=self.f_body, text_color=MUTED).grid(
            row=1, column=0, sticky="w", padx=28, pady=(2, 14))
        return page

    def _card(self, parent, row, expand=False):
        card = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=12)
        card.grid(row=row, column=0, sticky="nsew" if expand else "ew",
                  padx=24, pady=(0, 14))
        if expand:
            parent.grid_rowconfigure(row, weight=1)
        return card

    def _transcript_box(self, parent):
        box = ctk.CTkTextbox(parent, font=self.f_text, wrap="word", corner_radius=10,
                             border_width=0, fg_color=("white", "gray9"))
        return box

    def show_page(self, key):
        for k, p in self.pages.items():
            p.grid_forget()
        self.pages[key].grid(row=0, column=1, sticky="nsew")
        for k, b in self.nav_buttons.items():
            b.configure(fg_color=ACCENT if k == key else "transparent",
                        text_color=("white", "white") if k == key else ("gray15", "gray85"),
                        hover_color=ACCENT_HOVER if k == key else ("gray80", "gray20"))
        if key == "history":
            self._refresh_history()

    # ------------------------------------------------------------ live page
    def _build_live_page(self):
        page = self._page("Live meeting", "Transcribes you and everyone you can hear, as they speak.")

        src = self._card(page, 2)
        src.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(src, text="🎤  Your microphone", font=self.f_body).grid(
            row=0, column=0, sticky="w", padx=18, pady=(16, 6))
        self.mic_menu = ctk.CTkOptionMenu(src, values=["…"], dynamic_resizing=False,
                                          command=lambda v: self._remember("mic", v))
        self.mic_menu.grid(row=0, column=1, sticky="ew", padx=10, pady=(16, 6))
        self.mic_meter = ctk.CTkProgressBar(src, width=120, height=8, progress_color="#3DBE7A")
        self.mic_meter.set(0)
        self.mic_meter.grid(row=0, column=2, padx=(4, 18), pady=(16, 6))

        ctk.CTkLabel(src, text="🔊  Meeting audio (speakers)", font=self.f_body).grid(
            row=1, column=0, sticky="w", padx=18, pady=(6, 16))
        self.loop_menu = ctk.CTkOptionMenu(src, values=[NO_SPEAKER], dynamic_resizing=False,
                                           command=lambda v: self._remember("speaker", v))
        self.loop_menu.grid(row=1, column=1, sticky="ew", padx=10, pady=(6, 16))
        self.loop_meter = ctk.CTkProgressBar(src, width=120, height=8, progress_color="#3DBE7A")
        self.loop_meter.set(0)
        self.loop_meter.grid(row=1, column=2, padx=(4, 18), pady=(6, 16))

        ctrl = ctk.CTkFrame(page, fg_color="transparent")
        ctrl.grid(row=3, column=0, sticky="ew", padx=24, pady=(0, 12))
        self.live_btn = ctk.CTkButton(ctrl, text="●  Start recording", height=46, width=210,
                                      corner_radius=23, font=self.f_head,
                                      fg_color=RED, hover_color=RED_HOVER,
                                      command=self._toggle_live)
        self.live_btn.pack(side="left")
        self.timer_label = ctk.CTkLabel(ctrl, text="00:00:00",
                                        font=ctk.CTkFont(family="Consolas", size=22))
        self.timer_label.pack(side="left", padx=20)
        ctk.CTkButton(ctrl, text="Refresh devices", width=130, height=34,
                      fg_color="transparent", border_width=1,
                      text_color=("gray20", "gray85"), border_color=("gray70", "gray30"),
                      hover_color=("gray85", "gray20"),
                      command=self._refresh_devices).pack(side="right")

        out = self._card(page, 4, expand=True)
        out.grid_columnconfigure(0, weight=1)
        out.grid_rowconfigure(0, weight=1)
        self.live_text = self._transcript_box(out)
        self.live_text.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 4))
        self._toolbar(out, self.live_text, row=1)
        return page

    # ------------------------------------------------------------ file page
    def _build_file_page(self):
        page = self._page("Transcribe a file",
                          "Meeting recordings, voice notes or videos: mp3, m4a, wav, mp4, mkv and more.")
        drop = self._card(page, 2)
        drop.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(drop, text="📂", font=ctk.CTkFont(size=36)).grid(row=0, column=0, pady=(20, 0))
        self.file_btn = ctk.CTkButton(drop, text="Choose a file…", height=42, width=200,
                                      corner_radius=21, font=self.f_head,
                                      fg_color=ACCENT, hover_color=ACCENT_HOVER,
                                      command=self._choose_file)
        self.file_btn.grid(row=1, column=0, pady=(8, 6))
        self.file_label = ctk.CTkLabel(drop, text="No file selected", font=self.f_small,
                                       text_color=MUTED)
        self.file_label.grid(row=2, column=0)
        self.file_progress = ctk.CTkProgressBar(drop, height=10, progress_color=ACCENT)
        self.file_progress.set(0)
        self.file_progress.grid(row=3, column=0, sticky="ew", padx=40, pady=(10, 20))

        out = self._card(page, 3, expand=True)
        out.grid_columnconfigure(0, weight=1)
        out.grid_rowconfigure(0, weight=1)
        self.file_text = self._transcript_box(out)
        self.file_text.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 4))
        self._toolbar(out, self.file_text, row=1, srt=True)
        return page

    def _toolbar(self, parent, box, row, srt=False):
        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.grid(row=row, column=0, sticky="ew", padx=10, pady=(4, 10))
        style = dict(height=32, width=110, fg_color="transparent", border_width=1,
                     text_color=("gray20", "gray85"), border_color=("gray70", "gray30"),
                     hover_color=("gray85", "gray20"))
        ctk.CTkButton(bar, text="Copy", command=lambda: self._copy(box), **style).pack(side="left", padx=(0, 6))
        ctk.CTkButton(bar, text="Save as…", command=lambda: self._save_as(box, srt), **style).pack(side="left", padx=6)
        ctk.CTkButton(bar, text="Clear", command=lambda: box.delete("1.0", "end"), **style).pack(side="left", padx=6)
        ctk.CTkButton(bar, text="Open folder", command=lambda: open_folder(self.settings["output_dir"]),
                      **style).pack(side="right")

    # ------------------------------------------------------------ history page
    def _build_history_page(self):
        page = self._page("History", "Your saved transcripts. Click one to read it.")
        page.grid_rowconfigure(2, weight=1)
        body = ctk.CTkFrame(page, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=24, pady=(0, 20))
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)
        self.history_list = ctk.CTkScrollableFrame(body, width=280, fg_color=CARD, corner_radius=12)
        self.history_list.grid(row=0, column=0, sticky="nsw", padx=(0, 14))
        right = ctk.CTkFrame(body, fg_color=CARD, corner_radius=12)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=1)
        self.history_text = self._transcript_box(right)
        self.history_text.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 4))
        self._toolbar(right, self.history_text, row=1)
        return page

    def _refresh_history(self):
        for w in self.history_list.winfo_children():
            w.destroy()
        folder = Path(self.settings["output_dir"])
        files = sorted(folder.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True) \
            if folder.exists() else []
        if not files:
            ctk.CTkLabel(self.history_list, text="No transcripts yet.", text_color=MUTED,
                         font=self.f_small).pack(pady=20)
            return
        for f in files:
            when = datetime.fromtimestamp(f.stat().st_mtime).strftime("%d %b %Y, %H:%M")
            ctk.CTkButton(self.history_list, text=f"{f.stem}\n{when}", anchor="w",
                          height=48, font=self.f_small, fg_color="transparent",
                          text_color=("gray15", "gray85"), hover_color=("gray85", "gray20"),
                          command=lambda p=f: self._show_history_file(p)
                          ).pack(fill="x", padx=4, pady=2)

    def _show_history_file(self, path):
        self.history_text.delete("1.0", "end")
        try:
            self.history_text.insert("end", path.read_text(encoding="utf-8"))
        except Exception as e:
            self.history_text.insert("end", f"Could not open file: {e}")

    # ------------------------------------------------------------ settings page
    def _build_settings_page(self):
        page = self._page("Settings", "Changes are saved automatically.")
        page.grid_rowconfigure(2, weight=1)
        scroll = ctk.CTkScrollableFrame(page, fg_color="transparent")
        scroll.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))
        scroll.grid_columnconfigure(0, weight=1)

        def section(row, title):
            card = ctk.CTkFrame(scroll, fg_color=CARD, corner_radius=12)
            card.grid(row=row, column=0, sticky="ew", padx=14, pady=(0, 14))
            card.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(card, text=title, font=self.f_head).grid(
                row=0, column=0, columnspan=3, sticky="w", padx=18, pady=(14, 6))
            return card

        def setting_row(card, row, label, hint, widget):
            ctk.CTkLabel(card, text=label, font=self.f_body).grid(
                row=row, column=0, sticky="w", padx=18, pady=(8, 0))
            ctk.CTkLabel(card, text=hint, font=ctk.CTkFont(size=11), text_color=MUTED).grid(
                row=row + 1, column=0, sticky="w", padx=18, pady=(0, 10))
            widget.grid(row=row, column=2, rowspan=2, sticky="e", padx=18)
            return card.grid_slaves(row=row + 1, column=0)[0]

        # --- Model
        m = section(0, "Speech model")
        self.model_menu = ctk.CTkOptionMenu(m, values=list(MODELS), width=200,
                                            command=self._on_model_change)
        self.model_menu.set(self.settings["model"])
        self.model_hint = setting_row(m, 1, "Model", "", self.model_menu)
        self._update_model_hint()

        self.device_seg = ctk.CTkSegmentedButton(m, values=["Auto", "GPU", "CPU"],
                                                 command=self._on_device_change)
        self.device_seg.set(self.settings["device"])
        setting_row(m, 4, "Run on", "Auto uses your graphics card and falls back to the processor.",
                    self.device_seg)

        self.lang_menu = ctk.CTkOptionMenu(m, values=list(LANGUAGES), width=200,
                                           command=lambda v: self._remember("language", v))
        self.lang_menu.set(self.settings["language"])
        setting_row(m, 6, "Language", "Setting it is faster and more accurate than auto-detect.",
                    self.lang_menu)

        # --- Live
        lv = section(1, "Live transcription")
        chunk_wrap = ctk.CTkFrame(lv, fg_color="transparent")
        self.chunk_label = ctk.CTkLabel(chunk_wrap, text="", width=40, font=self.f_body)
        self.chunk_slider = ctk.CTkSlider(chunk_wrap, from_=3, to=15, number_of_steps=12, width=180,
                                          command=self._on_chunk_change)
        self.chunk_slider.set(self.settings["chunk_seconds"])
        self.chunk_slider.pack(side="left")
        self.chunk_label.pack(side="left", padx=(8, 0))
        self._on_chunk_change(self.settings["chunk_seconds"])
        setting_row(lv, 1, "Update every",
                    "Shorter = text appears sooner. Longer = more accurate sentences.", chunk_wrap)

        self.rec_switch = ctk.CTkSwitch(lv, text="", command=lambda: self._remember(
            "save_recordings", bool(self.rec_switch.get())))
        if self.settings["save_recordings"]:
            self.rec_switch.select()
        setting_row(lv, 3, "Save meeting audio",
                    "Keeps a WAV of each meeting so you can re-transcribe it for a cleaner copy.",
                    self.rec_switch)

        # --- Output
        o = section(2, "Transcripts")
        self.ts_switch = ctk.CTkSwitch(o, text="", command=lambda: self._remember(
            "timestamps", bool(self.ts_switch.get())))
        if self.settings["timestamps"]:
            self.ts_switch.select()
        setting_row(o, 1, "Show timestamps", "Adds [00:01:23] before each line.", self.ts_switch)

        self.beam_seg = ctk.CTkSegmentedButton(o, values=["Fast", "Balanced", "Accurate"],
                                               command=self._on_beam_change)
        self.beam_seg.set({1: "Fast", 5: "Balanced", 8: "Accurate"}.get(self.settings["beam_size"], "Balanced"))
        setting_row(o, 3, "Quality", "How hard the model searches for the best wording.", self.beam_seg)

        folder_wrap = ctk.CTkFrame(o, fg_color="transparent")
        self.folder_label = ctk.CTkLabel(folder_wrap, text=self._short(self.settings["output_dir"]),
                                         font=self.f_small, text_color=MUTED)
        self.folder_label.pack(side="left", padx=(0, 10))
        ctk.CTkButton(folder_wrap, text="Change…", width=90, command=self._choose_folder).pack(side="left")
        setting_row(o, 5, "Save transcripts in", "Live and file transcripts are saved here automatically.",
                    folder_wrap)

        # --- Appearance
        a = section(3, "Appearance")
        self.theme_seg = ctk.CTkSegmentedButton(a, values=["Light", "Dark", "System"],
                                                command=self._on_theme_change)
        self.theme_seg.set(self.settings["appearance"])
        setting_row(a, 1, "Theme", "Light, dark, or follow Windows.", self.theme_seg)
        return page

    @staticmethod
    def _short(path, n=38):
        return path if len(path) <= n else "…" + path[-(n - 1):]

    # ------------------------------------------------------------ settings handlers
    def _remember(self, key, value):
        self.settings[key] = value
        save_settings(self.settings)

    def _update_model_hint(self):
        self.model_hint.configure(text=MODELS.get(self.model_menu.get(), "") +
                                  ". Downloaded once from Hugging Face, then works offline.")

    def _on_model_change(self, value):
        if self.live_running or self.busy:
            messagebox.showinfo("Busy", "Finish the current transcription before changing the model.")
            self.model_menu.set(self.settings["model"])
            return
        self._remember("model", value)
        self._update_model_hint()
        self._load_model_async()

    def _on_device_change(self, value):
        if self.live_running or self.busy:
            messagebox.showinfo("Busy", "Finish the current transcription first.")
            self.device_seg.set(self.settings["device"])
            return
        self._remember("device", value)
        self._load_model_async()

    def _on_chunk_change(self, value):
        v = int(round(float(value)))
        self.chunk_label.configure(text=f"{v}s")
        self._remember("chunk_seconds", v)

    def _on_beam_change(self, value):
        self._remember("beam_size", {"Fast": 1, "Balanced": 5, "Accurate": 8}[value])

    def _on_theme_change(self, value):
        ctk.set_appearance_mode(value)
        self._remember("appearance", value)

    def _choose_folder(self):
        d = filedialog.askdirectory(initialdir=self.settings["output_dir"])
        if d:
            self._remember("output_dir", d)
            self.folder_label.configure(text=self._short(d))

    # ------------------------------------------------------------ status / queue
    def _set_status(self, title, sub="", colour="orange"):
        self.ui_queue.put(("status", (title, sub, colour)))

    def _tick(self):
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "status":
                    t, s, c = payload
                    self.status_title.configure(text=t)
                    self.status_sub.configure(text=s)
                    self.status_dot.configure(text_color=c)
                elif kind == "live":
                    self.live_text.insert("end", payload + "\n")
                    self.live_text.see("end")
                elif kind == "file":
                    self.file_text.insert("end", payload + "\n")
                    self.file_text.see("end")
                elif kind == "progress":
                    self.file_progress.set(payload)
                elif kind == "error":
                    messagebox.showerror("Something went wrong", payload)
                elif kind == "file_done":
                    self.file_btn.configure(state="normal")
                    self.live_btn.configure(state="normal")
                elif kind == "live_stopped":
                    self.live_btn.configure(text="●  Start recording", fg_color=RED,
                                            hover_color=RED_HOVER, state="normal")
                    self.file_btn.configure(state="normal")
        except queue.Empty:
            pass

        if self.live_running and self.live_start:
            self.timer_label.configure(text=fmt_ts(time.time() - self.live_start))
        mic = self.sources[0] if self.sources else None
        loop = self.sources[1] if len(self.sources) > 1 else None
        self.mic_meter.set(mic.level if mic else 0)
        self.loop_meter.set(loop.level if loop else 0)
        self.after(100, self._tick)

    def _ready_status(self):
        self._set_status(f"Ready • {self.engine.name}", f"Running on {self.engine.device_desc}",
                         "#3DBE7A" if "GPU" == self.engine.device_desc else "#E0B030")

    # ------------------------------------------------------------ model
    def _load_model_async(self):
        if self.loading:
            return
        self.loading = True
        threading.Thread(target=self._load_model, daemon=True).start()

    def _load_model(self):
        name = self.settings["model"]
        device = self.settings["device"].lower()
        self._set_status(f"Loading {name}…", "First use downloads the model - this can take a few minutes.")
        try:
            self.engine.load(name, device)
            self._ready_status()
        except Exception as e:
            self._set_status("Model not loaded", "Check Settings, then try again.", "#E5534B")
            self.ui_queue.put(("error", f"Could not load the {name} model:\n\n{e}"))
        finally:
            self.loading = False

    def _language(self):
        return LANGUAGES.get(self.settings["language"])

    def _line(self, start, text):
        return f"[{fmt_ts(start)}] {text}" if self.settings["timestamps"] else text

    def _check_ready(self):
        if self.loading or not self.engine.ready:
            messagebox.showinfo("Please wait", "The speech model is still loading.")
            return False
        return True

    # ------------------------------------------------------------ file transcription
    def _choose_file(self):
        if self.busy or self.live_running or not self._check_ready():
            return
        path = filedialog.askopenfilename(
            title="Choose a recording",
            filetypes=[("Audio and video", "*.mp3 *.wav *.m4a *.mp4 *.mkv *.webm *.ogg *.flac "
                                           "*.aac *.wma *.mov *.avi"), ("All files", "*.*")])
        if not path:
            return
        self.file_label.configure(text=Path(path).name)
        self.file_progress.set(0)
        self.file_btn.configure(state="disabled")
        self.live_btn.configure(state="disabled")
        threading.Thread(target=self._transcribe_file, args=(path,), daemon=True).start()

    def _transcribe_file(self, path):
        self.busy = True
        self.file_segments = []
        name = Path(path).name
        self.ui_queue.put(("file", f"━━━  {name}  ━━━"))
        self._set_status("Transcribing file…", name)
        started = time.time()
        try:
            for s, e, text, duration in self.engine.transcribe_stream(
                    path, self._language(), self.settings["beam_size"]):
                self.file_segments.append((s, e, text))
                self.ui_queue.put(("file", self._line(s, text)))
                self.ui_queue.put(("progress", min(1.0, e / duration)))
            self.ui_queue.put(("progress", 1.0))
            saved = self._write_transcript(Path(name).stem, self.file_segments, srt=True)
            self.ui_queue.put(("file", f"\n✓ Done in {time.time() - started:.0f}s • saved as {saved.name}\n"))
        except Exception as e:
            self.ui_queue.put(("error", f"Could not transcribe {name}:\n\n{e}"))
        finally:
            self.busy = False
            self._ready_status()
            self.ui_queue.put(("file_done", None))

    def _write_transcript(self, stem, segments, srt=False):
        folder = Path(self.settings["output_dir"])
        folder.mkdir(parents=True, exist_ok=True)
        txt = folder / f"{stem}.txt"
        txt.write_text("\n".join(self._line(s, t) for s, e, t in segments) + "\n", encoding="utf-8")
        if srt:
            (folder / f"{stem}.srt").write_text(self._srt(segments), encoding="utf-8")
        return txt

    @staticmethod
    def _srt(segments):
        return "".join(f"{i}\n{fmt_ts(s, True)} --> {fmt_ts(e, True)}\n{t}\n\n"
                       for i, (s, e, t) in enumerate(segments, 1))

    # ------------------------------------------------------------ live transcription
    def _refresh_devices(self):
        if engine.pyaudio is None:
            self.mic_menu.configure(values=["Audio library missing - run setup.bat"])
            self.mic_menu.set("Audio library missing - run setup.bat")
            return
        try:
            mics, loops, dmic, dloop = engine.list_devices()
        except Exception as e:
            messagebox.showerror("Audio devices", f"Could not list audio devices:\n{e}")
            return
        self.mic_devices = {d["name"]: d for d in mics}
        self.loop_devices = {NO_SPEAKER: None}
        self.loop_devices.update({d["name"]: d for d in loops})
        self.mic_menu.configure(values=list(self.mic_devices) or ["No microphone found"])
        self.loop_menu.configure(values=list(self.loop_devices))

        saved_mic, saved_spk = self.settings.get("mic"), self.settings.get("speaker")
        mic = saved_mic if saved_mic in self.mic_devices else next(
            (n for n, d in self.mic_devices.items() if d["index"] == dmic),
            next(iter(self.mic_devices), "No microphone found"))
        spk = saved_spk if saved_spk in self.loop_devices else next(
            (n for n, d in self.loop_devices.items() if d and d["index"] == dloop), NO_SPEAKER)
        self.mic_menu.set(mic)
        self.loop_menu.set(spk)

    def _toggle_live(self):
        if self.live_running:
            self.live_running = False
            self.live_btn.configure(text="Finishing…", state="disabled")
            return
        if self.busy or not self._check_ready():
            return
        mic = self.mic_devices.get(self.mic_menu.get())
        loop = self.loop_devices.get(self.loop_menu.get())
        if mic is None:
            messagebox.showerror("No microphone", "Choose a microphone first.")
            return
        try:
            self.pa = engine.pyaudio.PyAudio()
            self.sources = [engine.AudioSource(self.pa, mic)]
            if loop is not None:
                self.sources.append(engine.AudioSource(self.pa, loop))
            for s in self.sources:
                s.start()
        except Exception as e:
            self._stop_audio()
            messagebox.showerror("Audio error", f"Could not open the audio device:\n\n{e}")
            return

        self.live_stamp = datetime.now().strftime("Meeting %Y-%m-%d %H-%M")
        self.live_wav = None
        if self.settings["save_recordings"]:
            rec_dir = Path(self.settings["output_dir"]) / "recordings"
            rec_dir.mkdir(parents=True, exist_ok=True)
            self.live_wav = rec_dir / f"{self.live_stamp}.wav"
            self.wav_writer = wave.open(str(self.live_wav), "wb")
            self.wav_writer.setnchannels(1)
            self.wav_writer.setsampwidth(2)
            self.wav_writer.setframerate(SAMPLE_RATE)

        self.live_running = True
        self.live_start = time.time()
        self.live_btn.configure(text="■  Stop recording", fg_color=("gray30", "gray25"),
                                hover_color=("gray25", "gray20"))
        self.file_btn.configure(state="disabled")
        self.ui_queue.put(("live", f"━━━  {self.live_stamp}  ━━━"))
        self.live_thread = threading.Thread(target=self._live_loop, daemon=True)
        self.live_thread.start()

    def _live_loop(self):
        chunk = SAMPLE_RATE * int(self.settings["chunk_seconds"])
        mic = self.sources[0]
        loop = self.sources[1] if len(self.sources) > 1 else None
        elapsed, prompt, segments = 0.0, None, []
        self._set_status("● Recording", f"{self.engine.name} on {self.engine.device_desc}", "#E5534B")
        try:
            while True:
                stopping = not self.live_running
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
                if self.wav_writer is not None:
                    self.wav_writer.writeframes(
                        (np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())

                if float(np.sqrt(np.mean(audio ** 2))) > 0.003:
                    segs, _ = self.engine.transcribe(
                        audio, self._language(), self.settings["beam_size"], prompt,
                        condition_on_previous_text=False)
                    text = " ".join(t for _, _, t in segs).strip()
                    if text:
                        segments.append((elapsed, elapsed + len(audio) / SAMPLE_RATE, text))
                        self.ui_queue.put(("live", self._line(elapsed, text)))
                        self._write_transcript(self.live_stamp, segments)  # saved as you go
                        prompt = text[-200:]
                elapsed += len(audio) / SAMPLE_RATE
                if stopping:
                    break
        except Exception as e:
            self.ui_queue.put(("error", f"Live transcription stopped:\n\n{e}"))
        finally:
            self._stop_audio()
            self.live_running = False
            self.live_start = None
            msg = f"\n✓ Saved transcript: {self.live_stamp}.txt"
            if self.live_wav:
                msg += ("\n✓ Saved audio in the recordings folder - transcribe it on the "
                        "'Transcribe file' page for a cleaner final copy.")
            self.ui_queue.put(("live", msg + "\n"))
            self._ready_status()
            self.ui_queue.put(("live_stopped", None))

    def _stop_audio(self):
        for s in self.sources:
            s.stop()
        self.sources = []
        if self.pa is not None:
            try:
                self.pa.terminate()
            except Exception:
                pass
            self.pa = None
        if self.wav_writer is not None:
            try:
                self.wav_writer.close()
            except Exception:
                pass
            self.wav_writer = None

    # ------------------------------------------------------------ misc
    def _copy(self, box):
        self.clipboard_clear()
        self.clipboard_append(box.get("1.0", "end").strip())
        self._flash("Copied to clipboard")

    def _save_as(self, box, srt=False):
        content = box.get("1.0", "end").strip()
        if not content:
            return
        types = [("Text file", "*.txt")]
        if srt and self.file_segments:
            types.append(("Subtitles", "*.srt"))
        path = filedialog.asksaveasfilename(initialdir=self.settings["output_dir"],
                                            defaultextension=".txt", filetypes=types)
        if not path:
            return
        if path.lower().endswith(".srt"):
            Path(path).write_text(self._srt(self.file_segments), encoding="utf-8")
        else:
            Path(path).write_text(content, encoding="utf-8")
        self._flash(f"Saved {Path(path).name}")

    def _flash(self, msg):
        old = (self.status_title.cget("text"), self.status_sub.cget("text"),
               self.status_dot.cget("text_color"))
        self.status_sub.configure(text=msg)
        self.after(2500, lambda: self.status_sub.configure(text=old[1])
                   if self.status_sub.cget("text") == msg else None)

    def _on_close(self):
        if self.live_running:
            if not messagebox.askyesno("Recording in progress",
                                       "A meeting is being recorded. Stop and close?"):
                return
            self.live_running = False
            if self.live_thread:
                self.live_thread.join(timeout=30)
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
