@echo off
rem Double-click friendly wrapper for scripts\build-exe.ps1.
rem Usage: build-exe.cmd [-Version 1.0.2] [-Bundles nsis] [-SkipFrontendInstall] [-SkipOllama]
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build-exe.ps1" %*
exit /b %ERRORLEVEL%
