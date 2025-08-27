#!/bin/bash

# --- Configuration ---
REPO_URL_HAPI="https://github.com/hapifhir/hapi-fhir-jpaserver-starter.git"
REPO_URL_CANDLE="https://github.com/FHIR/fhir-candle.git"
CLONE_DIR_HAPI="hapi-fhir-jpaserver"
CLONE_DIR_CANDLE="fhir-candle"
SOURCE_CONFIG_DIR="hapi-fhir-Setup"
CONFIG_FILE="application.yaml"

# --- Define Paths ---
SOURCE_CONFIG_PATH="../${SOURCE_CONFIG_DIR}/target/classes/${CONFIG_FILE}"
DEST_CONFIG_PATH="${CLONE_DIR_HAPI}/target/classes/${CONFIG_FILE}"

APP_MODE=""
CUSTOM_FHIR_URL_VAL=""
SERVER_TYPE=""
CANDLE_FHIR_VERSION=""

# --- Error Handling Function ---
handle_error() {
    echo "------------------------------------"
    echo "An error occurred: $1"
    echo "Script aborted."
    echo "------------------------------------"
    exit 1
}

# === MODIFIED: Prompt for Installation Mode ===
get_mode_choice() {
    echo ""
    echo "Select Installation Mode:"
    echo "1. Lite (Excludes local HAPI FHIR Server - No Git/Maven/Dotnet needed)"
    echo "2. Custom URL (Uses a custom FHIR Server - No Git/Maven/Dotnet needed)"
    echo "3. Hapi (Includes local HAPI FHIR Server - Requires Git & Maven)"
    echo "4. Candle (Includes local FHIR Candle Server - Requires Git & Dotnet)"

    while true; do
        read -r -p "Enter your choice (1, 2, 3, or 4): " choice
        case "$choice" in
            1)
                APP_MODE="lite"
                break
                ;;
            2)
                APP_MODE="standalone"
                get_custom_url_prompt
                break
                ;;
            3)
                APP_MODE="standalone"
                SERVER_TYPE="hapi"
                break
                ;;
            4)
                APP_MODE="standalone"
                SERVER_TYPE="candle"
                get_candle_fhir_version
                break
                ;;
            *)
                echo "Invalid input. Please try again."
                ;;
        esac
    done
    echo "Selected Mode: $APP_MODE"
    echo "Server Type: $SERVER_TYPE"
    echo
}

# === NEW: Prompt for Custom URL ===
get_custom_url_prompt() {
    local confirmed_url=""
    while true; do
        echo
        read -r -p "Please enter the custom FHIR server URL: " custom_url_input
        echo
        echo "You entered: $custom_url_input"
        read -r -p "Is this URL correct? (Y/N): " confirm_url
        if [[ "$confirm_url" =~ ^[Yy]$ ]]; then
            confirmed_url="$custom_url_input"
            break
        else
            echo "URL not confirmed. Please re-enter."
        fi
    done

    while true; do
        echo
        read -r -p "Please re-enter the URL to confirm it is correct: " custom_url_input
        if [ "$custom_url_input" = "$confirmed_url" ]; then
            CUSTOM_FHIR_URL_VAL="$custom_url_input"
            echo
            echo "Custom URL confirmed: $CUSTOM_FHIR_URL_VAL"
            break
        else
            echo
            echo "URLs do not match. Please try again."
            confirmed_url="$custom_url_input"
        fi
    done
}

# === NEW: Prompt for Candle FHIR version ===
get_candle_fhir_version() {
    echo ""
    echo "Select the FHIR version for the Candle server:"
    echo "1. R4 (4.0)"
    echo "2. R4B (4.3)"
    echo "3. R5 (5.0)"
    while true; do
        read -r -p "Enter your choice (1, 2, or 3): " choice
        case "$choice" in
            1)
                CANDLE_FHIR_VERSION=r4
                break
                ;;
            2)
                CANDLE_FHIR_VERSION=r4b
                break
                ;;
            3)
                CANDLE_FHIR_VERSION=r5
                break
                ;;
            *)
                echo "Invalid input. Please try again."
                ;;
        esac
    done
}


# Call the function to get mode choice
get_mode_choice

