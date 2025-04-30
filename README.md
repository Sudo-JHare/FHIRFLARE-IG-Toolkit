# FHIRFLARE IG Toolkit

## Overview

The FHIRFLARE IG Toolkit is a Flask-based web application designed to streamline the management, processing, validation, and deployment of FHIR Implementation Guides (IGs) and test data. It offers a user-friendly interface for importing IG packages, extracting metadata, validating FHIR resources or bundles, pushing IGs to FHIR servers, converting FHIR resources to FHIR Shorthand (FSH), and uploading complex test data sets with dependency management. The toolkit includes live consoles for real-time feedback, making it an essential tool for FHIR developers and implementers.

The application can run in two modes:

* **Standalone:** Includes a Dockerized Flask frontend, SQLite database, and an embedded HAPI FHIR server for local validation and interaction.
* **Lite:** Includes only the Dockerized Flask frontend and SQLite database, excluding the local HAPI FHIR server. Requires connection to external FHIR servers for certain features.

## Installation Modes (Lite vs. Standalone)

This toolkit offers two primary installation modes to suit different needs:

* **Standalone Version:**
    * Includes the full FHIRFLARE Toolkit application **and** an embedded HAPI FHIR server running locally within the Docker environment.
    * Allows for local FHIR resource validation using HAPI FHIR's capabilities.
    * Enables the "Use Local HAPI" option in the FHIR API Explorer and FHIR UI Operations pages, proxying requests to the internal HAPI server (`http://localhost:8080/fhir`).
    * Requires Git and Maven during the initial build process (via the `.bat` script or manual steps) to prepare the HAPI FHIR server.
    * Ideal for users who want a self-contained environment for development and testing or who don't have readily available external FHIR servers.

* **Lite Version:**
    * Includes the FHIRFLARE Toolkit application **without** the embedded HAPI FHIR server.
    * Requires users to provide URLs for external FHIR servers when using features like the FHIR API Explorer and FHIR UI Operations pages. The "Use Local HAPI" option will be disabled in the UI.
    * Resource validation relies solely on local checks against downloaded StructureDefinitions, which may be less comprehensive than HAPI FHIR's validation (e.g., for terminology bindings or complex invariants).
    * **Does not require Git or Maven** for setup if using the `.bat` script or running the pre-built Docker image.
    * Ideal for users who primarily want to use the IG management, processing, and FSH conversion features, or who will always connect to existing external FHIR servers.

## Features

* **Import IGs:** Download FHIR IG packages and dependencies from a package registry, supporting flexible version formats (e.g., `1.2.3`, `1.1.0-preview`, `current`) and dependency pulling modes (Recursive, Patch Canonical, Tree Shaking).
* **Manage IGs:** View, process, unload, or delete downloaded IGs, with duplicate detection and resolution.
* **Process IGs:** Extract resource types, profiles, must-support elements, examples, and profile relationships (`structuredefinition-compliesWithProfile` and `structuredefinition-imposeProfile`).
* **Validate FHIR Resources/Bundles:** Validate single FHIR resources or bundles against selected IGs, with detailed error and warning reports (alpha feature). *Note: Lite version uses local SD checks only.*
* **Push IGs:** Upload IG resources (and optionally dependencies) to a target FHIR server. Features include:
    * Real-time console output.
    * Authentication support (Bearer Token).
    * Filtering by resource type or specific files to skip.
    * Semantic comparison to skip uploading identical resources (override with **Force Upload** option).
    * Correct handling of canonical resources (searching by URL/version before deciding POST/PUT).
    * Dry run mode for simulation.
    * Verbose logging option.
* **Upload Test Data:** Upload complex sets of test data (individual JSON/XML files or ZIP archives) to a target FHIR server. Features include:
    * Robust parsing of JSON and XML (using `fhir.resources` library when available).
    * Automatic dependency analysis based on resource references within the uploaded set.
    * Topological sorting to ensure resources are uploaded in the correct order.
    * Cycle detection in dependencies.
    * Choice of individual resource uploads or a single transaction bundle.
    * **Optional Pre-Upload Validation:** Validate resources against a selected profile package before uploading.
    * **Optional Conditional Uploads (Individual Mode):** Check resource existence (GET) and use conditional `If-Match` headers for updates (PUT) or create resources (PUT/POST). Falls back to simple PUT if unchecked.
    * Configurable error handling (stop on first error or continue).
    * Authentication support (Bearer Token).
    * Streaming progress log via the UI.
    * Handles large numbers of files using a custom form parser.
