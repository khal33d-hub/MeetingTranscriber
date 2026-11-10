@echo off
cd /d "%~dp0"
echo === Meeting Transcriber setup ===
where py >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python 3.12 from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during install, then run this again.
  pause
  exit /b 1
)
py -3.12 -m venv .venv 2>nul || py -3 -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
echo.
echo Creating desktop shortcut...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Meeting Transcriber.lnk'); $s.TargetPath='%~dp0.venv\Scripts\pythonw.exe'; $s.Arguments='\"%~dp0app.py\"'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%~dp0icon.ico'; $s.Description='Meeting Transcriber'; $s.Save()"
echo.
echo Setup complete. Open "Meeting Transcriber" from your desktop.
pause
