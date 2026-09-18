@echo off
rem ---------------------------------------------------------------------------
rem  The scheduled Meta pull. Task Scheduler runs THIS, not python directly.
rem
rem  It exists because a scheduled task has no working directory, no PATH you
rem  control and no venv -- so "python scripts/sync.py" in a task definition
rem  resolves to whichever python the SYSTEM account happens to see, which is
rem  usually none, and the task fails with an exit code nobody reads.
rem
rem  Exit codes, which Task Scheduler shows as "Last Run Result":
rem      0  everything pulled
rem      1  something failed -- read logs\sync.log
rem      2  a sync was already running, so this one did nothing
rem ---------------------------------------------------------------------------

setlocal

rem %~dp0 is this file's own folder, with a trailing slash. Absolute, so the
rem task's working directory never matters.
set "ROOT=%~dp0.."
set "PY=%ROOT%\.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo Cannot find the virtualenv at "%PY%".
    echo Create it:  python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    exit /b 1
)

pushd "%ROOT%"
"%PY%" scripts\sync.py %*
set CODE=%ERRORLEVEL%
popd

exit /b %CODE%
