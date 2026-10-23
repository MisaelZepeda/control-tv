@echo off
rem Doble clic para arrancar el puente de Control TV (deja esta ventana abierta mientras uses el control).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0iniciar-puente.ps1" %*
pause
