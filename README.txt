MEETING TRANSCRIBER - quick start
=================================

1. Install Python 3.12 from python.org (tick "Add python.exe to PATH").
2. Unzip this folder somewhere permanent, e.g. Documents\MeetingTranscriber.
3. Double-click setup.bat (one-off, a few minutes). It adds a "Meeting Transcriber"
   icon to your desktop.
4. Open the app from the desktop icon. First launch downloads the speech model
   from Hugging Face (about 1.6 GB); the status in the top-right corner turns green ("Ready") when done.

PAGES
- Live meeting: pick your microphone and the speakers/headset the meeting plays through,
  then press Start recording. Green bars show each source is picking up sound.
- Transcribe file: drag an audio or video file onto the window (or browse); a .txt and .srt are saved automatically.
- History: every saved transcript, click to read, copy or export.
- Settings: model, GPU/CPU, language, update speed, quality, save folder, light/dark theme.

UPDATING
- Close the app, pull the new code (or copy the new files over this folder), run setup.bat again.
  Your transcripts and settings are kept.

TROUBLESHOOTING
- If something fails, open the app with run.bat instead - the black window shows the error.
- If the status says "CPU (GPU unavailable)", update your NVIDIA driver and run setup.bat again.
- Moved the folder? Run setup.bat again so the desktop icon points to the new place.
- Let people know you're recording.
