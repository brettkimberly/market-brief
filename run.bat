@echo off
:: market_brief Windows runner
:: Usage: run.bat [sunday|weekday|saturday]
::        Omit argument to auto-detect from current day.
::
:: Task Scheduler setup (run once as Admin in PowerShell):
::   Weekdays 7:45am CST:
::     schtasks /create /tn "MarketBrief_Weekday" /tr "\"C:\path\to\run.bat\"" /sc WEEKLY /d MON,TUE,WED,THU,FRI /st 07:45
::   Saturday 11:00am CST:
::     schtasks /create /tn "MarketBrief_Saturday" /tr "\"C:\path\to\run.bat\"" /sc WEEKLY /d SAT /st 11:00
::   Sunday 6:00pm CST:
::     schtasks /create /tn "MarketBrief_Sunday" /tr "\"C:\path\to\run.bat\"" /sc WEEKLY /d SUN /st 18:00

setlocal
cd /d "%~dp0"

:: Use venv if present, otherwise fall back to system Python
if exist ".venv\Scripts\python.exe" (
    set PYTHON=".venv\Scripts\python.exe"
) else (
    set PYTHON=python
)

%PYTHON% market_brief.py %1 >> market_brief.log 2>&1
endlocal
