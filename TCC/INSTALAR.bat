@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
pushd "%~dp0"
if errorlevel 1 exit /b 1
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONUTF8=1"
set "codigo=1"
if not exist "executar_windows.py" goto incompleto
if not exist "requirements-lock.txt" goto incompleto

py -3.14 -I -B -c "import sys,struct; sys.exit(0 if sys.version_info[:3]==(3,14,6) and struct.calcsize('P')==8 else 1)" >nul 2>&1
if not errorlevel 1 goto usar_py
python -I -B -c "import sys,struct; sys.exit(0 if sys.version_info[:3]==(3,14,6) and struct.calcsize('P')==8 else 1)" >nul 2>&1
if not errorlevel 1 goto usar_python
if not exist ".venv\Scripts\python.exe" goto sem_python
".venv\Scripts\python.exe" -I -B -c "import sys,struct; sys.exit(0 if sys.version_info[:3]==(3,14,6) and struct.calcsize('P')==8 else 1)" >nul 2>&1
if not errorlevel 1 goto usar_venv

:sem_python
echo ERRO: instale Python 3.14.6 de 64 bits, conforme o README.
echo Marque Add Python to PATH e tente novamente.
goto finalizar

:usar_py
py -3.14 -I -B "executar_windows.py" instalar %*
set "codigo=%errorlevel%"
goto finalizar

:usar_python
python -I -B "executar_windows.py" instalar %*
set "codigo=%errorlevel%"
goto finalizar

:usar_venv
".venv\Scripts\python.exe" -I -B "executar_windows.py" instalar %*
set "codigo=%errorlevel%"
goto finalizar

:incompleto
echo ERRO: faltam arquivos. Extraia a pasta TCC completa antes de instalar.

:finalizar
popd
echo.
if not defined TCC_BANCA_SEM_PAUSA pause
exit /b %codigo%
