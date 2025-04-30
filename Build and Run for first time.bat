@echo off
setlocal enabledelayedexpansion

REM --- Configuration ---
set REPO_URL=https://github.com/hapifhir/hapi-fhir-jpaserver-starter.git
set CLONE_DIR=hapi-fhir-jpaserver
set SOURCE_CONFIG_DIR=hapi-fhir-setup
set CONFIG_FILE=application.yaml

REM --- Define Paths ---
set SOURCE_CONFIG_PATH=..\%SOURCE_CONFIG_DIR%\target\classes\%CONFIG_FILE%
set DEST_CONFIG_PATH=%CLONE_DIR%\target\classes\%CONFIG_FILE%

REM === CORRECTED: Prompt for Version ===
:GetModeChoice
SET "APP_MODE=" REM Clear the variable first
echo Select Installation Mode:
echo 1. Standalone (Includes local HAPI FHIR Server - Requires Git & Maven)
echo 2. Lite (Excludes local HAPI FHIR Server - No Git/Maven needed)
CHOICE /C 12 /N /M "Enter your choice (1 or 2):"

IF ERRORLEVEL 2 (
    SET APP_MODE=lite
    goto :ModeSet
)
IF ERRORLEVEL 1 (
    SET APP_MODE=standalone
    goto :ModeSet
)
REM If somehow neither was chosen (e.g., Ctrl+C), loop back
echo Invalid input. Please try again.
goto :GetModeChoice

:ModeSet
IF "%APP_MODE%"=="" (
    echo Invalid choice detected after checks. Exiting.
    goto :eof
)
echo Selected Mode: %APP_MODE%
echo.
REM === END CORRECTION ===


REM === Conditionally Execute HAPI Setup ===
IF "%APP_MODE%"=="standalone" (
    echo Running Standalone setup including HAPI FHIR...
    echo.

    REM --- Step 0: Clean up previous clone (optional) ---
    echo Checking for existing directory: %CLONE_DIR%
    if exist "%CLONE_DIR%" (
        echo Found existing directory, removing it...
        rmdir /s /q "%CLONE_DIR%"
        if errorlevel 1 (
            echo ERROR: Failed to remove existing directory: %CLONE_DIR%
            goto :error
        )
        echo Existing directory removed.
    ) else (
        echo Directory does not exist, proceeding with clone.
    )
    echo.

    REM --- Step 1: Clone the HAPI FHIR server repository ---
    echo Cloning repository: %REPO_URL% into %CLONE_DIR%...
    git clone "%REPO_URL%" "%CLONE_DIR%"
    if errorlevel 1 (
        echo ERROR: Failed to clone repository. Check Git installation and network connection.
        goto :error
    )
    echo Repository cloned successfully.
    echo.

    REM --- Step 2: Navigate into the cloned directory ---
    echo Changing directory to %CLONE_DIR%...
    cd "%CLONE_DIR%"
    if errorlevel 1 (
        echo ERROR: Failed to change directory to %CLONE_DIR%.
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

) ELSE (
    echo Running Lite setup, skipping HAPI FHIR build...
    REM Ensure the hapi-fhir-jpaserver directory doesn't exist or is empty if Lite mode is chosen after a standalone attempt
    if exist "%CLONE_DIR%" (
       echo Found existing HAPI directory in Lite mode. Removing it to avoid build issues...
       rmdir /s /q "%CLONE_DIR%"
    )
    REM Create empty target directories expected by Dockerfile COPY, even if not used
    mkdir "%CLONE_DIR%\target\classes" 2> nul
    mkdir "%CLONE_DIR%\custom" 2> nul
    REM Create a placeholder empty WAR file to satisfy Dockerfile COPY
    echo. > "%CLONE_DIR%\target\ROOT.war"
    echo. > "%CLONE_DIR%\target\classes\application.yaml"
    echo Placeholder files created for Lite mode build.
    echo.
)

REM === Modify docker-compose.yml to set APP_MODE ===
echo Updating docker-compose.yml with APP_MODE=%APP_MODE%...
(
  echo version: '3.8'
  echo services:
  echo   fhirflare:
  echo     build:
  echo       context: .
  echo       dockerfile: Dockerfile
  echo     ports:
  echo       - "5000:5000"
  echo       - "8080:8080" # Keep port exposed, even if Tomcat isn't running useful stuff in Lite
  echo     volumes:
  echo       - ./instance:/app/instance
  echo       - ./static/uploads:/app/static/uploads
  echo       - ./instance/hapi-h2-data/:/app/h2-data # Keep volume mounts consistent
  echo       - ./logs:/app/logs
  echo     environment:
  echo       - FLASK_APP=app.py
  echo       - FLASK_ENV=development
  echo       - NODE_PATH=/usr/lib/node_modules
  echo       - APP_MODE=%APP_MODE% 
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