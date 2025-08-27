@echo off
setlocal enabledelayedexpansion

REM --- Configuration ---
set REPO_URL_HAPI=https://github.com/hapifhir/hapi-fhir-jpaserver-starter.git
set REPO_URL_CANDLE=https://github.com/FHIR/fhir-candle.git
set CLONE_DIR_HAPI=hapi-fhir-jpaserver
set CLONE_DIR_CANDLE=fhir-candle
set SOURCE_CONFIG_DIR=hapi-fhir-setup
set CONFIG_FILE=application.yaml

REM --- Define Paths ---
set SOURCE_CONFIG_PATH=..\%SOURCE_CONFIG_DIR%\target\classes\%CONFIG_FILE%
set DEST_CONFIG_PATH=%CLONE_DIR_HAPI%\target\classes\%CONFIG_FILE%

REM --- NEW: Define a variable for the custom FHIR URL and server type ---
set "CUSTOM_FHIR_URL_VAL="
set "SERVER_TYPE="
set "CANDLE_FHIR_VERSION="

REM === MODIFIED: Prompt for Installation Mode ===
:GetModeChoice
SET "APP_MODE=" REM Clear the variable first
echo.
echo Select Installation Mode:
echo 1. Lite (Excludes local HAPI FHIR Server - No Git/Maven/Dotnet needed)
echo 2. Custom URL (Uses a custom FHIR Server - No Git/Maven/Dotnet needed)
echo 3. Hapi (Includes local HAPI FHIR Server - Requires Git & Maven)
echo 4. Candle (Includes local FHIR Candle Server - Requires Git & Dotnet)
CHOICE /C 1234 /N /M "Enter your choice (1, 2, 3, or 4):"

IF ERRORLEVEL 4 (
    SET APP_MODE=standalone
    SET SERVER_TYPE=candle
    goto :GetCandleFhirVersion
)
IF ERRORLEVEL 3 (
    SET APP_MODE=standalone
    SET SERVER_TYPE=hapi
    goto :ModeSet
)
IF ERRORLEVEL 2 (
    SET APP_MODE=standalone
    goto :GetCustomUrl
)
IF ERRORLEVEL 1 (
    SET APP_MODE=lite
    goto :ModeSet
)

REM If somehow neither was chosen (e.g., Ctrl+C), loop back
echo Invalid input. Please try again.
goto :GetModeChoice

:GetCustomUrl
set "CONFIRMED_URL="
:PromptUrlLoop
echo.
set /p "CUSTOM_URL_INPUT=Please enter the custom FHIR server URL: "
echo.
echo You entered: !CUSTOM_URL_INPUT!
set /p "CONFIRM_URL=Is this URL correct? (Y/N): "
if /i "!CONFIRM_URL!" EQU "Y" (
    set "CONFIRMED_URL=!CUSTOM_URL_INPUT!"
    goto :ConfirmUrlLoop
) else (
    goto :PromptUrlLoop
)
:ConfirmUrlLoop
echo.
echo Please re-enter the URL to confirm it is correct:
set /p "CUSTOM_URL_INPUT=Re-enter URL: "
if /i "!CUSTOM_URL_INPUT!" EQU "!CONFIRMED_URL!" (
    set "CUSTOM_FHIR_URL_VAL=!CUSTOM_URL_INPUT!"
    echo.
    echo Custom URL confirmed: !CUSTOM_FHIR_URL_VAL!
    goto :ModeSet
) else (
    echo.
    echo URLs do not match. Please try again.
    goto :PromptUrlLoop
)

:GetCandleFhirVersion
echo.
echo Select the FHIR version for the Candle server:
echo 1. R4 (4.0)
echo 2. R4B (4.3)
echo 3. R5 (5.0)
CHOICE /C 123 /N /M "Enter your choice (1, 2, or 3):"
IF ERRORLEVEL 3 (
    SET CANDLE_FHIR_VERSION=r5
    goto :ModeSet
)
IF ERRORLEVEL 2 (
    SET CANDLE_FHIR_VERSION=r4b
    goto :ModeSet
)
IF ERRORLEVEL 1 (
    SET CANDLE_FHIR_VERSION=r4
    goto :ModeSet
)
echo Invalid input. Please try again.
goto :GetCandleFhirVersion

:ModeSet
IF "%APP_MODE%"=="" (
    echo Invalid choice detected after checks. Exiting.
    goto :eof
)
echo Selected Mode: %APP_MODE%
echo Server Type: %SERVER_TYPE%
echo.
REM === END MODIFICATION ===