* **Profile Relationships:** Display and validate `compliesWithProfile` and `imposeProfile` extensions in the UI (configurable).
* **FSH Converter:** Convert FHIR JSON/XML resources to FHIR Shorthand (FSH) using GoFSH, with advanced options (Package context, Output styles, Log levels, FHIR versions, Fishing Trip, Dependencies, Indentation, Meta Profile handling, Alias File, No Alias). Includes a waiting spinner.
* **FHIR Interaction UIs:** Explore FHIR server capabilities and interact with resources using the "FHIR API Explorer" (simple GET/POST/PUT/DELETE) and "FHIR UI Operations" (Swagger-like interface based on CapabilityStatement). *Note: Lite version requires custom server URLs.*
* **API Support:** RESTful API endpoints for importing, pushing, retrieving metadata, validating, and uploading test data.
* **Live Console:** Real-time logs for push, validation, upload test data, and FSH conversion operations.
* **Configurable Behavior:** Control validation modes, display options via `app.config`.
* **Theming:** Supports light and dark modes.

## Technology Stack

* Python 3.12+, Flask 2.3.3, Flask-SQLAlchemy 3.0.5, Flask-WTF 1.2.1
* Jinja2, Bootstrap 5.3.3, JavaScript (ES6), Lottie-Web 5.12.2
* SQLite
* Docker, Docker Compose, Supervisor
* Node.js 18+ (for GoFSH/SUSHI), GoFSH, SUSHI
* HAPI FHIR (Standalone version only)
* Requests 2.31.0, Tarfile, Logging, Werkzeug
* fhir.resources (optional, for robust XML parsing)

## Prerequisites

* **Docker:** Required for containerized deployment (both versions).
* **Git & Maven:** Required **only** for building the **Standalone** version from source using the `.bat` script or manual steps. Not required for the Lite version build or for running pre-built Docker Hub images.
* **Windows:** Required if using the `.bat` scripts.

## Setup Instructions

### Running Pre-built Images (General Users)

This is the easiest way to get started without needing Git or Maven. Choose the version you need:

**Lite Version (No local HAPI FHIR):**

