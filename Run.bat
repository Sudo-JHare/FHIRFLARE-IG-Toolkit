REM --- Step 1: Start Docker containers ---
echo ===> Starting Docker containers (Step 7)...
docker-compose up -d
if errorlevel 1 (
    echo ERROR: Docker Compose up failed. Check Docker installation and container configurations. ErrorLevel: %errorlevel%
    goto :error
)
echo Docker containers started successfully. ErrorLevel: %errorlevel%
echo.

echo ====================================
echo Script finished successfully!
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