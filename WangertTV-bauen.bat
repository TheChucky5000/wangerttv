@echo off
rem ============================================================================
rem  WangertTV - ISO bauen unter Windows (Doppelklick genuegt)
rem
rem  Voraussetzungen:
rem   - Docker Desktop (https://www.docker.com/products/docker-desktop/) installiert
rem   - ca. 20 GB freier Speicher, Internetverbindung
rem   - Projektordner NICHT in OneDrive (z. B. C:\WangertTV verwenden)
rem
rem  Aufruf:
rem   WangertTV-bauen.bat            ISO bauen (mit Fortschritt in Schritten 1-7)
rem   WangertTV-bauen.bat details    ISO bauen und dabei ALLE Meldungen anzeigen
rem   WangertTV-bauen.bat deb        nur das WangertTV-Paket (.deb) bauen
rem
rem  Ergebnis: Ordner "out" -> wangerttv-<Version>-amd64.iso
rem ============================================================================
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title WangertTV - ISO bauen
set "VERBOSE="
set "TARGET=__all_inside"
if /i "%~1"=="details" set "VERBOSE=1"
if /i "%~1"=="deb" set "TARGET=__deb_inside"

echo.
echo   ===================================================
echo      WangertTV - Betriebssystem fuer die TV-Box bauen
echo   ===================================================
echo.

if not exist "build.sh" (
    echo   FEHLER: build.sh nicht gefunden.
    echo   Bitte die .bat im entpackten WangertTV-Projektordner starten.
    goto :ende_fehler
)
for /f "usebackq delims=" %%v in ("VERSION") do set "VERSION=%%v"
echo   Version: %VERSION%
echo   Ordner:  %CD%
echo.

rem ------------------------------------------------------------ Docker pruefen
where docker >nul 2>&1
if errorlevel 1 goto :kein_docker

docker info >nul 2>&1
if not errorlevel 1 goto :docker_ok

echo   Docker Desktop laeuft noch nicht - wird gestartet ...
if exist "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
set /a VERSUCHE=0
:warte_docker
timeout /t 5 /nobreak >nul
docker info >nul 2>&1
if not errorlevel 1 goto :docker_ok
set /a VERSUCHE+=1
<nul set /p "=."
if %VERSUCHE% lss 48 goto :warte_docker
echo.
echo   FEHLER: Docker Desktop antwortet nicht. Bitte Docker Desktop oeffnen,
echo   warten bis "Engine running" angezeigt wird, und diese Datei erneut starten.
goto :ende_fehler

:docker_ok
echo   Docker laeuft.
echo.
if not exist "out" mkdir "out"

rem ------------------------------------------------------------ Bauen
echo   Debian-13-Basis wird geladen ...
docker pull -q debian:trixie >nul
if errorlevel 1 (
    echo   FEHLER: Das Debian-Abbild konnte nicht geladen werden. Internetverbindung pruefen.
    goto :ende_fehler
)

echo   Start: %date% %time:~0,5% Uhr
if "%TARGET%"=="__all_inside" echo   Der ISO-Build dauert ca. 20-40 Minuten. Der Fortschritt erscheint unten in Schritten 1-7.
echo   Das Fenster bitte offen lassen. Abbrechen mit Strg+C.
echo.

docker run --rm -t --privileged -e WTV_VERBOSE=%VERBOSE% -v "%CD%:/src" -w /src debian:trixie bash -c "sed -i 's/\r$//' build.sh && bash build.sh %TARGET%"
set "RC=%errorlevel%"

echo.
echo   Ende: %date% %time:~0,5% Uhr
if not "%RC%"=="0" (
    echo.
    echo   FEHLER: Der Build ist fehlgeschlagen ^(Code %RC%^).
    if exist "out\build-iso.log" echo   Protokoll: %CD%\out\build-iso.log
    goto :ende_fehler
)

echo.
echo   ===================================================
echo      FERTIG!
echo   ===================================================
if "%TARGET%"=="__all_inside" (
    echo   ISO-Datei: %CD%\out\wangerttv-%VERSION%-amd64.iso
    echo.
    echo   Naechster Schritt: ISO mit Rufus ^(Modus "DD-Image"^) oder balenaEtcher
    echo   auf einen USB-Stick schreiben, Geekom mit F7 vom Stick starten.
) else (
    echo   Paket: %CD%\out\wangerttv_%VERSION%_all.deb
)
start "" explorer "%CD%\out"
echo.
pause
exit /b 0

:kein_docker
echo   FEHLER: Docker ist nicht installiert.
echo   1. Docker Desktop herunterladen: https://www.docker.com/products/docker-desktop/
echo   2. Installieren ^(WSL 2 aktiviert lassen^), PC neu starten, Docker Desktop einmal oeffnen.
echo   3. Diese Datei erneut starten.
start "" "https://www.docker.com/products/docker-desktop/"

:ende_fehler
echo.
pause
exit /b 1
