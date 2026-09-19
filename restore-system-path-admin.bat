@echo off
REM ===========================================================================
REM  EnvSwitch - restore the SYSTEM PATH from the automatic backup.
REM
REM  What it does : re-adds the entries that EnvSwitch removed from the
REM                 system PATH (HKLM ...\Session Manager\Environment\Path),
REM                 using ~/.envswitch/system_path_backup.json.
REM                 Entries added after the backup are kept.
REM
REM  How to run   : right-click this file -> "Run as administrator".
REM
REM  Note         : if Python is installed but not on PATH, edit PYEXE below.
REM ===========================================================================
setlocal
cd /d "%~dp0"

set "PYEXE="
where py >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE where python >nul 2>nul && set "PYEXE=python"
if not defined PYEXE if exist "C:\Python314\python.exe" set "PYEXE=C:\Python314\python.exe"
if not defined PYEXE if exist "C:\Python313\python.exe" set "PYEXE=C:\Python313\python.exe"
if not defined PYEXE if exist "C:\Python312\python.exe" set "PYEXE=C:\Python312\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Launcher\py.exe"

if not defined PYEXE (
  echo [ERROR] No Python interpreter found.
  echo         Run it manually with a full path, for example:
  echo             C:\Python314\python.exe "%~dp0main.py" systempath-restore
  echo.
  pause
  exit /b 1
)

echo Interpreter : %PYEXE%
echo Backup file : %USERPROFILE%\.envswitch\system_path_backup.json
echo.
echo Restoring system PATH ...
%PYEXE% "%~dp0main.py" systempath-restore --quiet
echo.
echo Result file : %USERPROFILE%\.envswitch\elevated_result.json
type "%USERPROFILE%\.envswitch\elevated_result.json"
echo.
echo Done. Reopen your terminal (or sign out/in) for the change to take effect.
pause
