@echo off
setlocal DisableDelayedExpansion
python "%~dp0manage.py" query %*
exit /b %errorlevel%
