@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
pushd "%~dp0"
if errorlevel 1 exit /b 1
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONUTF8=1"
set "codigo=1"
if not exist "executar_windows.py" goto incompleto
if not exist ".venv\Scripts\python.exe" goto sem_instalacao
".venv\Scripts\python.exe" -I -B "executar_windows.py" iniciar %*
set "codigo=%errorlevel%"
goto finalizar

:sem_instalacao
echo ERRO: execute primeiro INSTALAR.bat para instalar as bibliotecas.
goto finalizar

:incompleto
echo ERRO: faltam arquivos. Extraia a pasta TCC completa antes de iniciar.

:finalizar
popd
echo.
if not defined TCC_BANCA_SEM_PAUSA pause
exit /b %codigo%