REM === Conditionally Execute Server Setup ===
IF "%SERVER_TYPE%"=="hapi" (
    echo Running Hapi server setup...
    echo.
    
    REM --- Step 0: Clean up previous clone (optional) ---
    echo Checking for existing directory: %CLONE_DIR_HAPI%
    if exist "%CLONE_DIR_HAPI%" (
        echo Found existing directory, removing it...
        rmdir /s /q "%CLONE_DIR_HAPI%"
        if errorlevel 1 (
            echo ERROR: Failed to remove existing directory: %CLONE_DIR_HAPI%
            goto :error
        )
        echo Existing directory removed.
    ) else (
        echo Directory does not exist, proceeding with clone.
    )
    echo.

    REM --- Step 1: Clone the HAPI FHIR server repository ---
    echo Cloning repository: %REPO_URL_HAPI% into %CLONE_DIR_HAPI%...
    git clone "%REPO_URL_HAPI%" "%CLONE_DIR_HAPI%"
    if errorlevel 1 (
        echo ERROR: Failed to clone repository. Check Git installation and network connection.
        goto :error
    )
    echo Repository cloned successfully.
    echo.

    REM --- Step 2: Navigate into the cloned directory ---
    echo Changing directory to %CLONE_DIR_HAPI%...
    cd "%CLONE_DIR_HAPI%"
    if errorlevel 1 (
        echo ERROR: Failed to change directory to %CLONE_DIR_HAPI%.
        goto :error
    )
    echo Current directory: %CD%
    echo.

    REM --- Step 3: Build the HAPI server using Maven ---
    echo ===> "Starting Maven build (Step 3)...""
    cmd /c "mvn clean package -DskipTests=true -Pboot"
    echo ===> Maven command finished. Checking error level...
    if errorlevel 1 (
        echo ERROR: Maven build failed or cmd /c failed
        cd ..
        goto :error
    )
    echo Maven build completed successfully. ErrorLevel: %errorlevel%
    echo.
    
    REM --- Step 4: Copy the configuration file ---
    echo ===> "Starting file copy (Step 4)..."
    echo Copying configuration file...
    echo Source: %SOURCE_CONFIG_PATH%
    echo Destination: target\classes\%CONFIG_FILE%
    xcopy "%SOURCE_CONFIG_PATH%" "target\classes\" /Y /I
    echo ===> xcopy command finished. Checking error level...
    if errorlevel 1 (
        echo WARNING: Failed to copy configuration file. Check if the source file exists.
        echo The script will continue, but the server might use default configuration.
    ) else (
        echo Configuration file copied successfully. ErrorLevel: %errorlevel%
    )
    echo.

    REM --- Step 5: Navigate back to the parent directory ---
    echo ===> "Changing directory back (Step 5)..."
    cd ..
    if errorlevel 1 (
        echo ERROR: Failed to change back to the parent directory. ErrorLevel: %errorlevel%
        goto :error
    )
    echo Current directory: %CD%
    echo.

) ELSE IF "%SERVER_TYPE%"=="candle" (
    echo Running FHIR Candle server setup...
    echo.

    REM --- Step 0: Clean up previous clone (optional) ---
    echo Checking for existing directory: %CLONE_DIR_CANDLE%
    if exist "%CLONE_DIR_CANDLE%" (
        echo Found existing directory, removing it...
        rmdir /s /q "%CLONE_DIR_CANDLE%"
        if errorlevel 1 (
            echo ERROR: Failed to remove existing directory: %CLONE_DIR_CANDLE%
            goto :error
        )
        echo Existing directory removed.
    ) else (
        echo Directory does not exist, proceeding with clone.
    )
    echo.

    REM --- Step 1: Clone the FHIR Candle server repository ---
    echo Cloning repository: %REPO_URL_CANDLE% into %CLONE_DIR_CANDLE%...
    git clone "%REPO_URL_CANDLE%" "%CLONE_DIR_CANDLE%"
    if errorlevel 1 (
        echo ERROR: Failed to clone repository. Check Git and Dotnet installation and network connection.
        goto :error
    )
    echo Repository cloned successfully.
    echo.
    
    REM --- Step 2: Navigate into the cloned directory ---
    echo Changing directory to %CLONE_DIR_CANDLE%...
    cd "%CLONE_DIR_CANDLE%"
    if errorlevel 1 (
        echo ERROR: Failed to change directory to %CLONE_DIR_CANDLE%.
        goto :error
    )
    echo Current directory: %CD%
    echo.

    REM --- Step 3: Build the FHIR Candle server using Dotnet ---
    echo ===> "Starting Dotnet build (Step 3)...""
    dotnet publish -c Release -f net9.0 -o publish
    echo ===> Dotnet command finished. Checking error level...
    if errorlevel 1 (
        echo ERROR: Dotnet build failed. Check Dotnet SDK installation.
        cd ..
        goto :error
    )
    echo Dotnet build completed successfully. ErrorLevel: %errorlevel%
    echo.
    
    REM --- Step 4: Navigate back to the parent directory ---
    echo ===> "Changing directory back (Step 4)..."
    cd ..
    if errorlevel 1 (
        echo ERROR: Failed to change back to the parent directory. ErrorLevel: %errorlevel%
        goto :error
    )
    echo Current directory: %CD%
    echo.

) ELSE (
    echo Running Lite setup, skipping server build...
    REM Ensure the server directories don't exist in Lite mode
    if exist "%CLONE_DIR_HAPI%" (
       echo Found existing HAPI directory in Lite mode. Removing it to avoid build issues...
       rmdir /s /q "%CLONE_DIR_HAPI%"
    )
    if exist "%CLONE_DIR_CANDLE%" (
       echo Found existing Candle directory in Lite mode. Removing it to avoid build issues...
       rmdir /s /q "%CLONE_DIR_CANDLE%"
    )
    REM Create empty placeholder files to satisfy Dockerfile COPY commands in Lite mode.
    mkdir "%CLONE_DIR_HAPI%\target\classes" 2> nul
    mkdir "%CLONE_DIR_HAPI%\custom" 2> nul
    echo. > "%CLONE_DIR_HAPI%\target\ROOT.war"
    echo. > "%CLONE_DIR_HAPI%\target\classes\application.yaml"
    mkdir "%CLONE_DIR_CANDLE%\publish" 2> nul
    echo. > "%CLONE_DIR_CANDLE%\publish\fhir-candle.dll"
    echo Placeholder files created for Lite mode build.
    echo.
)

