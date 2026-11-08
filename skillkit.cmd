@echo off
setlocal DisableDelayedExpansion
python "%~dp0manage.py" %*
exit /b %errorlevel%
