# Meeting Transcriber

A local, offline Windows desktop app that turns meetings and recordings into text using
[faster-whisper](https://github.com/SYSTRAN/faster-whisper) (OpenAI Whisper models running
on CTranslate2).

- **Live meeting** – records your microphone *and* whatever plays through your speakers/headset
  (the other people on the call) via WASAPI loopback, and transcribes as you go.
- **Transcribe file** – drop in any audio or video file (mp3, m4a, wav, mp4, mkv, webm, ogg,
  flac…); a `.txt` and `.srt` are saved automatically.
- **History** – browse, read, copy and export every saved transcript.
- **Settings** – model, GPU/CPU, language, update speed, quality, save folder, light/dark theme.

Nothing is sent to the cloud. The only network access is a one-time model download from
Hugging Face on first launch.

---

## Minimum PC specs

The app is Windows-only (it uses WASAPI loopback capture and `.bat` launchers).

| | Minimum (CPU only) | Minimum (GPU) | Recommended |
|---|---|---|---|
| **OS** | Windows 10 64-bit | Windows 10 64-bit | Windows 11 64-bit |
| **CPU** | 4-core x64 (e.g. Intel i5 8th gen / Ryzen 5 2600) | 4-core x64 | 8-core (e.g. Ryzen 7 5700X / Intel i7 12th gen) |
| **RAM** | 8 GB | 8 GB | 16 GB+ |
| **GPU** | Not required | NVIDIA, CUDA 12 capable, **4 GB VRAM** (GTX 1650 / RTX 2060 class) | NVIDIA RTX, **8 GB VRAM** (RTX 3060 Ti or better) |
| **GPU driver** | – | NVIDIA driver 528 or newer | Latest NVIDIA driver |
| **Disk space** | ~3 GB free | ~8 GB free | 10 GB+ free on an SSD |
| **Python** | 3.12 (64-bit) | 3.12 (64-bit) | 3.12 (64-bit) |
| **Audio** | Any microphone | Any microphone | Headset (avoids echo of the other speakers) |
| **Model to pick** | `small` or `base` | `large-v3-turbo` | `large-v3-turbo` (or `large-v3`) |

Notes:

- **AMD and Intel GPUs are not used** – the app falls back to CPU automatically. CPU mode works
  but live transcription with the large models will lag behind on most machines; use `small`
  or `base` for live meetings on CPU.
- **Disk space** breaks down roughly as: Python environment ~1 GB (CPU) or ~4–5 GB with the
  NVIDIA CUDA/cuDNN libraries, plus the model (`base` ~150 MB, `small` ~500 MB, `medium`
  ~1.5 GB, `large-v3-turbo` ~1.6 GB, `large-v3` ~3 GB). Saved recordings (`.wav`) take about
  115 MB per hour.
- **VRAM by model** (float16, approximate): `small` ~1 GB, `medium` ~2.5 GB,
  `large-v3-turbo` ~3 GB, `large-v3` ~5 GB. If you have less, pick a smaller model or switch to CPU.

---

## Install

1. Install **Python 3.12** from [python.org](https://www.python.org/downloads/) and tick
   **"Add python.exe to PATH"** during install.
2. Clone or unzip this folder somewhere permanent, e.g. `Documents\MeetingTranscriber`.
   ```bash
   git clone <repo-url> MeetingTranscriber
   ```
3. Double-click **`setup.bat`** (one-off, a few minutes). It creates a `.venv`, installs the
   dependencies from `requirements.txt` and adds a **Meeting Transcriber** shortcut to your desktop.
4. Open the app from the desktop shortcut. The first launch downloads the speech model from
   Hugging Face; the sidebar shows **"Ready – Running on GPU"** (or CPU) when it's done.

After that the app works fully offline.

## Usage

- **Live meeting:** pick your microphone and the speakers/headset the meeting plays through,
  then press **Start recording**. Green bars show each source is picking up sound.
- **Transcribe file:** choose an audio or video file; the transcript appears as it's decoded.
- Transcripts are saved to the `transcripts/` folder by default (changeable in Settings).
  Live recordings are also kept as `.wav` in `transcripts/recordings/` if *Save recordings* is on.

**Let people know you're recording.**

## Project layout

| File | Purpose |
|---|---|
| `app.py` | CustomTkinter UI (pages, settings, history) |
| `engine.py` | Whisper model loading (GPU → CPU fallback), file decoding, WASAPI audio capture |
| `requirements.txt` | Python dependencies |
| `setup.bat` | Creates the virtual env, installs deps, makes the desktop shortcut |
| `run.bat` | Runs the app with a console window (useful for seeing errors) |
| `icon.ico` | App icon |

Not in the repo (see `.gitignore`): the `.venv`, downloaded model files, your `transcripts/`
and recordings, and `settings.json` (created on first run with your own paths and devices).
Models are cached by Hugging Face in `%USERPROFILE%\.cache\huggingface\hub`.

## Troubleshooting

- **Something fails:** open the app with `run.bat` instead – the console window shows the error.
- **Sidebar says "CPU (GPU unavailable)":** update your NVIDIA driver and run `setup.bat` again.
- **Moved the folder?** Run `setup.bat` again so the desktop shortcut points to the new place.
- **No speaker audio in live mode:** make sure you selected the output device the meeting is
  actually playing through (e.g. your headset, not the monitor speakers).