REM === MODIFIED: Update docker-compose.yml to set APP_MODE and HAPI_FHIR_URL ===
echo Updating docker-compose.yml with APP_MODE=%APP_MODE% and HAPI_FHIR_URL...
(
  echo version: '3.8'
  echo services:
  echo   fhirflare:
  echo     build:
  echo       context: .
  IF "%SERVER_TYPE%"=="hapi" (
        echo       dockerfile: Dockerfile.hapi
    ) ELSE IF "%SERVER_TYPE%"=="candle" (
        echo       dockerfile: Dockerfile.candle
    ) ELSE (
        echo       dockerfile: Dockerfile.lite
  )
  echo     ports:
  IF "%SERVER_TYPE%"=="candle" (
    echo       - "5000:5000"
    echo       - "5001:5826"
  ) ELSE (
    echo       - "5000:5000"
    echo       - "8080:8080"
  )
  echo     volumes:
  echo       - ./instance:/app/instance
  echo       - ./static/uploads:/app/static/uploads
  IF "%SERVER_TYPE%"=="hapi" (
    echo       - ./instance/hapi-h2-data/:/app/h2-data # Keep volume mounts consistent
  )
  echo       - ./logs:/app/logs
  IF "%SERVER_TYPE%"=="hapi" (
    echo       - ./hapi-fhir-jpaserver/target/ROOT.war:/usr/local/tomcat/webapps/ROOT.war
    echo       - ./hapi-fhir-jpaserver/target/classes/application.yaml:/usr/local/tomcat/conf/application.yaml
  ) ELSE IF "%SERVER_TYPE%"=="candle" (
    echo       - ./fhir-candle/publish/:/app/fhir-candle-publish/
  )
  echo     environment:
  echo       - FLASK_APP=app.py
  echo       - FLASK_ENV=development
  echo       - NODE_PATH=/usr/lib/node_modules
  echo       - APP_MODE=%APP_MODE%
  echo       - APP_BASE_URL=http://localhost:5000
  IF DEFINED CUSTOM_FHIR_URL_VAL (
    echo       - HAPI_FHIR_URL=!CUSTOM_FHIR_URL_VAL!
  ) ELSE (
    IF "%SERVER_TYPE%"=="candle" (
        echo       - HAPI_FHIR_URL=http://localhost:5826/fhir/%CANDLE_FHIR_VERSION%
        echo       - ASPNETCORE_URLS=http://0.0.0.0:5826
    ) ELSE (
        echo       - HAPI_FHIR_URL=http://localhost:8080/fhir
    )
  )
  echo     command: supervisord -c /etc/supervisord.conf
) > docker-compose.yml.tmp

REM Check if docker-compose.yml.tmp was created successfully
if not exist docker-compose.yml.tmp (
    echo ERROR: Failed to create temporary docker-compose file.
    goto :error
)

REM Replace the original docker-compose.yml
del docker-compose.yml /Q > nul 2>&1
ren docker-compose.yml.tmp docker-compose.yml
echo docker-compose.yml updated successfully.
echo.

REM --- Step 6: Build Docker images ---
echo ===> Starting Docker build (Step 6)...
docker-compose build --no-cache
if errorlevel 1 (
    echo ERROR: Docker Compose build failed. Check Docker installation and docker-compose.yml file. ErrorLevel: %errorlevel%
    goto :error
)
echo Docker images built successfully. ErrorLevel: %errorlevel%
echo.

REM --- Step 7: Start Docker containers ---
echo ===> Starting Docker containers (Step 7)...
docker-compose up -d
if errorlevel 1 (
    echo ERROR: Docker Compose up failed. Check Docker installation and container configurations. ErrorLevel: %errorlevel%
    goto :error
)
echo Docker containers started successfully. ErrorLevel: %errorlevel%
echo.

echo ====================================
echo Script finished successfully! (Mode: %APP_MODE%)
echo ====================================
goto :eof

:error
echo ------------------------------------
echo An error occurred. Script aborted.
echo ------------------------------------
pause
exit /b 1

:eof
echo Script execution finished.
pause
