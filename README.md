# Meeting Transcriber

A local, offline Windows desktop app that turns meetings and recordings into text using
[faster-whisper](https://github.com/SYSTRAN/faster-whisper) (OpenAI Whisper models running
on CTranslate2).

- **Live meeting** – records your microphone *and* whatever plays through your speakers/headset
  (the other people on the call) via WASAPI loopback, and transcribes as you go.
- **Transcribe file** – drag any audio or video file onto the window, or browse for one (mp3,
  m4a, wav, mp4, mkv, webm, ogg, flac…); a `.txt` and `.srt` are saved automatically.
- **History** – search, read, copy and export every saved transcript.
- **Sounds** – short cues for app start, model ready / not ready yet, recording start and stop,
  a soft continuous tone while a file is transcribing, a chime when it's done, and warning/error
  sounds. Volume control and a one-click mute.
- **Settings** – model, GPU/CPU, language, update speed, quality, save folder, sounds,
  light/dark theme.

The interface is a minimal HTML/CSS/JS front end (light and dark themes) shown in a native window
by [pywebview](https://pywebview.flowrl.com/), using the Edge WebView2 engine built into Windows.

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
| **WebView2 Runtime** | Required (built into Windows 11) | Required | Required |
| **Audio** | Any microphone | Any microphone | Headset (avoids echo of the other speakers) |
| **Model to pick** | `small` or `base` | `large-v3-turbo` | `large-v3-turbo` (or `large-v3`) |

Notes:

- **WebView2** ships with Windows 11 and with current Windows 10 updates. If the window opens blank,
  install the *Evergreen Runtime* from
  [Microsoft](https://developer.microsoft.com/microsoft-edge/webview2/).
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
   Hugging Face; the status pill in the top-right corner turns green and shows **"Ready"** when it's
   done (hover it to see whether it's running on GPU or CPU).

After that the app works fully offline.

## Updating

Your transcripts, recordings and settings are not part of the repo, so updating never touches them.

1. Close the app.
2. Get the new code: **Fetch origin → Pull** in GitHub Desktop, or `git pull` in the folder.
   (If you installed from a zip, download the new zip and copy its files over the old folder.)
3. Double-click **`setup.bat`** again. It reuses the existing `.venv` and only installs what's new.
4. Open the app from the same desktop shortcut.

## Usage

- **Live meeting:** pick your microphone and the speakers/headset the meeting plays through,
  then press **Start recording**. Green bars show each source is picking up sound.
- **Transcribe file:** drop an audio or video file on the window (or click *browse*); the
  transcript appears as it's decoded.
- **Shortcuts:** `Ctrl+1`–`Ctrl+4` switch pages, `Ctrl+R` starts/stops recording,
  `Ctrl+M` mutes/unmutes sounds.

### Sounds

| Sound | When |
|---|---|
| Welcome | The app opens |
| Ready | The speech model has loaded and the app can transcribe |
| Not ready | You press record or pick a file while the model is still loading |
| Start / Stop | Recording starts / stops |
| Working (continuous, soft) | While a file is being transcribed |
| Done | A file finished transcribing |
| Warning | Something needs attention (e.g. a job is already running, closing while recording) |
| Error | Something failed (model, audio device, file) |

Sounds are generated by the app (no audio files) and play through your default output.
The start sound finishes **before** recording begins, and the stop sound plays after capture has
stopped, so neither ends up in your transcript. To record with no distractions at all, mute
everything from **Settings → Sounds**, the speaker button in the side bar, or `Ctrl+M`.
- Transcripts are saved to the `transcripts/` folder by default (changeable in Settings).
  Live recordings are also kept as `.wav` in `transcripts/recordings/` if *Save recordings* is on.

**Let people know you're recording.**

## Project layout

| File | Purpose |
|---|---|
| `app.py` | Entry point and the Python ↔ UI bridge (pywebview window, settings, live/file jobs) |
| `ui/index.html`, `ui/styles.css`, `ui/app.js` | The interface (no external assets, works offline) |
| `ui/sounds.js` | Synthesized UI sounds (Web Audio), volume and mute |
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
- **Window is blank or white:** install the Microsoft Edge WebView2 Runtime (see the specs notes above).
- **Status says "CPU (GPU unavailable)":** update your NVIDIA driver and run `setup.bat` again.
- **Moved the folder?** Run `setup.bat` again so the desktop shortcut points to the new place.
- **No speaker audio in live mode:** make sure you selected the output device the meeting is
  actually playing through (e.g. your headset, not the monitor speakers). The app records
  whatever Windows sends to that device, so headsets work like any other output.
- **Bluetooth headset shows no speaker level:** while the headset mic is in use, Windows often
  switches to a separate "Headset (Hands-Free)" device instead of "Headphones (Stereo)". Pick
  whichever entry shows green bars during the call.
- **Meeting app plays to a different device:** Teams, Zoom and Discord have their own speaker
  setting, and may use Windows' *default communications device* rather than the default output.
  Make sure it matches the device selected in the app.
- **Headset plugged in after the app opened:** press **Refresh devices** so it appears in the list.
- **Quick check:** play a YouTube video through the device and watch the speaker bar. If it
  moves, meeting audio will be captured too.
- **Duplicate lines in the transcript:** your mic is picking up the other people from your
  speakers. Use a headset so their voices only come through the speaker capture.