```bash
# Pull the latest Lite image
docker pull ghcr.io/sudo-jhare/fhirflare-ig-toolkit-lite:latest

# Run the Lite version (maps port 5000 for the UI)
# You'll need to create local directories for persistent data first:
# mkdir instance logs static static/uploads instance/hapi-h2-data
docker run -d \
  -p 5000:5000 \
  -v ./instance:/app/instance \
  -v ./static/uploads:/app/static/uploads \
  -v ./instance/hapi-h2-data:/app/h2-data \
  -v ./logs:/app/logs \
  --name fhirflare-lite \
  ghcr.io/sudo-jhare/fhirflare-ig-toolkit-lite:latest

Standalone Version (Includes local HAPI FHIR):

# Pull the latest Standalone image
docker pull ghcr.io/sudo-jhare/fhirflare-ig-toolkit-standalone:latest

# Run the Standalone version (maps ports 5000 and 8080)
# You'll need to create local directories for persistent data first:
# mkdir instance logs static static/uploads instance/hapi-h2-data
docker run -d \
  -p 5000:5000 \
  -p 8080:8080 \
  -v ./instance:/app/instance \
  -v ./static/uploads:/app/static/uploads \
  -v ./instance/hapi-h2-data:/app/h2-data \
  -v ./logs:/app/logs \
  --name fhirflare-standalone \
  ghcr.io/sudo-jhare/fhirflare-ig-toolkit-standalone:latest

Building from Source (Developers)
Using Windows .bat Scripts (Standalone Version Only):

First Time Setup:

Run Build and Run for first time.bat:

cd "<project folder>"
git clone [https://github.com/hapifhir/hapi-fhir-jpaserver-starter.git](https://github.com/hapifhir/hapi-fhir-jpaserver-starter.git) hapi-fhir-jpaserver
copy .\hapi-fhir-Setup\target\classes\application.yaml .\hapi-fhir-jpaserver\target\classes\application.yaml
mvn clean package -DskipTests=true -Pboot
docker-compose build --no-cache
docker-compose up -d

This clones the HAPI FHIR server, copies configuration, builds the project, and starts the containers.

Subsequent Runs:

Run Run.bat:

cd "<project folder>"
docker-compose up -d

This starts the Flask app (port 5000) and HAPI FHIR server (port 8080).

Access the Application:

Flask UI: http://localhost:5000

HAPI FHIR server: http://localhost:8080

Manual Setup (Linux/MacOS/Windows):

Preparation (Standalone Version Only):

cd <project folder>
git clone [https://github.com/hapifhir/hapi-fhir-jpaserver-starter.git](https://github.com/hapifhir/hapi-fhir-jpaserver-starter.git) hapi-fhir-jpaserver
cp ./hapi-fhir-Setup/target/classes/application.yaml ./hapi-fhir-jpaserver/target/classes/application.yaml

Build:

# Build HAPI FHIR (Standalone Version Only)
mvn clean package -DskipTests=true -Pboot

# Build Docker Image (Specify APP_MODE=lite in docker-compose.yml for Lite version)
docker-compose build --no-cache

Run:

docker-compose up -d

Access the Application:

Flask UI: http://localhost:5000

HAPI FHIR server (Standalone only): http://localhost:8080

Local Development (Without Docker):

Clone the Repository:

git clone [https://github.com/Sudo-JHare/FHIRFLARE-IG-Toolkit.git](https://github.com/Sudo-JHare/FHIRFLARE-IG-Toolkit.git)
cd FHIRFLARE-IG-Toolkit

Install Dependencies:

python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt

Install Node.js, GoFSH, and SUSHI (for FSH Converter):

# Example for Debian/Ubuntu
curl -fsSL [https://deb.nodesource.com/setup_18.x](https://deb.nodesource.com/setup_18.x) | sudo bash -
sudo apt-get install -y nodejs
# Install globally
npm install -g gofsh fsh-sushi

Set Environment Variables:

export FLASK_SECRET_KEY='your-secure-secret-key'
export API_KEY='your-api-key'
# Optional: Set APP_MODE to 'lite' if desired
# export APP_MODE='lite'

Initialize Directories:

mkdir -p instance static/uploads logs
# Ensure write permissions if needed
# chmod -R 777 instance static/uploads logs

Run the Application:

export FLASK_APP=app.py
flask run

Access at http://localhost:5000.

Usage
Import an IG
Navigate to Import IG (/import-ig).

Enter a package name (e.g., hl7.fhir.au.core) and version (e.g., 1.1.0-preview).

Choose a dependency mode:

Current Recursive: Import all dependencies listed in package.json recursively.

Patch Canonical Versions: Import only canonical FHIR packages (e.g., hl7.fhir.r4.core).

Tree Shaking: Import only dependencies containing resources actually used by the main package.

Click Import to download the package and dependencies.

Manage IGs
Go to Manage FHIR Packages (/view-igs) to view downloaded and processed IGs.

Actions:

Process: Extract metadata (resource types, profiles, must-support elements, examples).

Unload: Remove processed IG data from the database.

Delete: Remove package files from the filesystem.

Duplicates are highlighted for resolution.

View Processed IGs
After processing, view IG details (/view-ig/<id>), including:

Resource types and profiles.

Must-support elements and examples.

Profile relationships (compliesWithProfile, imposeProfile) if enabled (DISPLAY_PROFILE_RELATIONSHIPS).

Interactive StructureDefinition viewer (Differential, Snapshot, Must Support, Key Elements, Constraints, Terminology, Search Params).

Validate FHIR Resources/Bundles
Navigate to Validate FHIR Sample (/validate-sample).

Select a package (e.g., hl7.fhir.au.core#1.1.0-preview).

Choose Single Resource or Bundle mode.

Paste or upload FHIR JSON/XML (e.g., a Patient resource).

Submit to view validation errors/warnings.

Note: Alpha feature; report issues to GitHub (remove PHI).

Push IGs to a FHIR Server
Go to Push IGs (/push-igs).

Select a downloaded package.

Enter the Target FHIR Server URL.

Configure Authentication (None, Bearer Token).

Choose options: Include Dependencies, Force Upload (skips comparison check), Dry Run, Verbose Log.

Optionally filter by Resource Types (comma-separated) or Skip Specific Files (paths within package, comma/newline separated).

Click Push to FHIR Server to upload resources. Canonical resources are checked before upload. Identical resources are skipped unless Force Upload is checked.

Monitor progress in the live console.

Upload Test Data
Navigate to Upload Test Data (/upload-test-data).

Enter the Target FHIR Server URL.

Configure Authentication (None, Bearer Token).

Select one or more .json, .xml files, or a single .zip file containing test resources.

Optionally check Validate Resources Before Upload? and select a Validation Profile Package.

Choose Upload Mode:

Individual Resources: Uploads each resource one by one in dependency order.

Transaction Bundle: Uploads all resources in a single transaction.

Optionally check Use Conditional Upload (Individual Mode Only)? to use If-Match headers for updates.

Choose Error Handling:

Stop on First Error: Halts the process if any validation or upload fails.

Continue on Error: Reports errors but attempts to process/upload remaining resources.

Click Upload and Process. The tool parses files, optionally validates, analyzes dependencies, topologically sorts resources, and uploads them according to selected options.

Monitor progress in the streaming log output.

Convert FHIR to FSH
Navigate to FSH Converter (/fsh-converter).

Optionally select a package for context (e.g., hl7.fhir.au.core#1.1.0-preview).

Choose input mode:

Upload File: Upload a FHIR JSON/XML file.

Paste Text: Paste FHIR JSON/XML content.

Configure options:

Output Style: file-per-definition, group-by-fsh-type, group-by-profile, single-file.

Log Level: error, warn, info, debug.

FHIR Version: R4, R4B, R5, or auto-detect.

Fishing Trip: Enable round-trip validation with SUSHI, generating a comparison report.

Dependencies: Specify additional packages (e.g., hl7.fhir.us.core@6.1.0, one per line).

Indent Rules: Enable context path indentation for readable FSH.

Meta Profile: Choose only-one, first, or none for meta.profile handling.

Alias File: Upload an FSH file with aliases (e.g., $MyAlias = http://example.org).

No Alias: Disable automatic alias generation.

Click Convert to FSH to generate and display FSH output, with a waiting spinner (light/dark theme) during processing.

If Fishing Trip is enabled, view the comparison report via the "Click here for SUSHI Validation" badge button.

Download the result as a .fsh file.

Explore FHIR Operations
Navigate to FHIR UI Operations (/fhir-ui-operations).

Toggle between local HAPI (/fhir) or a custom FHIR server.

Click Fetch Metadata to load the server’s CapabilityStatement.

Select a resource type (e.g., Patient, Observation) or System to view operations:

System operations: GET /metadata, POST /, GET /_history, GET/POST /$diff, POST /$reindex, POST /$expunge, etc.

Resource operations: GET Patient/:id, POST Observation/_search, etc.

Use Try it out to input parameters or request bodies, then Execute to view results in JSON, XML, or narrative formats.

API Usage
Import IG
curl -X POST http://localhost:5000/api/import-ig \
-H "Content-Type: application/json" \
-H "X-API-Key: your-api-key" \
-d '{"package_name": "hl7.fhir.au.core", "version": "1.1.0-preview", "dependency_mode": "recursive"}'

Returns complies_with_profiles, imposed_profiles, and duplicate_packages_present info.

Push IG
curl -X POST http://localhost:5000/api/push-ig \
-H "Content-Type: application/json" \
-H "Accept: application/x-ndjson" \
-H "X-API-Key: your-api-key" \
-d '{
      "package_name": "hl7.fhir.au.core",
      "version": "1.1.0-preview",
      "fhir_server_url": "http://localhost:8080/fhir",
      "include_dependencies": true,
      "force_upload": false,
      "dry_run": false,
      "verbose": false,
      "auth_type": "none"
    }'

Returns a streaming NDJSON response with progress and final summary.

Upload Test Data
curl -X POST http://localhost:5000/api/upload-test-data \
-H "X-API-Key: your-api-key" \
-H "Accept: application/x-ndjson" \
-F "fhir_server_url=http://your-fhir-server/fhir" \
-F "auth_type=bearerToken" \
-F "auth_token=YOUR_TOKEN" \
-F "upload_mode=individual" \
-F "error_handling=continue" \
-F "validate_before_upload=true" \
-F "validation_package_id=hl7.fhir.r4.core#4.0.1" \
-F "use_conditional_uploads=true" \
-F "test_data_files=@/path/to/your/patient.json" \
-F "test_data_files=@/path/to/your/observations.zip"

Returns a streaming NDJSON response with progress and final summary.

Uses multipart/form-data for file uploads.

Validate Resource/Bundle
Not yet exposed via API; use the UI at /validate-sample.

Configuration Options
Located in app.py:

VALIDATE_IMPOSED_PROFILES: (Default: True) Validates resources against imposed profiles during push.

DISPLAY_PROFILE_RELATIONSHIPS: (Default: True) Shows compliesWithProfile and imposeProfile in the UI.

FHIR_PACKAGES_DIR: (Default: /app/instance/fhir_packages) Stores .tgz packages and metadata.

UPLOAD_FOLDER: (Default: /app/static/uploads) Stores GoFSH output files and FSH comparison reports.

SECRET_KEY: Required for CSRF protection and sessions. Set via environment variable or directly.

API_KEY: Required for API authentication. Set via environment variable or directly.

MAX_CONTENT_LENGTH: (Default: Flask default) Max size for HTTP request body (e.g., 16 * 1024 * 1024 for 16MB). Important for large uploads.

MAX_FORM_PARTS: (Default: Werkzeug default, often 1000) Default max number of form parts. Overridden for /api/upload-test-data by CustomFormDataParser.

Testing
The project includes a test suite covering UI, API, database, file operations, and security.

Test Prerequisites:

pytest: For running tests.

pytest-mock: For mocking dependencies.

Install: pip install pytest pytest-mock

Running Tests:

cd <project folder>
pytest tests/test_app.py -v

Test Coverage:

UI Pages: Homepage, Import IG, Manage IGs, Push IGs, Validate Sample, View Processed IG, FSH Converter, Upload Test Data.

API Endpoints: POST /api/import-ig, POST /api/push-ig, GET /get-structure, GET /get-example, POST /api/upload-test-data.

Database: IG processing, unloading, viewing.

File Operations: Package processing, deletion, FSH output.

Security: CSRF protection, flash messages, secret key.

FSH Converter: Form submission, file/text input, GoFSH execution, Fishing Trip comparison.

Upload Test Data: Parsing, dependency graph, sorting, upload modes, validation, conditional uploads.

Development Notes
Background
The toolkit addresses the need for a comprehensive FHIR IG management tool, with recent enhancements for resource validation, FSH conversion with advanced GoFSH features, flexible versioning, improved IG pushing, and dependency-aware test data uploading, making it a versatile platform for FHIR developers.

Technical Decisions
Flask: Lightweight and flexible for web development.

SQLite: Simple for development; consider PostgreSQL for production.

Bootstrap 5.3.3: Responsive UI with custom styling.

Lottie-Web: Renders themed animations for FSH conversion waiting spinner.

GoFSH/SUSHI: Integrated via Node.js for advanced FSH conversion and round-trip validation.

Docker: Ensures consistent deployment with Flask and HAPI FHIR.

Flexible Versioning: Supports non-standard IG versions (e.g., -preview, -ballot).

Live Console/Streaming: Real-time feedback for complex operations (Push, Upload Test Data, FSH).

Validation: Alpha feature with ongoing FHIRPath improvements.

Dependency Management: Uses topological sort for Upload Test Data feature.

Form Parsing: Uses custom Werkzeug parser for Upload Test Data to handle large numbers of files.

Recent Updates
Upload Test Data Enhancements (April 2025):

Added optional Pre-Upload Validation against selected IG profiles.

Added optional Conditional Uploads (GET + POST/PUT w/ If-Match) for individual mode.

Implemented robust XML parsing using fhir.resources library (when available).

Fixed 413 Request Entity Too Large errors for large file counts using a custom Werkzeug FormDataParser.

Path: templates/upload_test_data.html, app.py, services.py, forms.py.

Push IG Enhancements (April 2025):

Added semantic comparison to skip uploading identical resources.

Added "Force Upload" option to bypass comparison.

Improved handling of canonical resources (search before PUT/POST).

Added filtering by specific files to skip during push.

More detailed summary report in stream response.

Path: templates/cp_push_igs.html, app.py, services.py.

Waiting Spinner for FSH Converter (April 2025):

Added a themed (light/dark) Lottie animation spinner during FSH execution.

Path: templates/fsh_converter.html, static/animations/, static/js/lottie-web.min.js.

Advanced FSH Converter (April 2025):

Added support for GoFSH advanced options: --fshing-trip, --dependency, --indent, --meta-profile, --alias-file, --no-alias.

Displays Fishing Trip comparison reports.

Path: templates/fsh_converter.html, app.py, services.py, forms.py.

(Keep older updates listed here if desired)

Known Issues and Workarounds
Favicon 404: Clear browser cache or verify /app/static/favicon.ico.

CSRF Errors: Set FLASK_SECRET_KEY and ensure {{ form.hidden_tag() }} in forms.

Import Fails: Check package name/version and connectivity.

Validation Accuracy: Alpha feature; report issues to GitHub (remove PHI).

Package Parsing: Non-standard .tgz filenames may parse incorrectly. Fallback uses name-only parsing.

Permissions: Ensure instance/ and static/uploads/ are writable.

GoFSH/SUSHI Errors: Check ./logs/flask_err.log for ERROR:services:GoFSH failed. Ensure valid FHIR inputs and SUSHI installation.

Upload Test Data XML Parsing: Relies on fhir.resources library for full validation; basic parsing used as fallback. Complex XML structures might not be fully analyzed for dependencies with basic parsing. Prefer JSON for reliable dependency analysis.

413 Request Entity Too Large: Primarily handled by CustomFormDataParser for /api/upload-test-data. Check the parser's max_form_parts limit if still occurring. MAX_CONTENT_LENGTH in app.py controls overall size. Reverse proxy limits (client_max_body_size in Nginx) might also apply.

Future Improvements
Upload Test Data: Improve XML parsing further (direct XML->fhir.resource object if possible), add visual progress bar, add upload order preview, implement transaction bundle size splitting, add 'Clear Target Server' option (with confirmation).

Validation: Enhance FHIRPath for complex constraints; add API endpoint.

Sorting: Sort IG versions in /view-igs (e.g., ascending).

Duplicate Resolution: Options to keep latest version or merge resources.

Production Database: Support PostgreSQL.

Error Reporting: Detailed validation error paths in the UI.

FSH Enhancements: Add API endpoint for FSH conversion; support inline instance construction.

FHIR Operations: Add complex parameter support (e.g., /$diff with left/right).

Completed Items
Testing suite with basic coverage.

API endpoints for POST /api/import-ig and POST /api/push-ig.

Flexible versioning (-preview, -ballot).

CSRF fixes for forms.

Resource validation UI (alpha).

FSH Converter with advanced GoFSH features and waiting spinner.

Push IG enhancements (force upload, semantic comparison, canonical handling, skip files).

Upload Test Data feature with dependency sorting, multiple upload modes, pre-upload validation, conditional uploads, robust XML parsing, and fix for large file counts.

Far-Distant Improvements
Cache Service: Use Redis for IG metadata caching.

Database Optimization: Composite index on ProcessedIg.package_name and ProcessedIg.version.

Directory Structure
FHIRFLARE-IG-Toolkit/
├── app.py                              # Main Flask application
├── Build and Run for first time.bat    # Windows script for first-time Docker setup
├── docker-compose.yml                  # Docker Compose configuration
├── Dockerfile                          # Docker configuration
├── forms.py                            # Form definitions
├── LICENSE.md                          # Apache 2.0 License
├── README.md                           # Project documentation
├── requirements.txt                    # Python dependencies
├── Run.bat                             # Windows script for running Docker
├── services.py                         # Logic for IG import, processing, validation, pushing, FSH conversion, test data upload
├── supervisord.conf                    # Supervisor configuration
├── hapi-fhir-Setup/
│   ├── README.md                       # HAPI FHIR setup instructions
│   └── target/
│       └── classes/
│           └── application.yaml        # HAPI FHIR configuration
├── instance/
│   ├── fhir_ig.db                      # SQLite database
│   ├── fhir_ig.db.old                  # Database backup
│   └── fhir_packages/                  # Stored IG packages and metadata
│       ├── ... (example packages) ...
├── logs/
│   ├── flask.log                       # Flask application logs
│   ├── flask_err.log                   # Flask error logs
│   ├── supervisord.log                 # Supervisor logs
│   ├── supervisord.pid                 # Supervisor PID file
│   ├── tomcat.log                      # Tomcat logs for HAPI FHIR
│   └── tomcat_err.log                  # Tomcat error logs
├── static/
│   ├── animations/
│   │   ├── loading-dark.json           # Dark theme spinner animation
│   │   └── loading-light.json          # Light theme spinner animation
│   ├── favicon.ico                     # Application favicon
│   ├── FHIRFLARE.png                   # Application logo
│   ├── js/
│   │   └── lottie-web.min.js           # Lottie library for spinner
│   └── uploads/
│       ├── output.fsh                  # Generated FSH output (temp location)
│       └── fsh_output/                 # GoFSH output directory
│           ├── ... (example GoFSH output) ...
├── templates/
│   ├── base.html                       # Base template
│   ├── cp_downloaded_igs.html          # UI for managing IGs
│   ├── cp_push_igs.html                # UI for pushing IGs
│   ├── cp_view_processed_ig.html       # UI for viewing processed IGs
│   ├── fhir_ui.html                    # UI for FHIR API explorer
│   ├── fhir_ui_operations.html         # UI for FHIR server operations
│   ├── fsh_converter.html              # UI for FSH conversion
│   ├── import_ig.html                  # UI for importing IGs
│   ├── index.html                      # Homepage
│   ├── upload_test_data.html           # UI for Uploading Test Data
│   ├── validate_sample.html            # UI for validating resources/bundles
│   └── _form_helpers.html              # Form helper macros
├── tests/
│   └── test_app.py                     # Test suite
└── hapi-fhir-jpaserver/                # HAPI FHIR server resources (if Standalone)


## Contributing

* Fork the repository.
* Create a feature branch (`git checkout -b feature/your-feature`).
* Commit changes (`git commit -m "Add your feature"`).
* Push to your branch (`git push origin feature/your-feature`).
* Open a Pull Request.

Ensure code follows PEP 8 and includes tests in `tests/test_app.py`.

## Troubleshooting

* **Favicon 404:** Clear browser cache or verify `/app/static/favicon.ico`:
    `docker exec -it <container_name> curl http://localhost:5000/static/favicon.ico`
* **CSRF Errors:** Set `FLASK_SECRET_KEY` and ensure `{{ form.hidden_tag() }}` in forms.
* **Import Fails:** Check package name/version and connectivity.
* **Validation Accuracy:** Alpha feature; report issues to GitHub (remove PHI).
* **Package Parsing:** Non-standard `.tgz` filenames may parse incorrectly. Fallback uses name-only parsing.
* **Permissions:** Ensure `instance/` and `static/uploads/` are writable:
    `chmod -R 777 instance static/uploads logs`
* **GoFSH/SUSHI Errors:** Check `./logs/flask_err.log` for `ERROR:services:GoFSH failed`. Ensure valid FHIR inputs and SUSHI installation:
    `docker exec -it <container_name> sushi --version`
* **413 Request Entity Too Large:** Increase `MAX_CONTENT_LENGTH` and `MAX_FORM_PARTS` in `app.py`. If using a reverse proxy (e.g., Nginx), increase its `client_max_body_size` setting as well. Ensure the application/container is fully restarted/rebuilt.

## License

Licensed under the Apache 2.0 License. See `LICENSE.md` for details.