# === Conditionally Execute Server Setup ===
case "$SERVER_TYPE" in
    "hapi")
        echo "Running Hapi server setup..."
        echo

        # --- Step 0: Clean up previous clone (optional) ---
        echo "Checking for existing directory: $CLONE_DIR_HAPI"
        if [ -d "$CLONE_DIR_HAPI" ]; then
            echo "Found existing directory, removing it..."
            rm -rf "$CLONE_DIR_HAPI"
            if [ $? -ne 0 ]; then
                handle_error "Failed to remove existing directory: $CLONE_DIR_HAPI"
            fi
            echo "Existing directory removed."
        else
            echo "Directory does not exist, proceeding with clone."
        fi
        echo

        # --- Step 1: Clone the HAPI FHIR server repository ---
        echo "Cloning repository: $REPO_URL_HAPI into $CLONE_DIR_HAPI..."
        git clone "$REPO_URL_HAPI" "$CLONE_DIR_HAPI"
        if [ $? -ne 0 ]; then
            handle_error "Failed to clone repository. Check Git installation and network connection."
        fi
        echo "Repository cloned successfully."
        echo

        # --- Step 2: Navigate into the cloned directory ---
        echo "Changing directory to $CLONE_DIR_HAPI..."
        cd "$CLONE_DIR_HAPI" || handle_error "Failed to change directory to $CLONE_DIR_HAPI."
        echo "Current directory: $(pwd)"
        echo

        # --- Step 3: Build the HAPI server using Maven ---
        echo "===> Starting Maven build (Step 3)..."
        mvn clean package -DskipTests=true -Pboot
        if [ $? -ne 0 ]; then
            echo "ERROR: Maven build failed."
            cd ..
            handle_error "Maven build process resulted in an error."
        fi
        echo "Maven build completed successfully."
        echo

        # --- Step 4: Copy the configuration file ---
        echo "===> Starting file copy (Step 4)..."
        echo "Copying configuration file..."
        INITIAL_SCRIPT_DIR=$(pwd)
        ABSOLUTE_SOURCE_CONFIG_PATH="${INITIAL_SCRIPT_DIR}/../${SOURCE_CONFIG_DIR}/target/classes/${CONFIG_FILE}"

        echo "Source: $ABSOLUTE_SOURCE_CONFIG_PATH"
        echo "Destination: target/classes/$CONFIG_FILE"

        if [ ! -f "$ABSOLUTE_SOURCE_CONFIG_PATH" ]; then
            echo "WARNING: Source configuration file not found at $ABSOLUTE_SOURCE_CONFIG_PATH."
            echo "The script will continue, but the server might use default configuration."
        else
            cp "$ABSOLUTE_SOURCE_CONFIG_PATH" "target/classes/"
            if [ $? -ne 0 ]; then
                echo "WARNING: Failed to copy configuration file. Check if the source file exists and permissions."
                echo "The script will continue, but the server might use default configuration."
            else
                echo "Configuration file copied successfully."
            fi
        fi
        echo

        # --- Step 5: Navigate back to the parent directory ---
        echo "===> Changing directory back (Step 5)..."
        cd .. || handle_error "Failed to change back to the parent directory."
        echo "Current directory: $(pwd)"
        echo
        ;;

    "candle")
        echo "Running FHIR Candle server setup..."
        echo
        
        # --- Step 0: Clean up previous clone (optional) ---
        echo "Checking for existing directory: $CLONE_DIR_CANDLE"
        if [ -d "$CLONE_DIR_CANDLE" ]; then
            echo "Found existing directory, removing it..."
            rm -rf "$CLONE_DIR_CANDLE"
            if [ $? -ne 0 ]; then
                handle_error "Failed to remove existing directory: $CLONE_DIR_CANDLE"
            fi
            echo "Existing directory removed."
        else
            echo "Directory does not exist, proceeding with clone."
        fi
        echo

        # --- Step 1: Clone the FHIR Candle server repository ---
        echo "Cloning repository: $REPO_URL_CANDLE into $CLONE_DIR_CANDLE..."
        git clone "$REPO_URL_CANDLE" "$CLONE_DIR_CANDLE"
        if [ $? -ne 0 ]; then
            handle_error "Failed to clone repository. Check Git and Dotnet SDK installation and network connection."
        fi
        echo "Repository cloned successfully."
        echo
        
        # --- Step 2: Navigate into the cloned directory ---
        echo "Changing directory to $CLONE_DIR_CANDLE..."
        cd "$CLONE_DIR_CANDLE" || handle_error "Failed to change directory to $CLONE_DIR_CANDLE."
        echo "Current directory: $(pwd)"
        echo

        # --- Step 3: Build the FHIR Candle server using Dotnet ---
        echo "===> Starting Dotnet build (Step 3)..."
        dotnet publish -c Release -f net9.0 -o publish
        if [ $? -ne 0 ]; then
            handle_error "Dotnet build failed. Check Dotnet SDK installation."
        fi
        echo "Dotnet build completed successfully."
        echo
        
        # --- Step 4: Navigate back to the parent directory ---
        echo "===> Changing directory back (Step 4)..."
        cd .. || handle_error "Failed to change back to the parent directory."
        echo "Current directory: $(pwd)"
        echo
        ;;

    *) # APP_MODE is Lite, no SERVER_TYPE
        echo "Running Lite setup, skipping server build..."
        if [ -d "$CLONE_DIR_HAPI" ]; then
            echo "Found existing HAPI directory in Lite mode. Removing it to avoid build issues..."
            rm -rf "$CLONE_DIR_HAPI"
        fi
        if [ -d "$CLONE_DIR_CANDLE" ]; then
            echo "Found existing Candle directory in Lite mode. Removing it to avoid build issues..."
            rm -rf "$CLONE_DIR_CANDLE"
        fi
        mkdir -p "${CLONE_DIR_HAPI}/target/classes"
        mkdir -p "${CLONE_DIR_HAPI}/custom"
        touch "${CLONE_DIR_HAPI}/target/ROOT.war"
        touch "${CLONE_DIR_HAPI}/target/classes/application.yaml"
        mkdir -p "${CLONE_DIR_CANDLE}/publish"
        touch "${CLONE_DIR_CANDLE}/publish/fhir-candle.dll"
        echo "Placeholder files and directories created for Lite mode build."
        echo
        ;;
esac

# === MODIFIED: Update docker-compose.yml to set APP_MODE and HAPI_FHIR_URL and DOCKERFILE ===
echo "Updating docker-compose.yml with APP_MODE=$APP_MODE and HAPI_FHIR_URL..."
DOCKER_COMPOSE_TMP="docker-compose.yml.tmp"
DOCKER_COMPOSE_ORIG="docker-compose.yml"

HAPI_URL_TO_USE="https://fhir.hl7.org.au/aucore/fhir/DEFAULT/"
if [ -n "$CUSTOM_FHIR_URL_VAL" ]; then
    HAPI_URL_TO_USE="$CUSTOM_FHIR_URL_VAL"
elif [ "$SERVER_TYPE" = "candle" ]; then
    HAPI_URL_TO_USE="http://localhost:5826/fhir/${CANDLE_FHIR_VERSION}"
else
    HAPI_URL_TO_USE="http://localhost:8080/fhir"
fi

DOCKERFILE_TO_USE="Dockerfile.lite"
if [ "$SERVER_TYPE" = "hapi" ]; then
    DOCKERFILE_TO_USE="Dockerfile.hapi"
elif [ "$SERVER_TYPE" = "candle" ]; then
    DOCKERFILE_TO_USE="Dockerfile.candle"
fi

cat << EOF > "$DOCKER_COMPOSE_TMP"
version: '3.8'
services:
  fhirflare:
    build:
      context: .
      dockerfile: ${DOCKERFILE_TO_USE}
    ports:
EOF

if [ "$SERVER_TYPE" = "candle" ]; then
    cat << EOF >> "$DOCKER_COMPOSE_TMP"
      - "5000:5000"
      - "5001:5826"
EOF
else
    cat << EOF >> "$DOCKER_COMPOSE_TMP"
      - "5000:5000"
      - "8080:8080"
EOF
fi

cat << EOF >> "$DOCKER_COMPOSE_TMP"
    volumes:
      - ./instance:/app/instance
      - ./static/uploads:/app/static/uploads
      - ./logs:/app/logs
EOF

if [ "$SERVER_TYPE" = "hapi" ]; then
    cat << EOF >> "$DOCKER_COMPOSE_TMP"
      - ./instance/hapi-h2-data/:/app/h2-data # Keep volume mounts consistent
      - ./hapi-fhir-jpaserver/target/ROOT.war:/usr/local/tomcat/webapps/ROOT.war
      - ./hapi-fhir-jpaserver/target/classes/application.yaml:/usr/local/tomcat/conf/application.yaml
EOF
elif [ "$SERVER_TYPE" = "candle" ]; then
    cat << EOF >> "$DOCKER_COMPOSE_TMP"
      - ./fhir-candle/publish/:/app/fhir-candle-publish/
EOF
fi

cat << EOF >> "$DOCKER_COMPOSE_TMP"
    environment:
      - FLASK_APP=app.py
      - FLASK_ENV=development
      - NODE_PATH=/usr/lib/node_modules
      - APP_MODE=${APP_MODE}
      - APP_BASE_URL=http://localhost:5000
      - HAPI_FHIR_URL=${HAPI_URL_TO_USE}
EOF

if [ "$SERVER_TYPE" = "candle" ]; then
    cat << EOF >> "$DOCKER_COMPOSE_TMP"
      - ASPNETCORE_URLS=http://0.0.0.0:5826
EOF
fi

cat << EOF >> "$DOCKER_COMPOSE_TMP"
    command: supervisord -c /etc/supervisord.conf
EOF

if [ ! -f "$DOCKER_COMPOSE_TMP" ]; then
    handle_error "Failed to create temporary docker-compose file ($DOCKER_COMPOSE_TMP)."
fi

# Replace the original docker-compose.yml
mv "$DOCKER_COMPOSE_TMP" "$DOCKER_COMPOSE_ORIG"
echo "docker-compose.yml updated successfully."
echo

# --- Step 6: Build Docker images ---
echo "===> Starting Docker build (Step 6)..."
docker-compose build --no-cache
if [ $? -ne 0 ]; then
    handle_error "Docker Compose build failed. Check Docker installation and docker-compose.yml file."
fi
echo "Docker images built successfully."
echo

# --- Step 7: Start Docker containers ---
echo "===> Starting Docker containers (Step 7)..."
docker-compose up -d
if [ $? -ne 0 ]; then
    handle_error "Docker Compose up failed. Check Docker installation and container configurations."
fi
echo "Docker containers started successfully."
echo

echo "===================================="
echo "Script finished successfully! (Mode: $APP_MODE)"
echo "===================================="
exit 0
