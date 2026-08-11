import sys
import os
# Make paths relative to the current directory instead of absolute '/app' paths
CURRENT_DIR = os.path.abspath(os.path.dirname(__file__))
# Introduce app_dir variable that can be overridden by environment
app_dir = os.environ.get('APP_DIR', CURRENT_DIR)
sys.path.append(CURRENT_DIR)
#sys.path.append(os.path.abspath(os.path.dirname(__file__)))
import datetime
import shutil
import queue
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, Response, current_app, session, send_file, make_response, g
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_wtf import FlaskForm
from flask_wtf.csrf import CSRFProtect
from werkzeug.utils import secure_filename
from werkzeug.formparser import FormDataParser
from werkzeug.exceptions import RequestEntityTooLarge
from urllib.parse import urlparse
from cachetools import TTLCache
from types import SimpleNamespace
import tarfile
import base64
import json
import logging
import requests
import re
import yaml
import threading
import time # Add time import
import io
import services
from services import (
    services_bp,
    construct_tgz_filename,
    parse_package_filename,
    import_package_and_dependencies,
    retrieve_bundles,
    split_bundles,
    fetch_packages_from_registries,
    normalize_package_data,
    cache_packages,
    HAS_PACKAGING_LIB,
    pkg_version,
    get_package_description,
    safe_parse_version,
    import_manual_package_and_dependencies,
    parse_and_list_igs, 
    generate_ig_yaml,
    apply_and_validate,
    run_validator_cli,
    uuid
)
from forms import IgImportForm, ManualIgImportForm, ValidationForm, FSHConverterForm, TestDataUploadForm, RetrieveSplitDataForm, IgYamlForm
from wtforms import SubmitField
from package import package_bp
from flasgger import Swagger, swag_from # Import Flasgger
from copy import deepcopy
import tempfile
from logging.handlers import RotatingFileHandler

#app setup
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-fallback-secret-key-here')

# Update paths to be relative to current directory
instance_path = os.path.join(CURRENT_DIR, 'instance')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', f'sqlite:///{os.path.join(instance_path, "fhir_ig.db")}')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['FHIR_PACKAGES_DIR'] = os.path.join(instance_path, 'fhir_packages')
app.config['API_KEY'] = os.environ.get('API_KEY', 'your-fallback-api-key-here')
app.config['VALIDATE_IMPOSED_PROFILES'] = True
app.config['DISPLAY_PROFILE_RELATIONSHIPS'] = True
app.config['LATEST_BUILD_URL'] = "http://build.fhir.org/ig/qas.json"
app.config['VALIDATION_LOG_DIR'] = os.path.join(app.instance_path, 'validation_logs')
os.makedirs(app.config['VALIDATION_LOG_DIR'], exist_ok=True)
app.config['UPLOAD_FOLDER'] = os.path.join(CURRENT_DIR, 'static', 'uploads')  # For GoFSH output
app.config['APP_BASE_URL'] = os.environ.get('APP_BASE_URL', 'http://localhost:5000')
app.config['HAPI_FHIR_URL'] = os.environ.get('HAPI_FHIR_URL', 'http://localhost:8080/fhir')
CONFIG_PATH = os.environ.get('CONFIG_PATH', '/usr/local/tomcat/conf/application.yaml')



# Basic Swagger configuration
app.config['SWAGGER'] = {
    'title': 'FHIRFLARE IG Toolkit API',
    'uiversion': 3,  # Use Swagger UI 3
    'version': '1.0.0',
    'description': 'API documentation for the FHIRFLARE IG Toolkit. This provides access to various FHIR IG management and validation functionalities.',
    'termsOfService': 'https://example.com/terms', # Replace with your terms
    'contact': {
        'name': 'FHIRFLARE Support',
        'url': 'https://github.com/Sudo-JHare/FHIRFLARE-IG-Toolkit/issues', # Replace with your support URL
        'email': 'xsannz@gmail.com', # Replace with your support email
    },
    'license': {
        'name': 'MIT License', # Or your project's license
        'url': 'https://github.com/Sudo-JHare/FHIRFLARE-IG-Toolkit/blob/main/LICENSE.md', # Link to your license
    },
    'securityDefinitions': { # Defines how API key security is handled
        'ApiKeyAuth': {
            'type': 'apiKey',
            'name': 'X-API-Key', # The header name for the API key
            'in': 'header',
            'description': 'API Key for accessing protected endpoints.'
        }
    },
    # 'security': [{'ApiKeyAuth': []}], # Optional: Apply ApiKeyAuth globally to all Flasgger-documented API endpoints by default
                                     # If you set this, individual public endpoints would need 'security': [] in their swag_from spec.
                                     # It's often better to define security per-endpoint in @swag_from.
    'specs_route': '/apidocs/' # URL for the Swagger UI. This makes url_for('flasgger.apidocs') work.
}
swagger = Swagger(app) # Initialize Flasgger with the app. This registers its routes.


# Register blueprints immediately after app setup
app.register_blueprint(services_bp, url_prefix='/api')
app.register_blueprint(package_bp)
logging.getLogger(__name__).info("Registered package_bp blueprint")



# Set max upload size (e.g., 12 MB, adjust as needed)
app.config['MAX_CONTENT_LENGTH'] = 6 * 1024 * 1024

# In-memory cache with 5-minute TTL
package_cache = TTLCache(maxsize=100, ttl=300)

# Increase max number of form parts (default is often 1000)
#app.config['MAX_FORM_PARTS'] = 1000 # Allow up to 1000 parts this is a hard coded stop limit in MAX_FORM_PARTS of werkzeug


#-----------------------------------------------------------------------------------------------------------------------
# --- Basic Logging Setup (adjust level and format as needed) ---
# Configure root logger first - This sets the foundation
# Set level to DEBUG initially to capture everything, handlers can filter later
logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    # Force=True might be needed if basicConfig was called elsewhere implicitly
                    # force=True
                   )

# Get the application logger (for app-specific logs)
logger = logging.getLogger(__name__)
# Explicitly set the app logger's level (can be different from root)
logger.setLevel(logging.DEBUG)

# --- Optional: Add File Handler for Debugging ---
# Ensure the instance path exists before setting up the file handler
# Note: This assumes app.instance_path is correctly configured later
#       If running this setup *before* app = Flask(), define instance path manually.
instance_folder_path_for_log = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'instance')
os.makedirs(instance_folder_path_for_log, exist_ok=True)
log_file_path = os.path.join(instance_folder_path_for_log, 'fhirflare_debug.log')

file_handler = None # Initialize file_handler to None
try:
    # Rotate logs: 5 files, 5MB each
    file_handler = RotatingFileHandler(log_file_path, maxBytes=5*1024*1024, backupCount=5, encoding='utf-8')
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    # Set the file handler level - DEBUG will capture everything
    file_handler.setLevel(logging.DEBUG)
    # Add handler to the *root* logger to capture logs from all modules (like services)
    logging.getLogger().addHandler(file_handler)
    logger.info(f"--- File logging initialized to {log_file_path} (Level: DEBUG) ---")
except Exception as e:
    # Log error if file handler setup fails, but continue execution
    logger.error(f"Failed to set up file logging to {log_file_path}: {e}", exc_info=True)
# --- End File Handler Setup ---

#-----------------------------------------------------------------------------------------------------------------------

try:
    import packaging.version as pkg_version
    HAS_PACKAGING_LIB = True
except ImportError:
    HAS_PACKAGING_LIB = False
    # Define a simple fallback parser if needed
    class BasicVersion:
         def __init__(self, v_str): self.v_str = str(v_str) # Ensure string
         def __gt__(self, other): return self.v_str > str(other)
         def __lt__(self, other): return self.v_str < str(other)
         def __eq__(self, other): return self.v_str == str(other)
         def __str__(self): return self.v_str
    pkg_version = SimpleNamespace(parse=BasicVersion, InvalidVersion=ValueError)
# --- End Imports ---


# --- NEW: Define Custom Form Parser ---
class CustomFormDataParser(FormDataParser):
    """Subclass to increase the maximum number of form parts."""
    def __init__(self, *args, **kwargs):
        # Set a higher limit for max_form_parts. Adjust value as needed.
        # This overrides the default limit checked by Werkzeug's parser.
        # Set to a sufficiently high number for your expected maximum file count.
        super().__init__(*args, max_form_parts=2000, **kwargs) # Example: Allow 2000 parts
# --- END NEW ---

# Custom logging handler to capture INFO logs from services module
log_queue = queue.Queue()
class StreamLogHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.INFO)
        self.formatter = logging.Formatter('%(levelname)s:%(name)s:%(message)s')

    def emit(self, record):
        if record.name == 'services' and record.levelno == logging.INFO:
            msg = self.format(record)
            log_queue.put(msg)

# Add custom handler to services logger
services_logger = logging.getLogger('services')
stream_handler = StreamLogHandler()
services_logger.addHandler(stream_handler)

# <<< ADD THIS CONTEXT PROCESSOR >>>
@app.context_processor
def inject_app_mode():
    """Injects the app_mode into template contexts."""
    return dict(app_mode=app.config.get('APP_MODE', 'standalone'))
# <<< END ADD >>>

# Read application mode from environment variable, default to 'standalone'
app.config['APP_MODE'] = os.environ.get('APP_MODE', 'standalone').lower()
logger.info(f"Application running in mode: {app.config['APP_MODE']}")
# --- END mode check ---

# Ensure directories exist and are writable
instance_path = '/app/instance'
packages_path = app.config['FHIR_PACKAGES_DIR']
logger.debug(f"Instance path configuration: {instance_path}")
logger.debug(f"Database URI: {app.config['SQLALCHEMY_DATABASE_URI']}")
logger.debug(f"Packages path: {packages_path}")

try:
    instance_folder_path = app.instance_path
    logger.debug(f"Flask instance folder path: {instance_folder_path}")
    os.makedirs(instance_folder_path, exist_ok=True)
    os.makedirs(packages_path, exist_ok=True)
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    logger.debug(f"Directories created/verified: Instance: {instance_folder_path}, Packages: {packages_path}")
except Exception as e:
    logger.error(f"Failed to create/verify directories: {e}", exc_info=True)

db = SQLAlchemy(app)
csrf = CSRFProtect(app)
migrate = Migrate(app, db)

# Add a global application state dictionary for sharing state between threads
app_state = {
    'fetch_failed': False
}

# @app.route('/clear-cache')
# def clear_cache():
#     """Clears the in-memory package cache, the DB timestamp, and the CachedPackage table."""
#     # Clear in-memory cache
#     app.config['MANUAL_PACKAGE_CACHE'] = None
#     app.config['MANUAL_CACHE_TIMESTAMP'] = None
#     logger.info("In-memory package cache cleared.")

#     # Clear DB timestamp and CachedPackage table
#     try:
#         # Clear the timestamp
#         timestamp_info = RegistryCacheInfo.query.first()
#         if timestamp_info:
#             timestamp_info.last_fetch_timestamp = None
#             db.session.commit()
#             logger.info("Database timestamp cleared.")
#         else:
#             logger.info("No database timestamp found to clear.")

#         # Clear the CachedPackage table
#         num_deleted = db.session.query(CachedPackage).delete()
#         db.session.commit()
#         logger.info(f"Cleared {num_deleted} entries from CachedPackage table.")
#     except Exception as db_err:
#         db.session.rollback()
#         logger.error(f"Failed to clear DB timestamp or CachedPackage table: {db_err}", exc_info=True)
#         flash("Failed to clear database cache.", "warning")

#     flash("Package cache cleared. Fetching fresh list from registries...", "info")
#     # Redirect back to the search page to force a reload and fetch
#     return redirect(url_for('search_and_import'))

# Remove logic from /clear-cache route - it's now handled by the API + background task
@app.route('/clear-cache')
def clear_cache():
    """
    This route is now effectively deprecated if the button uses the API.
    If accessed directly, it could just redirect or show a message.
    For safety, let it clear only the in-memory part and redirect.
    """
    app.config['MANUAL_PACKAGE_CACHE'] = None
    app.config['MANUAL_CACHE_TIMESTAMP'] = None
    session['fetch_failed'] = False # Reset flag
    logger.info("Direct /clear-cache access: Cleared in-memory cache only.")
    flash("Cache refresh must be initiated via the 'Clear & Refresh Cache' button.", "info")
    return redirect(url_for('search_and_import'))

# No changes needed in search_and_import logic itself for this fix.

class ProcessedIg(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    package_name = db.Column(db.String(128), nullable=False)
    version = db.Column(db.String(64), nullable=False)
    processed_date = db.Column(db.DateTime, nullable=False)
    resource_types_info = db.Column(db.JSON, nullable=False)
    must_support_elements = db.Column(db.JSON, nullable=True)
    examples = db.Column(db.JSON, nullable=True)
    complies_with_profiles = db.Column(db.JSON, nullable=True)
    imposed_profiles = db.Column(db.JSON, nullable=True)
    optional_usage_elements = db.Column(db.JSON, nullable=True)
    # --- ADD THIS LINE ---
    search_param_conformance = db.Column(db.JSON, nullable=True) # Stores the extracted conformance map
    # --- END ADD ---
    __table_args__ = (db.UniqueConstraint('package_name', 'version', name='uq_package_version'),)

class CachedPackage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    package_name = db.Column(db.String(128), nullable=False)
    version = db.Column(db.String(64), nullable=False)
    author = db.Column(db.String(128))
    fhir_version = db.Column(db.String(64))
    version_count = db.Column(db.Integer)
    url = db.Column(db.String(256))
    all_versions = db.Column(db.JSON, nullable=True)
    dependencies = db.Column(db.JSON, nullable=True)
    latest_absolute_version = db.Column(db.String(64))
    latest_official_version = db.Column(db.String(64))
    canonical = db.Column(db.String(256))
    registry = db.Column(db.String(256))
    __table_args__ = (db.UniqueConstraint('package_name', 'version', name='uq_cached_package_version'),)

class RegistryCacheInfo(db.Model):
    id = db.Column(db.Integer, primary_key=True) # Simple primary key
    last_fetch_timestamp = db.Column(db.DateTime(timezone=True), nullable=True) # Store UTC timestamp

    def __repr__(self):
        return f'<RegistryCacheInfo id={self.id} last_fetch={self.last_fetch_timestamp}>'

# --- Make sure to handle database migration if you use Flask-Migrate ---
# (e.g., flask db migrate -m "Add search_param_conformance to ProcessedIg", flask db upgrade)
# If not using migrations, you might need to drop and recreate the table (losing existing processed data)
# or manually alter the table using SQLite tools.

def check_api_key():
    api_key = request.headers.get('X-API-Key')
    if not api_key and request.is_json:
        api_key = request.json.get('api_key')
    if not api_key:
        logger.error("API key missing in request")
        return jsonify({"status": "error", "message": "API key missing"}), 401
    if api_key != app.config['API_KEY']:
        logger.error("Invalid API key provided.")
        return jsonify({"status": "error", "message": "Invalid API key"}), 401
    logger.debug("API key validated successfully")
    return None

def list_downloaded_packages(packages_dir):
    packages = []
    errors = []
    duplicate_groups = {}
    logger.debug(f"Scanning packages directory: {packages_dir}")
    if not os.path.exists(packages_dir):
        logger.warning(f"Packages directory not found: {packages_dir}")
        return packages, errors, duplicate_groups
    for filename in os.listdir(packages_dir):
        if filename.endswith('.tgz'):
            full_path = os.path.join(packages_dir, filename)
            name = filename[:-4]
            version = ''
            parsed_name, parsed_version = services.parse_package_filename(filename)
            if parsed_name:
                name = parsed_name
                version = parsed_version
            else:
                logger.warning(f"Could not parse version from {filename}, using default name.")
                errors.append(f"Could not parse {filename}")
            try:
                with tarfile.open(full_path, "r:gz") as tar:
                    # Ensure correct path within tarfile
                    pkg_json_member_path = "package/package.json"
                    try:
                        pkg_json_member = tar.getmember(pkg_json_member_path)
                        fileobj = tar.extractfile(pkg_json_member)
                        if fileobj:
                            pkg_data = json.loads(fileobj.read().decode('utf-8-sig'))
                            name = pkg_data.get('name', name)
                            version = pkg_data.get('version', version)
                            fileobj.close()
                    except KeyError:
                        logger.warning(f"{pkg_json_member_path} not found in {filename}")
                        # Keep parsed name/version if package.json is missing
            except (tarfile.TarError, json.JSONDecodeError, UnicodeDecodeError) as e:
                logger.warning(f"Could not read package.json from {filename}: {e}")
                errors.append(f"Error reading {filename}: {str(e)}")
            except Exception as e:
                logger.error(f"Unexpected error reading package.json from {filename}: {e}", exc_info=True)
                errors.append(f"Unexpected error for {filename}: {str(e)}")

            if name and version: # Only add if both name and version are valid
                packages.append({'name': name, 'version': version, 'filename': filename})
            else:
                logger.warning(f"Skipping package {filename} due to invalid name ('{name}') or version ('{version}')")
                errors.append(f"Invalid package {filename}: name='{name}', version='{version}'")

    # Group duplicates
    name_counts = {}
    for pkg in packages:
        name_val = pkg['name']
        name_counts[name_val] = name_counts.get(name_val, 0) + 1
    for name_val, count in name_counts.items():
        if count > 1:
            duplicate_groups[name_val] = sorted([p['version'] for p in packages if p['name'] == name_val])

    logger.debug(f"Found packages: {len(packages)}")
    logger.debug(f"Errors during package listing: {errors}")
    logger.debug(f"Duplicate groups: {duplicate_groups}")
    return packages, errors, duplicate_groups

@app.route('/')
def index():
    return render_template('index.html', site_name='FHIRFLARE IG Toolkit', now=datetime.datetime.now())

@app.route('/debug-routes')
@swag_from({
    'tags': ['Debugging'],
    'summary': 'List all application routes.',
    'description': 'Provides a JSON list of all registered URL rules and their endpoints. Useful for development and debugging.',
    'responses': {
        '200': {
            'description': 'A list of route strings.',
            'schema': {
                'type': 'array',
                'items': {
                    'type': 'string',
                    'example': 'Endpoint: my_endpoint, Methods: GET,POST, URL: /my/url'
                }
            }
        }
    }
    # No API key needed for this one, so you can add:
    # 'security': [] 
})
def debug_routes():
    """
    Debug endpoint to list all registered routes and their endpoints.
    """
    routes = []
    for rule in app.url_map.iter_rules():
        routes.append(f"Endpoint: {rule.endpoint}, URL: {rule}")
    return jsonify(routes)

@app.route('/api/config', methods=['GET'])
@csrf.exempt
@swag_from({
    'tags': ['HAPI Configuration'],
    'summary': 'Get HAPI FHIR server configuration.',
    'description': 'Retrieves the current HAPI FHIR server configuration from the application.yaml file.',
    'security': [{'ApiKeyAuth': []}], # Requires API Key
    'responses': {
        '200': {
            'description': 'HAPI FHIR configuration.',
            'schema': { 'type': 'object' } # You can be more specific if you know the YAML structure
        },
        '500': {'description': 'Error reading configuration file.'}
    }
})
def get_config():
    try:
        with open(CONFIG_PATH, 'r') as file:
            config = yaml.safe_load(file)
        return jsonify(config)
    except Exception as e:
        logger.error(f"Error reading config file: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/config', methods=['POST'])
@csrf.exempt
@swag_from({
    'tags': ['HAPI Configuration'],
    'summary': 'Save HAPI FHIR server configuration.',
    'description': 'Saves the provided HAPI FHIR server configuration to the application.yaml file.',
    'security': [{'ApiKeyAuth': []}], # Requires API Key
    'parameters': [
        {
            'name': 'config_payload', # Changed name to avoid conflict with function arg
            'in': 'body',
            'required': True,
            'description': 'The HAPI FHIR configuration object.',
            'schema': {
                'type': 'object',
                # Add example properties if you know them
                'example': {'fhir_server': {'base_url': 'http://localhost:8080/fhir'}}
            }
        }
    ],
    'responses': {
        '200': {'description': 'Configuration saved successfully.'},
        '400': {'description': 'Invalid request body.'},
        '500': {'description': 'Error saving configuration file.'}
    }
})
def save_config():
    try:
        config = request.get_json()
        with open(CONFIG_PATH, 'w') as file:
            yaml.safe_dump(config, file, default_flow_style=False)
        logger.info("Configuration saved successfully")
        return jsonify({'message': 'Configuration saved'})
    except Exception as e:
        logger.error(f"Error saving config file: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/restart-tomcat', methods=['POST'])
@csrf.exempt
@swag_from({
    'tags': ['HAPI Configuration'],
    'summary': 'Restart the Tomcat server.',
    'description': 'Attempts to restart the Tomcat server using supervisorctl. Requires appropriate server permissions.',
    'security': [{'ApiKeyAuth': []}], # Requires API Key
    'responses': {
        '200': {'description': 'Tomcat restart initiated successfully.'},
        '500': {'description': 'Error restarting Tomcat (e.g., supervisorctl not found or command failed).'}
    }
})
def restart_tomcat():
    try:
        result = subprocess.run(['supervisorctl', 'restart', 'tomcat'], capture_output=True, text=True)
        if result.returncode == 0:
            logger.info("Tomcat restarted successfully")
            return jsonify({'message': 'Tomcat restarted'})
        else:
            logger.error(f"Failed to restart Tomcat: {result.stderr}")
            return jsonify({'error': result.stderr}), 500
    except Exception as e:
        logger.error(f"Error restarting Tomcat: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/config-hapi')
def config_hapi():
    return render_template('config_hapi.html', site_name='FHIRFLARE IG Toolkit', now=datetime.datetime.now())

@app.route('/api/get-local-server-url', methods=['GET'])
@swag_from({
    'tags': ['FHIR Server Configuration'],
    'summary': 'Get the local FHIR server URL.',
    'description': 'Retrieves the base URL of the configured local FHIR server (HAPI). This is used by frontend components to make direct requests, bypassing the proxy.',
    'responses': {
        '200': {
            'description': 'The URL of the local FHIR server.',
            'schema': {
                'type': 'object',
                'properties': {
                    'url': {'type': 'string', 'example': 'http://localhost:8080/fhir'}
                }
            }
        }
    }
})
def get_local_server_url():
    """
    Expose the local HAPI FHIR server URL to the frontend.
    """
    return jsonify({'url': app.config.get('HAPI_FHIR_URL', 'http://localhost:8080/fhir')})

@app.route('/manual-import-ig', methods=['GET', 'POST'])
def manual_import_ig():
    """
    Handle manual import of FHIR Implementation Guides using file or URL uploads.
    Uses ManualIgImportForm to support file and URL inputs without registry option.
    """
    form = ManualIgImportForm()
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.headers.get('HX-Request') == 'true'

    if form.validate_on_submit():
        import_mode = form.import_mode.data
        dependency_mode = form.dependency_mode.data
        resolve_dependencies = form.resolve_dependencies.data
        while not log_queue.empty():
            log_queue.get()

        try:
            if import_mode == 'file':
                tgz_file = form.tgz_file.data
                temp_dir = tempfile.mkdtemp()
                temp_path = os.path.join(temp_dir, secure_filename(tgz_file.filename))
                tgz_file.save(temp_path)
                result = import_manual_package_and_dependencies(temp_path, dependency_mode=dependency_mode, is_file=True, resolve_dependencies=resolve_dependencies)
                identifier = result.get('requested', tgz_file.filename)
                shutil.rmtree(temp_dir, ignore_errors=True)
            elif import_mode == 'url':
                tgz_url = form.tgz_url.data
                result = import_manual_package_and_dependencies(tgz_url, dependency_mode=dependency_mode, is_url=True, resolve_dependencies=resolve_dependencies)
                identifier = result.get('requested', tgz_url)

            if result['errors'] and not result['downloaded']:
                error_msg = result['errors'][0]
                simplified_msg = error_msg
                if "HTTP error" in error_msg and "404" in error_msg:
                    simplified_msg = "Package not found (404). Check input."
                elif "HTTP error" in error_msg:
                    simplified_msg = f"Error: {error_msg.split(': ', 1)[-1]}"
                elif "Connection error" in error_msg:
                    simplified_msg = "Could not connect to source."
                flash(f"Failed to import {identifier}: {simplified_msg}", "error")
                logger.error(f"Manual import failed for {identifier}: {error_msg}")
                if is_ajax:
                    return jsonify({"status": "error", "message": simplified_msg}), 400
                return render_template('manual_import_ig.html', form=form, site_name='FHIRFLARE IG Toolkit', now=datetime.datetime.now())
            else:
                if result['errors']:
                    flash(f"Partially imported {identifier} with errors. Check logs.", "warning")
                    for err in result['errors']:
                        logger.warning(f"Manual import warning for {identifier}: {err}")
                else:
                    flash(f"Successfully imported {identifier}! Mode: {dependency_mode}", "success")
                if is_ajax:
                    return jsonify({"status": "success", "message": f"Imported {identifier}", "redirect": url_for('view_igs')}), 200
                return redirect(url_for('view_igs'))
        except Exception as e:
            logger.error(f"Unexpected error during manual IG import: {str(e)}", exc_info=True)
            flash(f"An unexpected error occurred: {str(e)}", "error")
            if is_ajax:
                return jsonify({"status": "error", "message": str(e)}), 500
            return render_template('manual_import_ig.html', form=form, site_name='FHIRFLARE IG Toolkit', now=datetime.datetime.now())
    else:
        for field, errors in form.errors.items():
            for error in errors:
                flash(f"Error in {getattr(form, field).label.text}: {error}", "danger")
        if is_ajax:
            return jsonify({"status": "error", "message": "Form validation failed", "errors": form.errors}), 400
        return render_template('manual_import_ig.html', form=form, site_name='FHIRFLARE IG Toolkit', now=datetime.datetime.now())

@app.route('/import-ig', methods=['GET', 'POST'])
def import_ig():
    form = IgImportForm()
    # Check for HTMX request using both X-Requested-With and HX-Request headers
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.headers.get('HX-Request') == 'true'

    if form.validate_on_submit():
        name = form.package_name.data
        version = form.package_version.data
        dependency_mode = form.dependency_mode.data

        # Clear log queue for this request
        while not log_queue.empty():
            log_queue.get()

        try:
            result = import_package_and_dependencies(name, version, dependency_mode=dependency_mode)
            if result['errors'] and not result['downloaded']:
                error_msg = result['errors'][0]
                simplified_msg = error_msg
                if "HTTP error" in error_msg and "404" in error_msg:
                    simplified_msg = "Package not found on registry (404). Check name and version."
                elif "HTTP error" in error_msg:
                    simplified_msg = f"Registry error: {error_msg.split(': ', 1)[-1]}"
                elif "Connection error" in error_msg:
                    simplified_msg = "Could not connect to the FHIR package registry."
                flash(f"Failed to import {name}#{version}: {simplified_msg}", "error")
                logger.error(f"Import failed critically for {name}#{version}: {error_msg}")
                if is_ajax:
                    return jsonify({"status": "error", "message": simplified_msg}), 400
                return render_template('import_ig.html', form=form, site_name='FHIRFLARE IG Toolkit', now=datetime.datetime.now())
            else:
                if result['errors']:
                    flash(f"Partially imported {name}#{version} with errors during dependency processing. Check logs.", "warning")
                    for err in result['errors']:
                        logger.warning(f"Import warning for {name}#{version}: {err}")
                else:
                    flash(f"Successfully downloaded {name}#{version} and dependencies! Mode: {dependency_mode}", "success")
                if is_ajax:
                    return jsonify({"status": "success", "message": f"Imported {name}#{version}", "redirect": url_for('view_igs')}), 200
                return redirect(url_for('view_igs'))
        except Exception as e:
            logger.error(f"Unexpected error during IG import: {str(e)}", exc_info=True)
            flash(f"An unexpected error occurred downloading the IG: {str(e)}", "error")
            if is_ajax:
                return jsonify({"status": "error", "message": str(e)}), 500
            return render_template('import_ig.html', form=form, site_name='FHIRFLARE IG Toolkit', now=datetime.datetime.now())
    else:
        for field, errors in form.errors.items():
            for error in errors:
                flash(f"Error in {getattr(form, field).label.text}: {error}", "danger")
        if is_ajax:
            return jsonify({"status": "error", "message": "Form validation failed", "errors": form.errors}), 400
        return render_template('import_ig.html', form=form, site_name='FHIRFLARE IG Toolkit', now=datetime.datetime.now())

# Function to perform the actual refresh logic in the background
def perform_cache_refresh_and_log():
    """Clears caches, fetches, normalizes, and caches packages, logging progress."""
    # Ensure this runs within an app context to access db, config etc.
    with app.app_context():
        logger.info("--- Starting Background Cache Refresh ---")
        try:
            # 1. Clear In-Memory Cache
            app.config['MANUAL_PACKAGE_CACHE'] = None
            app.config['MANUAL_CACHE_TIMESTAMP'] = None
            logger.info("In-memory cache cleared.")

            # 2. Clear DB Timestamp and CachedPackage Table
            try:
                timestamp_info = RegistryCacheInfo.query.first()
                if timestamp_info:
                    timestamp_info.last_fetch_timestamp = None
                    # Don't commit yet, commit at the end
                num_deleted = db.session.query(CachedPackage).delete()
                db.session.flush() # Apply delete within transaction
                logger.info(f"Cleared {num_deleted} entries from CachedPackage table (DB).")
            except Exception as db_clear_err:
                db.session.rollback()
                logger.error(f"Failed to clear DB cache tables: {db_clear_err}", exc_info=True)
                log_queue.put(f"ERROR: Failed to clear DB - {db_clear_err}")
                log_queue.put("[DONE]") # Signal completion even on error
                return # Stop processing

            # 3. Fetch from Registries
            logger.info("Fetching fresh package list from registries...")
            fetch_failed = False
            try:
                raw_packages = fetch_packages_from_registries(search_term='') # Uses services logger internally
                if not raw_packages:
                    logger.warning("No packages returned from registries during refresh.")
                    fetch_failed = True
                    normalized_packages = []
                else:
                    # 4. Normalize Data
                    logger.info("Normalizing fetched package data...")
                    normalized_packages = normalize_package_data(raw_packages) # Uses services logger

            except Exception as fetch_norm_err:
                 logger.error(f"Error during fetch/normalization: {fetch_norm_err}", exc_info=True)
                 fetch_failed = True
                 normalized_packages = []
                 log_queue.put(f"ERROR: Failed during fetch/normalization - {fetch_norm_err}")


            # 5. Update In-Memory Cache (always update, even if empty on failure)
            now_ts = datetime.datetime.now(datetime.timezone.utc)
            app.config['MANUAL_PACKAGE_CACHE'] = normalized_packages
            app.config['MANUAL_CACHE_TIMESTAMP'] = now_ts
            app_state['fetch_failed'] = fetch_failed # Update app_state instead of session
            logger.info(f"Updated in-memory cache with {len(normalized_packages)} packages. Fetch failed: {fetch_failed}")

            # 6. Cache in Database (if successful fetch)
            if not fetch_failed and normalized_packages:
                try:
                    logger.info("Caching packages in database...")
                    cache_packages(normalized_packages, db, CachedPackage) # Uses services logger
                except Exception as cache_err:
                    db.session.rollback() # Rollback DB changes on caching error
                    logger.error(f"Failed to cache packages in database: {cache_err}", exc_info=True)
                    log_queue.put(f"ERROR: Failed to cache packages in DB - {cache_err}")
                    log_queue.put("[DONE]") # Signal completion
                    return # Stop processing
            elif fetch_failed:
                 logger.warning("Skipping database caching due to fetch failure.")
            else: # No packages but fetch didn't fail (edge case?)
                 logger.info("No packages to cache in database.")


            # 7. Update DB Timestamp (only if fetch didn't fail)
            if not fetch_failed:
                if timestamp_info:
                    timestamp_info.last_fetch_timestamp = now_ts
                else:
                    timestamp_info = RegistryCacheInfo(last_fetch_timestamp=now_ts)
                    db.session.add(timestamp_info)
                logger.info(f"Set DB timestamp to {now_ts}.")
            else:
                 # Ensure timestamp_info is not added if fetch failed and it was new
                 if timestamp_info and timestamp_info in db.new:
                     db.session.expunge(timestamp_info)
                 logger.warning("Skipping DB timestamp update due to fetch failure.")


            # 8. Commit all DB changes (only commit if successful)
            if not fetch_failed:
                db.session.commit()
                logger.info("Database changes committed.")
            else:
                # Rollback any potential flushed changes if fetch failed
                db.session.rollback()
                logger.info("Rolled back DB changes due to fetch failure.")

        except Exception as e:
            db.session.rollback() # Rollback on any other unexpected error
            logger.error(f"Critical error during background cache refresh: {e}", exc_info=True)
            log_queue.put(f"CRITICAL ERROR: {e}")
        finally:
            logger.info("--- Background Cache Refresh Finished ---")
            log_queue.put("[DONE]") # Signal completion


@app.route('/api/refresh-cache-task', methods=['POST'])
@csrf.exempt # Ensure CSRF is handled if needed, or keep exempt
@swag_from({
    'tags': ['Package Management'],
    'summary': 'Refresh FHIR package cache.',
    'description': 'Triggers an asynchronous background task to clear and refresh the FHIR package cache from configured registries.',
    'security': [{'ApiKeyAuth': []}], # Requires API Key
    'responses': {
        '202': {'description': 'Cache refresh process started in the background.'},
        # Consider if other error codes are possible before task starts
    }
})
def refresh_cache_task():
    """API endpoint to trigger the background cache refresh."""
    # Note: Clearing queue here might interfere if multiple users click concurrently.
    # A more robust solution uses per-request queues or task IDs.
    # For simplicity, we clear it assuming low concurrency for this action.
    while not log_queue.empty():
        try: log_queue.get_nowait()
        except queue.Empty: break

    logger.info("Received API request to refresh cache.")
    thread = threading.Thread(target=perform_cache_refresh_and_log, daemon=True)
    thread.start()
    logger.info("Background cache refresh thread started.")
    # Return 202 Accepted: Request accepted, processing in background.
    return jsonify({"status": "accepted", "message": "Cache refresh process started in the background."}), 202


# Modify stream_import_logs - Simpler version: relies on thread putting [DONE]
@app.route('/stream-import-logs')
@swag_from({
    'tags': ['Package Management'],
    'summary': 'Stream package import logs.',
    'description': 'Provides a Server-Sent Events (SSE) stream of logs generated during package import or cache refresh operations. The client should listen for "data:" events. The stream ends with "data: [DONE]".',
    'produces': ['text/event-stream'],
    # No API key usually for SSE streams if they are tied to an existing user session/action
    # 'security': [], 
    'responses': {
        '200': {
            'description': 'An event stream of log messages.',
            'schema': {
                'type': 'string',
                'format': 'text/event-stream',
                'example': "data: INFO: Starting import...\ndata: INFO: Package downloaded.\ndata: [DONE]\n\n"
            }
        }
    }
})
def stream_import_logs():
    logger.debug("SSE connection established to stream-import-logs")
    def generate():
        # Directly consume from the shared queue
        while True:
            try:
                # Block-wait on the shared queue with a timeout
                msg = log_queue.get(timeout=300) # 5 min timeout on get
                clean_msg = str(msg).replace('INFO:services:', '').replace('INFO:app:', '').strip()
                yield f"data: {clean_msg}\n\n"

                if msg == '[DONE]':
                    logger.debug("SSE stream received [DONE] from queue, closing stream.")
                    break # Exit the generate loop
            except queue.Empty:
                # Timeout occurred waiting for message or [DONE]
                logger.warning("SSE stream timed out waiting for logs. Closing.")
                yield "data: ERROR: Timeout waiting for logs.\n\n"
                yield "data: [DONE]\n\n" # Still send DONE to signal client closure
                break
            except GeneratorExit:
                 logger.debug("SSE client disconnected.")
                 break # Exit loop if client disconnects
            except Exception as e:
                 logger.error(f"Error in SSE generate loop: {e}", exc_info=True)
                 yield f"data: ERROR: Server error in log stream - {e}\n\n"
                 yield "data: [DONE]\n\n" # Send DONE to signal client closure on error
                 break

    response = Response(generate(), mimetype='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no' # Useful for Nginx proxying
    return response

@app.route('/view-igs')
def view_igs():
    form = FlaskForm()
    processed_igs = ProcessedIg.query.order_by(ProcessedIg.package_name, ProcessedIg.version).all()
    processed_ids = {(ig.package_name, ig.version) for ig in processed_igs}
    packages_dir = app.config['FHIR_PACKAGES_DIR']
    packages, errors, duplicate_groups = list_downloaded_packages(packages_dir)
    if errors:
        flash(f"Warning: Errors encountered while listing packages: {', '.join(errors)}", "warning")
    colors = ['bg-warning', 'bg-info', 'bg-success', 'bg-danger', 'bg-secondary']
    group_colors = {}
    for i, name in enumerate(duplicate_groups.keys()):
        group_colors[name] = colors[i % len(colors)]
    return render_template('cp_downloaded_igs.html', form=form, packages=packages,
                           processed_list=processed_igs, processed_ids=processed_ids,
                           duplicate_groups=duplicate_groups, group_colors=group_colors,
                           site_name='FHIRFLARE IG Toolkit', now=datetime.datetime.now(),
                           config=app.config)

@app.route('/about')
def about():
    """Renders the about page."""
    # The app_mode is automatically injected by the context processor
    return render_template('about.html',
                           title="About", # Optional title for the page
                           site_name='FHIRFLARE IG Toolkit') # Or get from config


@app.route('/push-igs', methods=['GET'])
def push_igs():
    # form = FlaskForm() # OLD - Replace this line
    form = IgImportForm() # Use a real form class that has CSRF handling built-in
    processed_igs = ProcessedIg.query.order_by(ProcessedIg.package_name, ProcessedIg.version).all()
    processed_ids = {(ig.package_name, ig.version) for ig in processed_igs}
    packages_dir = app.config['FHIR_PACKAGES_DIR']
    packages, errors, duplicate_groups = list_downloaded_packages(packages_dir)
    if errors:
        flash(f"Warning: Errors encountered while listing packages: {', '.join(errors)}", "warning")
    colors = ['bg-warning', 'bg-info', 'bg-success', 'bg-danger', 'bg-secondary']
    group_colors = {}
    for i, name in enumerate(duplicate_groups.keys()):
        group_colors[name] = colors[i % len(colors)]
    return render_template('cp_push_igs.html', form=form, packages=packages, # Pass the form instance
                           processed_list=processed_igs, processed_ids=processed_ids,
                           duplicate_groups=duplicate_groups, group_colors=group_colors,
                           site_name='FHIRFLARE IG Toolkit', now=datetime.datetime.now(),
                           api_key=app.config['API_KEY'], config=app.config)

@app.route('/process-igs', methods=['POST'])
def process_ig():
    form = FlaskForm() # Assuming a basic FlaskForm for CSRF protection
    if form.validate_on_submit():
        filename = request.form.get('filename')
        # --- Keep existing filename and path validation ---
        if not filename or not filename.endswith('.tgz'):
            flash("Invalid package file selected.", "error")
            return redirect(url_for('view_igs'))
        tgz_path = os.path.join(app.config['FHIR_PACKAGES_DIR'], filename)
        if not os.path.exists(tgz_path):
            flash(f"Package file not found: {filename}", "error")
            return redirect(url_for('view_igs'))

        name, version = services.parse_package_filename(filename)
        if not name or not version:
            # Filename didn't encode name/version (e.g. "package.tgz") — read package.json inside
            try:
                with tarfile.open(tgz_path, "r:gz") as _tar:
                    _pkg_member = _tar.getmember("package/package.json")
                    with _tar.extractfile(_pkg_member) as _f:
                        _pkg_data = json.loads(_f.read().decode('utf-8-sig'))
                        name = _pkg_data.get('name', name) or name
                        version = _pkg_data.get('version', version) or version
                logger.info(f"Resolved name/version from package.json: {name}#{version}")
            except Exception as _e:
                logger.warning(f"Could not read package.json from {filename}: {_e}")
            if not name:
                name = filename[:-4].replace('_', '.')
            if not version:
                version = 'unknown'
            logger.warning(f"Using package.json/fallback naming for {filename} -> {name}#{version}")

        try:
            logger.info(f"Starting processing for {name}#{version} from file {filename}")
            # This now returns the conformance map too
            package_info = services.process_package_file(tgz_path)

            if package_info.get('errors'):
                flash(f"Processing completed with errors for {name}#{version}: {', '.join(package_info['errors'])}", "warning")

            # (Keep existing optional_usage_dict logic)
            optional_usage_dict = {
                info['name']: True
                for info in package_info.get('resource_types_info', [])
                if info.get('optional_usage')
            }
            logger.debug(f"Optional usage elements identified: {optional_usage_dict}")

            # Find existing or create new DB record
            existing_ig = ProcessedIg.query.filter_by(package_name=name, version=version).first()

            if existing_ig:
                logger.info(f"Updating existing processed record for {name}#{version}")
                processed_ig = existing_ig
            else:
                logger.info(f"Creating new processed record for {name}#{version}")
                processed_ig = ProcessedIg(package_name=name, version=version)
                db.session.add(processed_ig)

            # Update all fields
            processed_ig.processed_date = datetime.datetime.now(tz=datetime.timezone.utc)
            processed_ig.resource_types_info = package_info.get('resource_types_info', [])
            processed_ig.must_support_elements = package_info.get('must_support_elements')
            processed_ig.examples = package_info.get('examples')
            processed_ig.complies_with_profiles = package_info.get('complies_with_profiles', [])
            processed_ig.imposed_profiles = package_info.get('imposed_profiles', [])
            processed_ig.optional_usage_elements = optional_usage_dict
            # --- ADD THIS LINE: Save the extracted conformance map ---
            processed_ig.search_param_conformance = package_info.get('search_param_conformance') # Get map from results
            # --- END ADD ---

            db.session.commit() # Commit all changes
            flash(f"Successfully processed {name}#{version}!", "success")

        except Exception as e:
            db.session.rollback() # Rollback on error
            logger.error(f"Error processing IG {filename}: {str(e)}", exc_info=True)
            flash(f"Error processing IG '{filename}': {str(e)}", "error")
    else:
        # Handle CSRF or other form validation errors
        logger.warning(f"Form validation failed for process-igs: {form.errors}")
        flash("CSRF token missing or invalid, or other form error.", "error")

    return redirect(url_for('view_igs'))

# --- End of /process-igs Function ---

@app.route('/delete-ig', methods=['POST'])
def delete_ig():
    form = FlaskForm()
    if form.validate_on_submit():
        filename = request.form.get('filename')
        if not filename or not filename.endswith('.tgz'):
            flash("Invalid package file specified.", "error")
            return redirect(url_for('view_igs'))
        tgz_path = os.path.join(app.config['FHIR_PACKAGES_DIR'], filename)
        metadata_path = tgz_path.replace('.tgz', '.metadata.json')
        deleted_files = []
        errors = []
        if os.path.exists(tgz_path):
            try:
                os.remove(tgz_path)
                deleted_files.append(filename)
                logger.info(f"Deleted package file: {tgz_path}")
            except OSError as e:
                errors.append(f"Could not delete {filename}: {e}")
                logger.error(f"Error deleting {tgz_path}: {e}")
        else:
            flash(f"Package file not found: {filename}", "warning")
        if os.path.exists(metadata_path):
            try:
                os.remove(metadata_path)
                deleted_files.append(os.path.basename(metadata_path))
                logger.info(f"Deleted metadata file: {metadata_path}")
            except OSError as e:
                errors.append(f"Could not delete metadata for {filename}: {e}")
                logger.error(f"Error deleting {metadata_path}: {e}")
        if errors:
            for error in errors:
                flash(error, "error")
        elif deleted_files:
            flash(f"Deleted: {', '.join(deleted_files)}", "success")
        else:
            flash("No files found to delete.", "info")
    else:
        logger.warning(f"Form validation failed for delete-ig: {form.errors}")
        flash("CSRF token missing or invalid.", "error")
    return redirect(url_for('view_igs'))

@app.route('/unload-ig', methods=['POST'])
def unload_ig():
    form = FlaskForm()
    if form.validate_on_submit():
        ig_id = request.form.get('ig_id')
        try:
            ig_id_int = int(ig_id)
            processed_ig = db.session.get(ProcessedIg, ig_id_int)
            if processed_ig:
                try:
                    pkg_name = processed_ig.package_name
                    pkg_version = processed_ig.version
                    db.session.delete(processed_ig)
                    db.session.commit()
                    flash(f"Unloaded processed data for {pkg_name}#{pkg_version}", "success")
                    logger.info(f"Unloaded DB record for {pkg_name}#{pkg_version} (ID: {ig_id_int})")
                except Exception as e:
                    db.session.rollback()
                    flash(f"Error unloading package data: {str(e)}", "error")
                    logger.error(f"Error deleting ProcessedIg record ID {ig_id_int}: {e}", exc_info=True)
            else:
                flash(f"Processed package data not found with ID: {ig_id}", "error")
                logger.warning(f"Attempted to unload non-existent ProcessedIg record ID: {ig_id}")
        except ValueError:
            flash("Invalid package ID provided.", "error")
            logger.warning(f"Invalid ID format received for unload-ig: {ig_id}")
        except Exception as e:
            flash(f"An unexpected error occurred during unload: {str(e)}", "error")
            logger.error(f"Unexpected error in unload_ig for ID {ig_id}: {e}", exc_info=True)
    else:
        logger.warning(f"Form validation failed for unload-ig: {form.errors}")
        flash("CSRF token missing or invalid.", "error")
    return redirect(url_for('view_igs'))

@app.route('/view-ig/<int:processed_ig_id>')
def view_ig(processed_ig_id):
    processed_ig = db.session.get(ProcessedIg, processed_ig_id)
    if not processed_ig:
        flash(f"Processed IG with ID {processed_ig_id} not found.", "error")
        return redirect(url_for('view_igs'))
    profile_list = [t for t in processed_ig.resource_types_info if t.get('is_profile')]
    base_list = [t for t in processed_ig.resource_types_info if not t.get('is_profile')]
    examples_by_type = processed_ig.examples or {}
    optional_usage_elements = processed_ig.optional_usage_elements or {}
    complies_with_profiles = processed_ig.complies_with_profiles or []
    imposed_profiles = processed_ig.imposed_profiles or []
    logger.debug(f"Viewing IG {processed_ig.package_name}#{processed_ig.version}: "
                 f"{len(profile_list)} profiles, {len(base_list)} base resources, "
                 f"{len(optional_usage_elements)} optional elements")
    return render_template('cp_view_processed_ig.html',
                           title=f"View {processed_ig.package_name}#{processed_ig.version}",
                           processed_ig=processed_ig,
                           profile_list=profile_list,
                           base_list=base_list,
                           examples_by_type=examples_by_type,
                           site_name='FHIRFLARE IG Toolkit',
                           now=datetime.datetime.now(),
                           complies_with_profiles=complies_with_profiles,
                           imposed_profiles=imposed_profiles,
                           optional_usage_elements=optional_usage_elements,
                           config=current_app.config)

@app.route('/get-example')
@swag_from({
    'tags': ['Package Management'],
    'summary': 'Get a specific example resource from a package.',
    'description': 'Retrieves the content of an example JSON file from a specified FHIR package and version.',
    'parameters': [
        {'name': 'package_name', 'in': 'query', 'type': 'string', 'required': True, 'description': 'Name of the FHIR package.'},
        {'name': 'version', 'in': 'query', 'type': 'string', 'required': True, 'description': 'Version of the FHIR package.'},
        {'name': 'filename', 'in': 'query', 'type': 'string', 'required': True, 'description': 'Path to the example file within the package (e.g., "package/Patient-example.json").'},
        {'name': 'include_narrative', 'in': 'query', 'type': 'boolean', 'required': False, 'default': False, 'description': 'Whether to include the HTML narrative in the response.'}
    ],
    'responses': {
        '200': {'description': 'The example FHIR resource in JSON format.', 'schema': {'type': 'object'}},
        '400': {'description': 'Missing required query parameters or invalid file path.'},
        '404': {'description': 'Package or example file not found.'},
        '500': {'description': 'Server error during file retrieval or processing.'}
    }
})
def get_example():
    package_name = request.args.get('package_name')
    version = request.args.get('version')
    filename = request.args.get('filename')
    include_narrative = request.args.get('include_narrative', 'false').lower() == 'true'
    if not all([package_name, version, filename]):
        logger.warning("get_example: Missing query parameters: package_name=%s, version=%s, filename=%s", package_name, version, filename)
        return jsonify({"error": "Missing required query parameters: package_name, version, filename"}), 400
    if not filename.startswith('package/') or '..' in filename:
        logger.warning(f"Invalid example file path requested: {filename}")
        return jsonify({"error": "Invalid example file path."}), 400
    packages_dir = current_app.config.get('FHIR_PACKAGES_DIR')
    if not packages_dir:
        logger.error("FHIR_PACKAGES_DIR not configured.")
        return jsonify({"error": "Server configuration error: Package directory not set."}), 500
    tgz_filename = services.construct_tgz_filename(package_name, version)
    tgz_path = os.path.join(packages_dir, tgz_filename)
    if not os.path.exists(tgz_path):
        logger.error(f"Package file not found: {tgz_path}")
        return jsonify({"error": f"Package {package_name}#{version} not found"}), 404
    try:
        with tarfile.open(tgz_path, "r:gz") as tar:
            try:
                example_member = tar.getmember(filename)
                with tar.extractfile(example_member) as example_fileobj:
                    content_bytes = example_fileobj.read()
                content_string = content_bytes.decode('utf-8-sig')
                content = json.loads(content_string)
                if not include_narrative:
                    content = services.remove_narrative(content, include_narrative=False)
                filtered_content_string = json.dumps(content, separators=(',', ':'), sort_keys=False)
                return Response(filtered_content_string, mimetype='application/json')
            except KeyError:
                logger.error(f"Example file '{filename}' not found within {tgz_filename}")
                return jsonify({"error": f"Example file '{os.path.basename(filename)}' not found in package."}), 404
            except json.JSONDecodeError as e:
                logger.error(f"JSON parsing error for example '{filename}' in {tgz_filename}: {e}")
                return jsonify({"error": f"Invalid JSON in example file: {str(e)}"}), 500
            except UnicodeDecodeError as e:
                logger.error(f"Encoding error reading example '{filename}' from {tgz_filename}: {e}")
                return jsonify({"error": f"Error decoding example file (invalid UTF-8?): {str(e)}"}), 500
            except tarfile.TarError as e:
                logger.error(f"TarError reading example '{filename}' from {tgz_filename}: {e}")
                return jsonify({"error": f"Error reading package archive: {str(e)}"}), 500
    except tarfile.TarError as e:
        logger.error(f"Error opening package file {tgz_path}: {e}")
        return jsonify({"error": f"Error reading package archive: {str(e)}"}), 500
    except FileNotFoundError:
        logger.error(f"Package file disappeared: {tgz_path}")
        return jsonify({"error": f"Package file not found: {package_name}#{version}"}), 404
    except Exception as e:
        logger.error(f"Unexpected error getting example '{filename}' from {tgz_filename}: {e}", exc_info=True)
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500

#----------------------------------------------------------------------new
def collect_all_structure_definitions(tgz_path):
    """Collect all StructureDefinitions from a .tgz package."""
    structure_definitions = {}
    try:
        with tarfile.open(tgz_path, "r:gz") as tar:
            for member in tar:
                if not (member.isfile() and member.name.startswith('package/') and member.name.lower().endswith('.json')):
                    continue
                if os.path.basename(member.name).lower() in ['package.json', '.index.json', 'validation-summary.json', 'validation-oo.json']:
                    continue
                fileobj = None
                try:
                    fileobj = tar.extractfile(member)
                    if fileobj:
                        content_bytes = fileobj.read()
                        content_string = content_bytes.decode('utf-8-sig')
                        data = json.loads(content_string)
                        if isinstance(data, dict) and data.get('resourceType') == 'StructureDefinition':
                            sd_url = data.get('url')
                            if sd_url:
                                structure_definitions[sd_url] = data
                except Exception as e:
                    logger.warning(f"Could not read/parse potential SD {member.name}, skipping: {e}")
                finally:
                    if fileobj:
                        fileobj.close()
    except Exception as e:
        logger.error(f"Unexpected error collecting StructureDefinitions from {tgz_path}: {e}", exc_info=True)
    return structure_definitions

def generate_snapshot(structure_def, core_package_path, local_package_path):
    """Generate a snapshot by merging the differential with the base StructureDefinition."""
    if 'snapshot' in structure_def:
        return structure_def

    # Fetch all StructureDefinitions from the local package for reference resolution
    local_sds = collect_all_structure_definitions(local_package_path)

    # Get the base StructureDefinition from the core package
    base_url = structure_def.get('baseDefinition')
    if not base_url:
        logger.error("No baseDefinition found in StructureDefinition.")
        return structure_def

    resource_type = structure_def.get('type')
    base_sd_data, _ = services.find_and_extract_sd(core_package_path, resource_type, profile_url=base_url)
    if not base_sd_data or 'snapshot' not in base_sd_data:
        logger.error(f"Could not fetch or find snapshot in base StructureDefinition: {base_url}")
        return structure_def

    # Copy the base snapshot elements
    snapshot_elements = deepcopy(base_sd_data['snapshot']['element'])
    differential_elements = structure_def.get('differential', {}).get('element', [])

    # Map snapshot elements by path and id for easier lookup
    snapshot_by_path = {el['path']: el for el in snapshot_elements}
    snapshot_by_id = {el['id']: el for el in snapshot_elements if 'id' in el}

    # Process differential elements
    for diff_el in differential_elements:
        diff_path = diff_el.get('path')
        diff_id = diff_el.get('id')

        # Resolve extensions or referenced types
        if 'type' in diff_el:
            for type_info in diff_el['type']:
                if 'profile' in type_info:
                    for profile_url in type_info['profile']:
                        if profile_url in local_sds:
                            # Add elements from the referenced StructureDefinition
                            ref_sd = local_sds[profile_url]
                            ref_elements = ref_sd.get('snapshot', {}).get('element', []) or ref_sd.get('differential', {}).get('element', [])
                            for ref_el in ref_elements:
                                # Adjust paths to fit within the current structure
                                ref_path = ref_el.get('path')
                                if ref_path.startswith(ref_sd.get('type')):
                                    new_path = diff_path + ref_path[len(ref_sd.get('type')):]
                                    new_el = deepcopy(ref_el)
                                    new_el['path'] = new_path
                                    new_el['id'] = diff_id + ref_path[len(ref_sd.get('type')):]
                                    snapshot_elements.append(new_el)

        # Find matching element in snapshot
        target_el = snapshot_by_id.get(diff_id) or snapshot_by_path.get(diff_path)
        if target_el:
            # Update existing element with differential constraints
            target_el.update(diff_el)
        else:
            # Add new element (e.g., extensions or new slices)
            snapshot_elements.append(diff_el)

    structure_def['snapshot'] = {'element': snapshot_elements}
    return structure_def

@app.route('/get-structure')
@swag_from({
    'tags': ['Package Management'],
    'summary': 'Get a StructureDefinition from a package.',
    'description': 'Retrieves a StructureDefinition, optionally generating or filtering for snapshot/differential views.',
    'parameters': [
        {'name': 'package_name', 'in': 'query', 'type': 'string', 'required': True},
        {'name': 'version', 'in': 'query', 'type': 'string', 'required': True},
        {'name': 'resource_type', 'in': 'query', 'type': 'string', 'required': True, 'description': 'The resource type or profile ID.'},
        {'name': 'view', 'in': 'query', 'type': 'string', 'required': False, 'default': 'snapshot', 'enum': ['snapshot', 'differential']},
        {'name': 'include_narrative', 'in': 'query', 'type': 'boolean', 'required': False, 'default': False},
        {'name': 'raw', 'in': 'query', 'type': 'boolean', 'required': False, 'default': False, 'description': 'If true, returns the raw SD JSON.'},
        {'name': 'profile_url', 'in': 'query', 'type': 'string', 'required': False, 'description': 'Canonical URL of the profile to retrieve.'}
    ],
    'responses': {
        '200': {
            'description': 'The StructureDefinition data.',
            'schema': {
                'type': 'object',
                'properties': {
                    'elements': {'type': 'array', 'items': {'type': 'object'}},
                    'must_support_paths': {'type': 'array', 'items': {'type': 'string'}},
                    'search_parameters': {'type': 'array', 'items': {'type': 'object'}},
                    'fallback_used': {'type': 'boolean'},
                    'source_package': {'type': 'string'}
                }
            }
         },
        '400': {'description': 'Missing required parameters.'},
        '404': {'description': 'StructureDefinition not found.'},
        '500': {'description': 'Server error.'}
    }
})
def get_structure():
    package_name = request.args.get('package_name')
    version = request.args.get('version')
    resource_type = request.args.get('resource_type')
    view = request.args.get('view', 'snapshot')
    include_narrative = request.args.get('include_narrative', 'false').lower() == 'true'
    raw = request.args.get('raw', 'false').lower() == 'true'
    profile_url = request.args.get('profile_url')
    if not all([package_name, version, resource_type]):
        logger.warning("get_structure: Missing query parameters: package_name=%s, version=%s, resource_type=%s", package_name, version, resource_type)
        return jsonify({"error": "Missing required query parameters: package_name, version, resource_type"}), 400
    packages_dir = current_app.config.get('FHIR_PACKAGES_DIR')
    if not packages_dir:
        logger.error("FHIR_PACKAGES_DIR not configured.")
        return jsonify({"error": "Server configuration error: Package directory not set."}), 500
    tgz_filename = services.construct_tgz_filename(package_name, version)
    tgz_path = os.path.join(packages_dir, tgz_filename)
    core_package_name, core_package_version = services.CANONICAL_PACKAGE
    core_tgz_filename = services.construct_tgz_filename(core_package_name, core_package_version)
    core_tgz_path = os.path.join(packages_dir, core_tgz_filename)
    sd_data = None
    search_params_data = []
    fallback_used = False
    source_package_id = f"{package_name}#{version}"
    base_resource_type_for_sp = None
    logger.debug(f"Attempting to find SD for '{resource_type}' in {tgz_filename}")
    primary_package_exists = os.path.exists(tgz_path)
    core_package_exists = os.path.exists(core_tgz_path)
    if primary_package_exists:
        try:
            sd_data, _ = services.find_and_extract_sd(tgz_path, resource_type, profile_url=profile_url, include_narrative=include_narrative, raw=raw)
            if sd_data:
                base_resource_type_for_sp = sd_data.get('type')
                logger.debug(f"Determined base resource type '{base_resource_type_for_sp}' from primary SD '{resource_type}'")
        except Exception as e:
            logger.error(f"Unexpected error extracting SD '{resource_type}' from primary package {tgz_path}: {e}", exc_info=True)
            sd_data = None
    if sd_data is None:
        logger.info(f"SD for '{resource_type}' not found or failed to load from {source_package_id}. Attempting fallback to {services.CANONICAL_PACKAGE_ID}.")
        if not core_package_exists:
            error_message = f"SD for '{resource_type}' not found in primary package, and core package is missing." if primary_package_exists else f"Primary package {package_name}#{version} and core package are missing."
            return jsonify({"error": error_message}), 500 if primary_package_exists else 404
        try:
            sd_data, _ = services.find_and_extract_sd(core_tgz_path, resource_type, profile_url=profile_url, include_narrative=include_narrative, raw=raw)
            if sd_data is not None:
                fallback_used = True
                source_package_id = services.CANONICAL_PACKAGE_ID
                base_resource_type_for_sp = sd_data.get('type')
                logger.info(f"Found SD for '{resource_type}' in fallback package {source_package_id}. Base type: '{base_resource_type_for_sp}'")
        except Exception as e:
            logger.error(f"Unexpected error extracting SD '{resource_type}' from fallback {core_tgz_path}: {e}", exc_info=True)
            return jsonify({"error": f"Unexpected error reading fallback StructureDefinition: {str(e)}"}), 500
    if not sd_data:
        logger.error(f"SD for '{resource_type}' could not be found in primary or fallback packages.")
        return jsonify({"error": f"StructureDefinition for '{resource_type}' not found."}), 404
    
    # Generate snapshot if missing
    if 'snapshot' not in sd_data:
        logger.info(f"Snapshot missing for {resource_type}. Generating snapshot...")
        sd_data = generate_snapshot(sd_data, core_tgz_path, tgz_path)

    if raw:
        return Response(json.dumps(sd_data, indent=None, separators=(',', ':')), mimetype='application/json')

    # Prepare elements based on the view
    snapshot_elements = sd_data.get('snapshot', {}).get('element', [])
    differential_elements = sd_data.get('differential', {}).get('element', [])
    differential_ids = {el.get('id') for el in differential_elements if el.get('id')}
    logger.debug(f"Found {len(differential_ids)} unique IDs in differential.")

    # Select elements based on the view
    enriched_elements = []
    if view == 'snapshot':
        if snapshot_elements:
            logger.debug(f"Processing {len(snapshot_elements)} snapshot elements for Snapshot view.")
            for element in snapshot_elements:
                element_id = element.get('id')
                element['isInDifferential'] = bool(element_id and element_id in differential_ids)
                enriched_elements.append(element)
        else:
            logger.warning(f"No snapshot elements found for {resource_type} in {source_package_id} for Snapshot view.")
    else:  # Differential, Must Support, Key Elements views use differential elements as a base
        if differential_elements:
            logger.debug(f"Processing {len(differential_elements)} differential elements for {view} view.")
            for element in differential_elements:
                element['isInDifferential'] = True
                enriched_elements.append(element)
        else:
            logger.warning(f"No differential elements found for {resource_type} in {source_package_id} for {view} view.")

    enriched_elements = [services.remove_narrative(el, include_narrative=include_narrative) for el in enriched_elements]

    must_support_paths = []
    processed_ig_record = ProcessedIg.query.filter_by(package_name=package_name, version=version).first()
    if processed_ig_record and processed_ig_record.must_support_elements:
        ms_elements_dict = processed_ig_record.must_support_elements
        must_support_paths = ms_elements_dict.get(resource_type, [])
        if not must_support_paths and base_resource_type_for_sp:
            must_support_paths = ms_elements_dict.get(base_resource_type_for_sp, [])
            if must_support_paths:
                logger.debug(f"Retrieved {len(must_support_paths)} MS paths using base type key '{base_resource_type_for_sp}' from DB.")
        elif must_support_paths:
            logger.debug(f"Retrieved {len(must_support_paths)} MS paths using profile key '{resource_type}' from DB.")
        else:
            logger.debug(f"No specific MS paths found for keys '{resource_type}' or '{base_resource_type_for_sp}' in DB.")
    else:
        logger.debug(f"No processed IG record or no must_support_elements found in DB for {package_name}#{version}")

    if base_resource_type_for_sp and primary_package_exists:
        try:
            logger.info(f"Fetching SearchParameters for base type '{base_resource_type_for_sp}' from primary package {tgz_path}")
            search_params_data = services.find_and_extract_search_params(tgz_path, base_resource_type_for_sp)
        except Exception as e:
            logger.error(f"Error extracting SearchParameters for '{base_resource_type_for_sp}' from primary package {tgz_path}: {e}", exc_info=True)
            search_params_data = []
    elif not primary_package_exists:
        logger.warning(f"Original package {tgz_path} not found, cannot search it for specific SearchParameters.")
    elif not base_resource_type_for_sp:
        logger.warning(f"Base resource type could not be determined for '{resource_type}', cannot search for SearchParameters.")
    if not search_params_data and base_resource_type_for_sp and core_package_exists:
        logger.info(f"No relevant SearchParameters found in primary package for '{base_resource_type_for_sp}'. Searching core package {core_tgz_path}.")
        try:
            search_params_data = services.find_and_extract_search_params(core_tgz_path, base_resource_type_for_sp)
            if search_params_data:
                logger.info(f"Found {len(search_params_data)} SearchParameters for '{base_resource_type_for_sp}' in core package.")
        except Exception as e:
            logger.error(f"Error extracting SearchParameters for '{base_resource_type_for_sp}' from core package {core_tgz_path}: {e}", exc_info=True)
            search_params_data = []
    elif not search_params_data and not core_package_exists:
        logger.warning(f"Core package {core_tgz_path} not found, cannot perform fallback search for SearchParameters.")
    search_param_conformance_rules = {}
    if base_resource_type_for_sp:
        if processed_ig_record:
            if hasattr(processed_ig_record, 'search_param_conformance') and processed_ig_record.search_param_conformance:
                all_conformance_data = processed_ig_record.search_param_conformance
                search_param_conformance_rules = all_conformance_data.get(base_resource_type_for_sp, {})
                logger.debug(f"Retrieved conformance rules for {base_resource_type_for_sp} from DB: {search_param_conformance_rules}")
            else:
                logger.warning(f"ProcessedIg record found, but 'search_param_conformance' attribute/data is missing or empty for {package_name}#{version}.")
        else:
            logger.warning(f"No ProcessedIg record found for {package_name}#{version} to get conformance rules.")
        if search_params_data:
            logger.debug(f"Merging conformance data into {len(search_params_data)} search parameters.")
            for param in search_params_data:
                param_code = param.get('code')
                if param_code:
                    conformance_level = search_param_conformance_rules.get(param_code, 'Optional')
                    param['conformance'] = conformance_level
                else:
                    param['conformance'] = 'Unknown'
            logger.debug("Finished merging conformance data.")
        else:
            logger.debug(f"No search parameters found for {base_resource_type_for_sp} to merge conformance data into.")
    else:
        logger.warning(f"Cannot fetch conformance data because base resource type (e.g., Patient) for '{resource_type}' could not be determined.")
        for param in search_params_data:
            if 'conformance' not in param or param['conformance'] == 'N/A':
                param['conformance'] = 'Optional'
    response_data = {
        'elements': enriched_elements,
        'must_support_paths': must_support_paths,
        'search_parameters': search_params_data,
        'fallback_used': fallback_used,
        'source_package': source_package_id
    }
    return Response(json.dumps(response_data, indent=None, separators=(',', ':')), mimetype='application/json')
#------------------------------------------------------------------------


@app.route('/get-package-metadata')
@swag_from({
    'tags': ['Package Management'],
    'summary': 'Get metadata for a downloaded package.',
    'parameters': [
        {'name': 'package_name', 'in': 'query', 'type': 'string', 'required': True},
        {'name': 'version', 'in': 'query', 'type': 'string', 'required': True}
    ],
    'responses': {
        '200': {
            'description': 'Package metadata.',
            'schema': {
                'type': 'object',
                'properties': {
                    'package_name': {'type': 'string'},
                    'version': {'type': 'string'},
                    'dependency_mode': {'type': 'string'},
                    'imported_dependencies': {'type': 'array', 'items': {'type': 'object'}},
                    'complies_with_profiles': {'type': 'array', 'items': {'type': 'string'}},
                    'imposed_profiles': {'type': 'array', 'items': {'type': 'string'}}
                }
            }
        },
        '400': {'description': 'Missing parameters.'},
        '404': {'description': 'Metadata not found.'},
        '500': {'description': 'Server error.'}
    }
})
def get_package_metadata():
    package_name = request.args.get('package_name')
    version = request.args.get('version')
    if not package_name or not version:
        return jsonify({'error': 'Missing package_name or version parameter'}), 400
    try:
        metadata = services.get_package_metadata(package_name, version)
        if metadata:
            return jsonify({
                'package_name': metadata.get('package_name'),
                'version': metadata.get('version'),
                'dependency_mode': metadata.get('dependency_mode'),
                'imported_dependencies': metadata.get('imported_dependencies', []),
                'complies_with_profiles': metadata.get('complies_with_profiles', []),
                'imposed_profiles': metadata.get('imposed_profiles', [])
            })
        else:
            return jsonify({'error': 'Metadata file not found for this package version.'}), 404
    except Exception as e:
        logger.error(f"Error retrieving metadata for {package_name}#{version}: {e}", exc_info=True)
        return jsonify({'error': f'Error retrieving metadata: {str(e)}'}), 500

@app.route('/api/import-ig', methods=['POST'])
@swag_from({
    'tags': ['Package Management'],
    'summary': 'Import a FHIR Implementation Guide via API.',
    'description': 'Downloads and processes a FHIR IG and its dependencies.',
    'security': [{'ApiKeyAuth': []}],
    'consumes': ['application/json'],
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'required': ['package_name', 'version'],
                'properties': {
                    'package_name': {'type': 'string', 'example': 'hl7.fhir.us.core'},
                    'version': {'type': 'string', 'example': '6.1.0'},
                    'dependency_mode': {
                        'type': 'string', 'enum': ['recursive', 'patch-canonical', 'tree-shaking', 'direct'],
                        'default': 'recursive'
                    }
                }
            }
        }
    ],
    'responses': {
        '200': {'description': 'Package imported successfully or with warnings.'},
        '400': {'description': 'Invalid request (e.g., missing fields, invalid mode).'},
        '404': {'description': 'Package not found on registry.'},
        '500': {'description': 'Server error during import.'}
    }
})
def api_import_ig():
    auth_error = check_api_key()
    if auth_error:
        return auth_error
    if not request.is_json:
        return jsonify({"status": "error", "message": "Request must be JSON"}), 400
    data = request.get_json()
    package_name = data.get('package_name')
    version = data.get('version')
    dependency_mode = data.get('dependency_mode', 'recursive')
    if not package_name or not version:
        return jsonify({"status": "error", "message": "Missing package_name or version"}), 400
    if not (isinstance(package_name, str) and isinstance(version, str) and
            re.match(r'^[a-zA-Z0-9\-\.]+$', package_name) and
            re.match(r'^[a-zA-Z0-9\.\-\+]+$', version)):
        return jsonify({"status": "error", "message": "Invalid characters in package name or version"}), 400
    valid_modes = ['recursive', 'patch-canonical', 'tree-shaking', 'direct']
    if dependency_mode not in valid_modes:
        return jsonify({"status": "error", "message": f"Invalid dependency mode: {dependency_mode}. Must be one of {valid_modes}"}), 400
    try:
        result = services.import_package_and_dependencies(package_name, version, dependency_mode=dependency_mode)
        if result['errors'] and not result['downloaded']:
            error_msg = f"Failed to import {package_name}#{version}: {result['errors'][0]}"
            logger.error(f"[API] Import failed: {error_msg}")
            status_code = 404 if "404" in result['errors'][0] else 500
            return jsonify({"status": "error", "message": error_msg}), status_code
        package_filename = services.construct_tgz_filename(package_name, version)
        packages_dir = current_app.config.get('FHIR_PACKAGES_DIR', '/app/instance/fhir_packages')
        package_path = os.path.join(packages_dir, package_filename)
        complies_with_profiles = []
        imposed_profiles = []
        processing_errors = []
        if os.path.exists(package_path):
            logger.info(f"[API] Processing downloaded package {package_path} for metadata.")
            process_result = services.process_package_file(package_path)
            complies_with_profiles = process_result.get('complies_with_profiles', [])
            imposed_profiles = process_result.get('imposed_profiles', [])
            if process_result.get('errors'):
                processing_errors.extend(process_result['errors'])
                logger.warning(f"[API] Errors during post-import processing of {package_name}#{version}: {processing_errors}")
        else:
            logger.warning(f"[API] Package file {package_path} not found after reported successful download.")
            processing_errors.append("Package file disappeared after download.")
        all_packages, errors, duplicate_groups_after = list_downloaded_packages(packages_dir)
        duplicates_found = []
        for name, versions in duplicate_groups_after.items():
            duplicates_found.append(f"{name} (Versions present: {', '.join(versions)})")
        response_status = "success"
        response_message = "Package imported successfully."
        if result['errors'] or processing_errors:
            response_status = "warning"
            response_message = "Package imported, but some errors occurred during processing or dependency handling."
            all_issues = result.get('errors', []) + processing_errors
            logger.warning(f"[API] Import for {package_name}#{version} completed with warnings/errors: {all_issues}")
        response = {
            "status": response_status,
            "message": response_message,
            "package_name": package_name,
            "version": version,
            "dependency_mode": dependency_mode,
            "dependencies_processed": result.get('dependencies', []),
            "complies_with_profiles": complies_with_profiles,
            "imposed_profiles": imposed_profiles,
            "processing_issues": result.get('errors', []) + processing_errors,
            "duplicate_packages_present": duplicates_found
        }
        return jsonify(response), 200
    except Exception as e:
        logger.error(f"[API] Unexpected error in api_import_ig for {package_name}#{version}: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": f"Unexpected server error during import: {str(e)}"}), 500

@app.route('/api/push-ig', methods=['POST'])
@csrf.exempt  # Retain CSRF exemption as specified
@swag_from({
    'tags': ['Package Management'],
    'summary': 'Push a FHIR Implementation Guide to a server via API.',
    'description': 'Uploads resources from a specified FHIR IG (and optionally its dependencies) to a target FHIR server. Returns an NDJSON stream of progress.',
    'security': [{'ApiKeyAuth': []}],
    'consumes': ['application/json'],
    'produces': ['application/x-ndjson'],
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'required': ['package_name', 'version', 'fhir_server_url'],
                'properties': {
                    'package_name': {'type': 'string', 'example': 'hl7.fhir.us.core'},
                    'version': {'type': 'string', 'example': '6.1.0'},
                    'fhir_server_url': {'type': 'string', 'format': 'url', 'example': 'http://localhost:8080/fhir'},
                    'include_dependencies': {'type': 'boolean', 'default': True},
                    'auth_type': {'type': 'string', 'enum': ['apiKey', 'bearerToken', 'basic', 'none'], 'default': 'none'},
                    'auth_token': {'type': 'string', 'description': 'Required if auth_type is bearerToken or basic (for basic, use "Basic <base64_encoded_user:pass>")'},
                    'username': {'type': 'string', 'description': 'Required if auth_type is basic'},
                    'password': {'type': 'string', 'format': 'password', 'description': 'Required if auth_type is basic'},
                    'resource_types_filter': {'type': 'array', 'items': {'type': 'string'}, 'description': 'List of resource types to include.'},
                    'skip_files': {'type': 'array', 'items': {'type': 'string'}, 'description': 'List of specific file paths within packages to skip.'},
                    'dry_run': {'type': 'boolean', 'default': False},
                    'verbose': {'type': 'boolean', 'default': False},
                    'force_upload': {'type': 'boolean', 'default': False, 'description': 'If true, uploads resources even if they appear identical to server versions.'}
                }
            }
        }
    ],
    'responses': {
        '200': {'description': 'NDJSON stream of push progress and results.'},
        '400': {'description': 'Invalid request parameters.'},
        '401': {'description': 'Authentication error.'},
        '404': {'description': 'Package not found locally.'},
        '500': {'description': 'Server error during push operation setup.'}
    }
})
def api_push_ig():
    auth_error = check_api_key()
    if auth_error: return auth_error
    if not request.is_json: return jsonify({"status": "error", "message": "Request must be JSON"}), 400

    data = request.get_json()
    package_name = data.get('package_name')
    version = data.get('version')
    fhir_server_url = data.get('fhir_server_url')
    include_dependencies = data.get('include_dependencies', True)
    auth_type = data.get('auth_type', 'none')
    auth_token = data.get('auth_token')
    username = data.get('username')  # ADD: Extract username
    password = data.get('password')  # ADD: Extract password
    resource_types_filter_raw = data.get('resource_types_filter')
    skip_files_raw = data.get('skip_files')
    dry_run = data.get('dry_run', False)
    verbose = data.get('verbose', False)
    force_upload = data.get('force_upload', False)

    # --- Input Validation ---
    if not all([package_name, version, fhir_server_url]): return jsonify({"status": "error", "message": "Missing required fields"}), 400
    valid_auth_types = ['apiKey', 'bearerToken', 'basic', 'none']  # ADD: 'basic' to valid auth types
    if auth_type not in valid_auth_types: return jsonify({"status": "error", "message": f"Invalid auth_type."}), 400
    if auth_type == 'bearerToken' and not auth_token: return jsonify({"status": "error", "message": "auth_token required for bearerToken."}), 400
    if auth_type == 'basic' and (not username or not password):  # ADD: Validate Basic Auth inputs
        return jsonify({"status": "error", "message": "Username and password required for Basic Authentication."}), 400

    # Parse filters (unchanged)
    resource_types_filter = None
    if resource_types_filter_raw:
        if isinstance(resource_types_filter_raw, list): resource_types_filter = [s for s in resource_types_filter_raw if isinstance(s, str)]
        elif isinstance(resource_types_filter_raw, str): resource_types_filter = [s.strip() for s in resource_types_filter_raw.split(',') if s.strip()]
        else: return jsonify({"status": "error", "message": "Invalid resource_types_filter format."}), 400
    skip_files = None
    if skip_files_raw:
        if isinstance(skip_files_raw, list): skip_files = [s.strip().replace('\\', '/') for s in skip_files_raw if isinstance(s, str) and s.strip()]
        elif isinstance(skip_files_raw, str): skip_files = [s.strip().replace('\\', '/') for s in re.split(r'[,\n]', skip_files_raw) if s.strip()]
        else: return jsonify({"status": "error", "message": "Invalid skip_files format."}), 400

    # --- File Path Setup (unchanged) ---
    packages_dir = current_app.config.get('FHIR_PACKAGES_DIR')
    if not packages_dir: return jsonify({"status": "error", "message": "Server config error: Package dir missing."}), 500
    tgz_filename = services.construct_tgz_filename(package_name, version)
    tgz_path = os.path.join(packages_dir, tgz_filename)
    if not os.path.exists(tgz_path): return jsonify({"status": "error", "message": f"Package not found locally: {package_name}#{version}"}), 404

    # ADD: Handle Basic Authentication
    if auth_type == 'basic':
        credentials = f"{username}:{password}"
        auth_token = f"Basic {base64.b64encode(credentials.encode('utf-8')).decode('utf-8')}"

    # --- Streaming Response ---
    def generate_stream_wrapper():
        yield from services.generate_push_stream(
            package_name=package_name, version=version, fhir_server_url=fhir_server_url,
            include_dependencies=include_dependencies, auth_type=auth_type,
            auth_token=auth_token, resource_types_filter=resource_types_filter,
            skip_files=skip_files, dry_run=dry_run, verbose=verbose,
            force_upload=force_upload, packages_dir=packages_dir
        )
    return Response(generate_stream_wrapper(), mimetype='application/x-ndjson')

# Ensure csrf.exempt(api_push_ig) remains

@app.route('/validate-sample', methods=['GET'])
def validate_sample():
    form = ValidationForm()
    packages = []
    packages_dir = app.config['FHIR_PACKAGES_DIR']
    if os.path.exists(packages_dir):
        for filename in os.listdir(packages_dir):
            if filename.endswith('.tgz'):
                try:
                    with tarfile.open(os.path.join(packages_dir, filename), 'r:gz') as tar:
                        package_json = tar.extractfile('package/package.json')
                        if package_json:
                            pkg_info = json.load(package_json)
                            name = pkg_info.get('name')
                            version = pkg_info.get('version')
                            if name and version:
                                packages.append({'name': name, 'version': version})
                except Exception as e:
                    logger.warning(f"Error reading package {filename}: {e}")
                    continue
    return render_template(
        'validate_sample.html',
        form=form,
        packages=packages,
        validation_report=None,
        site_name='FHIRFLARE IG Toolkit',
        now=datetime.datetime.now(), app_mode=app.config['APP_MODE']
    )

# Exempt specific API views defined directly on 'app'
csrf.exempt(api_import_ig) # Add this line
csrf.exempt(api_push_ig)   # Add this line

# Exempt the entire API blueprint (for routes defined IN services.py, like /api/validate-sample)
csrf.exempt(services_bp) # Keep this line for routes defined in the blueprint

@app.route('/validate-sample', methods=['GET'])
def validate_resource_page():
    """Renders the validation form page."""
    form = ValidationForm()
    # Fetch list of packages to populate the dropdown
    packages = current_app.config.get('MANUAL_PACKAGE_CACHE', [])
    return render_template('validate_sample.html', form=form, packages=packages)

@app.route('/api/validate', methods=['POST'])
@swag_from({
    'tags': ['Validation'],
    'summary': 'Starts a validation process for a FHIR resource or bundle.',
    'description': 'Starts a validation task and immediately returns a task ID for polling. The actual validation is run in a separate thread to prevent timeouts.',
    'security': [{'ApiKeyAuth': []}],
    'consumes': ['application/json'],
    'parameters': [
        {
            'name': 'validation_payload',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'required': ['package_name', 'version', 'fhir_resource'],
                'properties': {
                    'package_name': {'type': 'string', 'example': 'hl7.fhir.us.core'},
                    'version': {'type': 'string', 'example': '6.1.0'},
                    'fhir_resource': {'type': 'string', 'description': 'A JSON string of the FHIR resource or Bundle to validate.'},
                    'fhir_version': {'type': 'string', 'example': '4.0.1'},
                    'do_native': {'type': 'boolean', 'default': False},
                    'hint_about_must_support': {'type': 'boolean', 'default': False},
                    'assume_valid_rest_references': {'type': 'boolean', 'default': False},
                    'no_extensible_binding_warnings': {'type': 'boolean', 'default': False},
                    'show_times': {'type': 'boolean', 'default': False},
                    'allow_example_urls': {'type': 'boolean', 'default': False},
                    'check_ips_codes': {'type': 'boolean', 'default': False},
                    'allow_any_extensions': {'type': 'boolean', 'default': False},
                    'tx_routing': {'type': 'boolean', 'default': False},
                    'snomed_ct_version': {'type': 'string', 'default': ''},
                    'profiles': {'type': 'string', 'description': 'Comma-separated list of profile URLs.'},
                    'extensions': {'type': 'string', 'description': 'Comma-separated list of extension URLs.'},
                    'terminology_server': {'type': 'string', 'description': 'URL of an alternate terminology server.'}
                }
            }
        }
    ],
    'responses': {
        '202': {'description': 'Validation process started successfully, returns a task_id.'},
        '400': {'description': 'Invalid request (e.g., missing fields, invalid JSON).'}
    }
})
def validate_resource():
    """API endpoint to run validation on a FHIR resource."""
    try:
        # Get data from the JSON payload
        data = request.get_json()
        if not data:
            return jsonify({'status': 'error', 'message': 'Invalid JSON: No data received.'}), 400
            
        fhir_resource_text = data.get('fhir_resource')
        fhir_version = data.get('fhir_version')
        package_name = data.get('package_name')
        version = data.get('version')
        profiles = data.get('profiles')
        extensions = data.get('extensions')
        terminology_server = data.get('terminology_server')
        snomed_ct_version = data.get('snomed_ct_version')
        
        # Build the list of IGs from the package name and version
        igs = [f"{package_name}#{version}"] if package_name and version else []
        
        flags = {
            'doNative': data.get('do_native'),
            'hintAboutMustSupport': data.get('hint_about_must_support'),
            'assumeValidRestReferences': data.get('assume_valid_rest_references'),
            'noExtensibleBindingWarnings': data.get('no_extensible_binding_warnings'),
            'showTimes': data.get('show_times'),
            'allowExampleUrls': data.get('allow_example_urls'),
            'checkIpsCodes': data.get('check_ips_codes'),
            'allowAnyExtensions': data.get('allow_any_extensions'),
            'txRouting': data.get('tx_routing'),
        }

        # Check for empty resource
        if not fhir_resource_text:
            return jsonify({'status': 'error', 'message': 'No FHIR resource provided.'}), 400
        
        try:
            # Generate a unique task ID
            task_id = str(uuid.uuid4())
            
            # Store a pending status in the global cache
            with services.cache_lock:
                services.validation_results_cache[task_id] = {"status": "pending"}
            
            # Start the validation process in a new thread
            app_context = app.app_context()
            thread = threading.Thread(
                target=services.validate_in_thread,
                args=(app_context, task_id, fhir_resource_text, fhir_version, igs, profiles, extensions, terminology_server, flags, snomed_ct_version),
                daemon=True
            )
            thread.start()
            
            logger.info(f"Validation task {task_id} started in background thread.")
            
            return jsonify({"status": "accepted", "message": "Validation started", "task_id": task_id}), 202

        except Exception as e:
            logger.error(f"Error starting validation thread: {e}", exc_info=True)
            return jsonify({'status': 'error', 'message': f'An internal server error occurred while starting the validation: {e}'}), 500

    except Exception as e:
        logger.error(f"Validation API error: {e}")
        return jsonify({'status': 'error', 'message': f'An internal server error occurred: {e}'}), 500

@app.route('/api/check-validation-status/<task_id>', methods=['GET'])
@swag_from({
    'tags': ['Validation'],
    'summary': 'Check the status of a validation task.',
    'description': 'Polls for the result of a validation task by its ID. Returns the full validation report when the task is complete.',
    'security': [{'ApiKeyAuth': []}],
    'parameters': [
        {'name': 'task_id', 'in': 'path', 'type': 'string', 'required': True, 'description': 'The unique ID of the validation task.'}
    ],
    'responses': {
        '200': {'description': 'Returns the full validation report if the task is complete.'},
        '202': {'description': 'The validation task is still in progress.'},
        '404': {'description': 'The specified task ID was not found.'}
    }
})
def check_validation_status(task_id):
    """
    This GET endpoint is polled by the frontend to check the status of a validation task.
    """
    with services.cache_lock:
        task_result = services.validation_results_cache.get(task_id)

    if task_result is None:
        return jsonify({"status": "error", "message": "Task not found."}), 404
        
    if task_result["status"] == "pending":
        return jsonify({"status": "pending", "message": "Validation is still in progress."}), 202

    # Remove the result from the cache after it has been retrieved to prevent a memory leak
    with services.cache_lock:
        del services.validation_results_cache[task_id]

    return jsonify({"status": "complete", "result": task_result["result"]}), 200



def create_db():
    logger.debug(f"Attempting to create database tables for URI: {app.config['SQLALCHEMY_DATABASE_URI']}")
    try:
        db.create_all() # This will create RegistryCacheInfo if it doesn't exist
        # Optionally initialize the timestamp row if it's missing
        with app.app_context():
             if RegistryCacheInfo.query.first() is None:
                  initial_info = RegistryCacheInfo(last_fetch_timestamp=None)
                  db.session.add(initial_info)
                  db.session.commit()
                  logger.info("Initialized RegistryCacheInfo table.")
        logger.info("Database tables created/verified successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize database tables: {e}", exc_info=True)
        #db.session.rollback() # Rollback in case of error during init
        raise

with app.app_context():
    create_db()


class FhirRequestForm(FlaskForm):
    submit = SubmitField('Send Request')

@app.route('/fhir-ui')
def fhir_ui():
    form = FhirRequestForm()
    return render_template('fhir_ui.html', form=form, site_name='FHIRFLARE IG Toolkit', now=datetime.datetime.now(), app_mode=app.config['APP_MODE'])

@app.route('/fhir-ui-operations')
def fhir_ui_operations():
    form = FhirRequestForm()
    return render_template('fhir_ui_operations.html', form=form, site_name='FHIRFLARE IG Toolkit', now=datetime.datetime.now(), app_mode=app.config['APP_MODE'])

# --- CORRECTED PROXY FUNCTION DEFINITION (Simplified Decorator) ---

# Use a single route to capture everything after /fhir/
# The 'path' converter handles slashes. 'subpath' can be empty.
@app.route('/fhir', defaults={'subpath': ''}, methods=['GET', 'POST', 'PUT', 'DELETE'])
@app.route('/fhir/', defaults={'subpath': ''}, methods=['GET', 'POST', 'PUT', 'DELETE'])
@app.route('/fhir/<path:subpath>', methods=['GET', 'POST', 'PUT', 'DELETE'])
def proxy_hapi(subpath):
    """
    Proxies FHIR requests to either the local HAPI server or a custom
    target server specified by the 'X-Target-FHIR-Server' header.
    Handles requests to /fhir/ (base, subpath='') and /fhir/<subpath>.
    The route '/fhir' (no trailing slash) is handled separately for the UI.
    """
    # Clean subpath just in case prefixes were somehow included
    clean_subpath = subpath.replace('r4/', '', 1).replace('fhir/', '', 1).strip('/')
    logger.debug(f"Proxy received request for path: '/fhir/{subpath}', cleaned subpath: '{clean_subpath}'")

    # Determine the target FHIR server base URL
    # NEW: Check for proxy-target query parameter first
    target_server_query = request.args.get('proxy-target')
    target_server_header = request.headers.get('X-Target-FHIR-Server')
    final_base_url = None
    is_custom_target = False

    if target_server_query:
        try:
            parsed_url = urlparse(target_server_query)
            if not parsed_url.scheme or not parsed_url.netloc:
                raise ValueError("Invalid URL format in proxy-target query parameter")
            final_base_url = target_server_query.rstrip('/')
            is_custom_target = True
            logger.info(f"Proxy target identified from query parameter: {final_base_url}")
        except ValueError as e:
            logger.warning(f"Invalid URL in proxy-target query parameter: '{target_server_query}'. Falling back. Error: {e}")
            final_base_url = current_app.config['HAPI_FHIR_URL'].rstrip('/')
            logger.debug(f"Falling back to default local HAPI due to invalid query: {final_base_url}")
    elif target_server_header:
        try:
            parsed_url = urlparse(target_server_header)
            if not parsed_url.scheme or not parsed_url.netloc:
                raise ValueError("Invalid URL format in X-Target-FHIR-Server header")
            final_base_url = target_server_header.rstrip('/')
            is_custom_target = True
            logger.info(f"Proxy target identified from header: {final_base_url}")
        except ValueError as e:
            logger.warning(f"Invalid URL in X-Target-FHIR-Server header: '{target_server_header}'. Falling back. Error: {e}")
            final_base_url = current_app.config['HAPI_FHIR_URL'].rstrip('/')
            logger.debug(f"Falling back to default local HAPI due to invalid header: {final_base_url}")
    else:
        final_base_url = current_app.config['HAPI_FHIR_URL'].rstrip('/')
        logger.debug(f"No target info found, proxying to default local HAPI: {final_base_url}")

    # Construct the final URL for the target server request
    # Append the cleaned subpath only if it's not empty
    final_url = f"{final_base_url}/{clean_subpath}" if clean_subpath else final_base_url

    # Prepare headers to forward
    headers_to_forward = {
        k: v for k, v in request.headers.items()
        if k.lower() not in [
            'host', 'x-target-fhir-server', 'content-length', 'connection',
            'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te',
            'trailers', 'transfer-encoding', 'upgrade'
            ]
    }
    if 'Content-Type' in request.headers:
        headers_to_forward['Content-Type'] = request.headers['Content-Type']
    if 'Accept' in request.headers:
         headers_to_forward['Accept'] = request.headers['Accept']
    elif 'Accept' not in headers_to_forward:
         headers_to_forward['Accept'] = 'application/fhir+json, application/fhir+xml;q=0.9, */*;q=0.8'

    logger.info(f"Proxying request: {request.method} {final_url}")
    request_data = request.get_data()

    try:
        # Make the request
        response = requests.request(
            method=request.method,
            url=final_url,
            headers=headers_to_forward,
            data=request_data,
            cookies=request.cookies,
            allow_redirects=False,
            timeout=60
        )
        logger.info(f"Target server '{final_base_url}' responded with status: {response.status_code}")
        response.raise_for_status()

        # Filter hop-by-hop headers
        response_headers = { k: v for k, v in response.headers.items() if k.lower() not in ('transfer-encoding', 'connection', 'content-encoding', 'content-length', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailers', 'upgrade', 'server', 'date', 'x-powered-by', 'via', 'x-forwarded-for', 'x-forwarded-proto', 'x-request-id') }
        response_content = response.content
        response_headers['Content-Length'] = str(len(response_content))

        # Create Flask response
        resp = make_response(response_content)
        resp.status_code = response.status_code
        for key, value in response_headers.items(): resp.headers[key] = value
        if 'Content-Type' in response.headers: resp.headers['Content-Type'] = response.headers['Content-Type']
        return resp

    # --- Exception Handling (same as previous version) ---
    except requests.exceptions.Timeout:
         error_msg = f"Request to the target FHIR server timed out: {final_url}"
         logger.error(f"Proxy timeout error: {error_msg}")
         return jsonify({'resourceType': 'OperationOutcome', 'issue': [{'severity': 'error', 'code': 'timeout', 'diagnostics': error_msg}]}), 504
    except requests.exceptions.ConnectionError as e:
         target_name = 'custom server' if is_custom_target else 'local HAPI'
         error_message = f"Could not connect to the target FHIR server ({target_name} at {final_base_url}). Please check the URL and server status."
         logger.error(f"Proxy connection error: {error_message} - {str(e)}")
         return jsonify({'resourceType': 'OperationOutcome', 'issue': [{'severity': 'error', 'code': 'exception', 'diagnostics': error_message, 'details': {'text': str(e)}}]}), 503
    except requests.exceptions.HTTPError as e:
         logger.warning(f"Proxy received HTTP error from target {final_url}: {e.response.status_code}")
         try:
             error_response_headers = { k: v for k, v in e.response.headers.items() if k.lower() not in ('transfer-encoding', 'connection', 'content-encoding','content-length', 'keep-alive', 'proxy-authenticate','proxy-authorization', 'te', 'trailers', 'upgrade','server', 'date', 'x-powered-by', 'via', 'x-forwarded-for','x-forwarded-proto', 'x-request-id') }
             error_content = e.response.content
             error_response_headers['Content-Length'] = str(len(error_content))
             error_resp = make_response(error_content)
             error_resp.status_code = e.response.status_code
             for key, value in error_response_headers.items(): error_resp.headers[key] = value
             if 'Content-Type' in e.response.headers: error_resp.headers['Content-Type'] = e.response.headers['Content-Type']
             return error_resp
         except Exception as inner_e:
              logger.error(f"Failed to process target server's error response: {inner_e}")
              diag_text = f'Target server returned status {e.response.status_code}, but failed to forward its error details.'
              return jsonify({'resourceType': 'OperationOutcome', 'issue': [{'severity': 'error', 'code': 'exception', 'diagnostics': diag_text, 'details': {'text': str(e)}}]}), e.response.status_code or 502
    except requests.exceptions.RequestException as e:
        logger.error(f"Proxy request error for {final_url}: {str(e)}")
        return jsonify({'resourceType': 'OperationOutcome', 'issue': [{'severity': 'error', 'code': 'exception', 'diagnostics': 'Error communicating with the target FHIR server.', 'details': {'text': str(e)}}]}), 502
    except Exception as e:
         logger.error(f"Unexpected proxy error for {final_url}: {str(e)}", exc_info=True)
         return jsonify({'resourceType': 'OperationOutcome', 'issue': [{'severity': 'error', 'code': 'exception', 'diagnostics': 'An unexpected error occurred within the FHIR proxy.', 'details': {'text': str(e)}}]}), 500


@app.route('/api/stream-retrieve', methods=['GET'])
def stream_retrieve_bundles():
    """
    Handles streaming FHIR bundle retrieval. This endpoint directly calls the service function,
    bypassing the proxy to avoid conflicts. It receives the target URL,
    resources, and other parameters from the front-end via URL query parameters.
    """
    def generate_stream():
        with app.app_context():
            # Extract parameters from query string
            fhir_server_url = request.args.get('fhir_server_url')
            resources = request.args.getlist('resources')
            validate_references = request.args.get('validate_references', 'false').lower() == 'true'
            fetch_reference_bundles = request.args.get('fetch_reference_bundles', 'false').lower() == 'true'
            
            # Extract authentication headers
            auth_token = request.headers.get('Authorization')
            auth_type = 'bearer' if auth_token and auth_token.lower().startswith('bearer') else 'basic' if auth_token and auth_token.lower().startswith('basic') else 'none'

            temp_dir = tempfile.gettempdir()
            zip_filename = f"retrieved_bundles_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}.zip"
            output_zip = os.path.join(temp_dir, zip_filename)

            try:
                yield from services.retrieve_bundles(
                    fhir_server_url=fhir_server_url,
                    resources=resources,
                    output_zip=output_zip,
                    validate_references=validate_references,
                    fetch_reference_bundles=fetch_reference_bundles,
                    auth_type=auth_type,
                    auth_token=auth_token
                )
            except Exception as e:
                logger.error(f"Error in retrieve_bundles: {e}", exc_info=True)
                yield json.dumps({"type": "error", "message": f"Unexpected error: {str(e)}"}) + "\n"

    response = Response(generate_stream(), mimetype='application/x-ndjson')
    response.headers['X-Zip-Path'] = os.path.join('/tmp', zip_filename)
    return response


@app.route('/api/load-ig-to-hapi', methods=['POST'])
@swag_from({
    'tags': ['HAPI Integration'],
    'summary': 'Load an IG into the local HAPI FHIR server.',
    'description': 'Extracts all resources from a specified IG package and PUTs them to the configured HAPI FHIR server.',
    'security': [{'ApiKeyAuth': []}],
    'consumes': ['application/json'],
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'required': ['package_name', 'version'],
                'properties': {
                    'package_name': {'type': 'string', 'example': 'hl7.fhir.us.core'},
                    'version': {'type': 'string', 'example': '6.1.0'}
                }
            }
        }
    ],
    'responses': {
        '200': {'description': 'Package loaded to HAPI successfully.'},
        '400': {'description': 'Invalid request (e.g., missing package_name/version).'},
        '404': {'description': 'Package not found locally.'},
        '500': {'description': 'Error loading IG to HAPI (e.g., HAPI server connection issue, resource upload failure).'}
    }
})
def load_ig_to_hapi():
    data = request.get_json()
    package_name = data.get('package_name')
    version = data.get('version')
    tgz_path = os.path.join(current_app.config['FHIR_PACKAGES_DIR'], construct_tgz_filename(package_name, version))
    if not os.path.exists(tgz_path):
        return jsonify({"error": "Package not found"}), 404
    try:
        with tarfile.open(tgz_path, "r:gz") as tar:
            for member in tar.getmembers():
                if member.name.endswith('.json') and member.name not in ['package/package.json', 'package/.index.json']:
                    resource = json.load(tar.extractfile(member))
                    resource_type = resource.get('resourceType')
                    resource_id = resource.get('id')
                    if resource_type and resource_id:
                        response = requests.put(
                            f"{current_app.config['HAPI_FHIR_URL'].rstrip('/')}/{resource_type}/{resource_id}",
                            json=resource,
                            headers={'Content-Type': 'application/fhir+json'}
                        )
                        response.raise_for_status()
        return jsonify({"status": "success", "message": f"Loaded {package_name}#{version} to HAPI"})
    except Exception as e:
        logger.error(f"Failed to load IG to HAPI: {e}")
        return jsonify({"error": str(e)}), 500


# Assuming 'app' and 'logger' are defined, and other necessary imports are present above

@app.route('/fsh-converter', methods=['GET', 'POST'])
def fsh_converter():
    form = FSHConverterForm()
    fsh_output = None
    error = None
    comparison_report = None

    # --- Populate package choices ---
    packages = []
    packages_dir = app.config.get('FHIR_PACKAGES_DIR', '/app/instance/fhir_packages') # Use .get with default
    logger.debug(f"Scanning packages directory: {packages_dir}")
    if os.path.exists(packages_dir):
        tgz_files = [f for f in os.listdir(packages_dir) if f.endswith('.tgz')]
        logger.debug(f"Found {len(tgz_files)} .tgz files: {tgz_files}")
        for filename in tgz_files:
            package_file_path = os.path.join(packages_dir, filename)
            try:
                # Check if it's a valid tar.gz file before opening
                if not tarfile.is_tarfile(package_file_path):
                     logger.warning(f"Skipping non-tarfile or corrupted file: {filename}")
                     continue

                with tarfile.open(package_file_path, 'r:gz') as tar:
                    # Find package.json case-insensitively and handle potential path variations
                    package_json_path = next((m for m in tar.getmembers() if m.name.lower().endswith('package.json') and m.isfile() and ('/' not in m.name.replace('package/','', 1).lower())), None) # Handle package/ prefix better

                    if package_json_path:
                        package_json_stream = tar.extractfile(package_json_path)
                        if package_json_stream:
                            try:
                                pkg_info = json.load(package_json_stream)
                                name = pkg_info.get('name')
                                version = pkg_info.get('version')
                                if name and version:
                                    package_id = f"{name}#{version}"
                                    packages.append((package_id, package_id))
                                    logger.debug(f"Added package: {package_id}")
                                else:
                                    logger.warning(f"Missing name or version in {filename}/package.json: name={name}, version={version}")
                            except json.JSONDecodeError as json_e:
                                logger.warning(f"Error decoding package.json from {filename}: {json_e}")
                            except Exception as read_e:
                                logger.warning(f"Error reading stream from package.json in {filename}: {read_e}")
                            finally:
                                package_json_stream.close() # Ensure stream is closed
                        else:
                             logger.warning(f"Could not extract package.json stream from {filename} (path: {package_json_path.name})")
                    else:
                        logger.warning(f"No suitable package.json found in {filename}")
            except tarfile.ReadError as tar_e:
                 logger.warning(f"Tarfile read error for {filename}: {tar_e}")
            except Exception as e:
                logger.warning(f"Error processing package {filename}: {str(e)}")
                continue # Continue to next file
    else:
        logger.warning(f"Packages directory does not exist: {packages_dir}")

    unique_packages = sorted(list(set(packages)), key=lambda x: x[0])
    form.package.choices = [('', 'None')] + unique_packages
    logger.debug(f"Set package choices: {form.package.choices}")
    # --- End package choices ---

    if form.validate_on_submit(): # This block handles POST requests
        input_mode = form.input_mode.data
        # Use request.files.get to safely access file data
        fhir_file_storage = request.files.get(form.fhir_file.name)
        fhir_file = fhir_file_storage if fhir_file_storage and fhir_file_storage.filename != '' else None

        fhir_text = form.fhir_text.data

        alias_file_storage = request.files.get(form.alias_file.name)
        alias_file = alias_file_storage if alias_file_storage and alias_file_storage.filename != '' else None

        output_style = form.output_style.data
        log_level = form.log_level.data
        fhir_version = form.fhir_version.data if form.fhir_version.data != 'auto' else None
        fishing_trip = form.fishing_trip.data
        dependencies = [dep.strip() for dep in form.dependencies.data.splitlines() if dep.strip()] if form.dependencies.data else None # Use splitlines()
        indent_rules = form.indent_rules.data
        meta_profile = form.meta_profile.data
        no_alias = form.no_alias.data

        logger.debug(f"Processing input: mode={input_mode}, has_file={bool(fhir_file)}, has_text={bool(fhir_text)}, has_alias={bool(alias_file)}")
        # Pass the FileStorage object directly if needed by process_fhir_input
        input_file, temp_dir, alias_path, input_error = services.process_fhir_input(input_mode, fhir_file, fhir_text, alias_file)

        if input_error:
            error = input_error
            flash(error, 'error')
            logger.error(f"Input processing error: {error}")
            if temp_dir and os.path.exists(temp_dir):
                 try: shutil.rmtree(temp_dir, ignore_errors=True)
                 except Exception as cleanup_e: logger.warning(f"Error removing temp dir after input error {temp_dir}: {cleanup_e}")
        else:
            # Proceed only if input processing was successful
            output_dir = os.path.join(app.config.get('UPLOAD_FOLDER', '/app/static/uploads'), 'fsh_output') # Use .get
            os.makedirs(output_dir, exist_ok=True)
            logger.debug(f"Running GoFSH with input: {input_file}, output_dir: {output_dir}")
            # Pass form data directly to run_gofsh
            fsh_output, comparison_report, gofsh_error = services.run_gofsh(
                input_file, output_dir, output_style, log_level, fhir_version,
                fishing_trip, dependencies, indent_rules, meta_profile, alias_path, no_alias
            )
            # Clean up temp dir after GoFSH run
            if temp_dir and os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    logger.debug(f"Successfully removed temp directory: {temp_dir}")
                except Exception as cleanup_e:
                     logger.warning(f"Error removing temp directory {temp_dir}: {cleanup_e}")

            if gofsh_error:
                error = gofsh_error
                flash(error, 'error')
                logger.error(f"GoFSH error: {error}")
            else:
                # Store potentially large output carefully - session might have limits
                session['fsh_output'] = fsh_output
                flash('Conversion successful!', 'success')
                logger.info("FSH conversion successful")

        # Return response for POST (AJAX or full page)
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            logger.debug("Returning partial HTML for AJAX POST request.")
            return render_template('_fsh_output.html', form=form, error=error, fsh_output=fsh_output, comparison_report=comparison_report)
        else:
             # For standard POST, re-render the full page with results/errors
             logger.debug("Handling standard POST request, rendering full page.")
             return render_template('fsh_converter.html', form=form, error=error, fsh_output=fsh_output, comparison_report=comparison_report, site_name='FHIRFLARE IG Toolkit', now=datetime.datetime.now())

    # --- Handle GET request (Initial Page Load or Failed POST Validation) ---
    else:
        if request.method == 'POST': # POST but validation failed
             logger.warning("POST request failed form validation.")
             # Render the full page, WTForms errors will be displayed by render_field
             return render_template('fsh_converter.html', form=form, error="Form validation failed. Please check fields.", fsh_output=None, comparison_report=None, site_name='FHIRFLARE IG Toolkit', now=datetime.datetime.now())
        else:
             # This is the initial GET request
             logger.debug("Handling GET request for FSH converter page.")
             # **** FIX APPLIED HERE ****
             # Make the response object to add headers
             response = make_response(render_template(
                 'fsh_converter.html',
                 form=form, # Pass the empty form
                 error=None,
                 fsh_output=None,
                 comparison_report=None,
                 site_name='FHIRFLARE IG Toolkit',
                 now=datetime.datetime.now()
             ))
             # Add headers to prevent caching
             response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
             response.headers['Pragma'] = 'no-cache'
             response.headers['Expires'] = '0'
             return response
             # **** END OF FIX ****

@app.route('/download-fsh')
def download_fsh():
    fsh_output = session.get('fsh_output')
    if not fsh_output:
        flash('No FSH output available for download.', 'error')
        return redirect(url_for('fsh_converter'))
    
    temp_file = os.path.join(app.config['UPLOAD_FOLDER'], 'output.fsh')
    with open(temp_file, 'w', encoding='utf-8') as f:
        f.write(fsh_output)
    
    return send_file(temp_file, as_attachment=True, download_name='output.fsh')

@app.route('/upload-test-data', methods=['GET'])
def upload_test_data():
    """Renders the page for uploading test data."""
    form = TestDataUploadForm()
    try:
        processed_igs = ProcessedIg.query.order_by(ProcessedIg.package_name, ProcessedIg.version).all()
        form.validation_package_id.choices = [('', '-- Select Package for Validation --')] + [
            (f"{ig.package_name}#{ig.version}", f"{ig.package_name}#{ig.version}") for ig in processed_igs ]
    except Exception as e:
        logger.error(f"Error fetching processed IGs: {e}")
        flash("Could not load processed packages for validation.", "warning")
        form.validation_package_id.choices = [('', '-- Error Loading Packages --')]
    api_key = current_app.config.get('API_KEY', '')
    return render_template('upload_test_data.html', title="Upload Test Data", form=form, api_key=api_key)


# --- Updated /api/upload-test-data Endpoint ---
@app.route('/api/upload-test-data', methods=['POST'])
@csrf.exempt
@swag_from({
    'tags': ['Test Data Management'],
    'summary': 'Upload and process FHIR test data.',
    'description': 'Handles multipart/form-data uploads of FHIR resources (JSON, XML, or ZIP containing these) for processing and uploading to a target FHIR server. Returns an NDJSON stream of progress.',
    'security': [{'ApiKeyAuth': []}],
    'consumes': ['multipart/form-data'],
    'produces': ['application/x-ndjson'],
    'parameters': [
        {'name': 'fhir_server_url', 'in': 'formData', 'type': 'string', 'required': True, 'format': 'url', 'description': 'Target FHIR server URL.'},
        {'name': 'auth_type', 'in': 'formData', 'type': 'string', 'enum': ['none', 'bearerToken', 'basic'], 'default': 'none'},
        {'name': 'auth_token', 'in': 'formData', 'type': 'string', 'description': 'Bearer token if auth_type is bearerToken.'},
        {'name': 'username', 'in': 'formData', 'type': 'string', 'description': 'Username if auth_type is basic.'},
        {'name': 'password', 'in': 'formData', 'type': 'string', 'format': 'password', 'description': 'Password if auth_type is basic.'},
        {'name': 'test_data_files', 'in': 'formData', 'type': 'file', 'required': True, 'description': 'One or more FHIR resource files (JSON, XML) or ZIP archives containing them.'},
        {'name': 'validate_before_upload', 'in': 'formData', 'type': 'boolean', 'default': False},
        {'name': 'validation_package_id', 'in': 'formData', 'type': 'string', 'description': 'Package ID (name#version) for validation, if validate_before_upload is true.'},
        {'name': 'upload_mode', 'in': 'formData', 'type': 'string', 'enum': ['individual', 'transaction'], 'default': 'individual'},
        {'name': 'use_conditional_uploads', 'in': 'formData', 'type': 'boolean', 'default': True, 'description': 'For individual mode, use conditional logic (GET then PUT/POST).'},
        {'name': 'error_handling', 'in': 'formData', 'type': 'string', 'enum': ['stop', 'continue'], 'default': 'stop'}
    ],
    'responses': {
        '200': {'description': 'NDJSON stream of upload progress and results.'},
        '400': {'description': 'Invalid request parameters or file types.'},
        '401': {'description': 'Authentication error.'},
        '413': {'description': 'Request entity too large.'},
        '500': {'description': 'Server error during upload processing.'}
    }
})
def api_upload_test_data():
    """API endpoint to handle test data upload and processing, using custom parser."""
    auth_error = check_api_key()
    if auth_error: return auth_error

    temp_dir = None
    try:
        parser = CustomFormDataParser()
        stream = request.stream
        mimetype = request.mimetype
        content_length = request.content_length
        options = request.mimetype_params
        _, form_data, files_data = parser.parse(stream, mimetype, content_length, options)
        logger.debug(f"Form parsed using CustomFormDataParser. Form fields: {len(form_data)}, Files: {len(files_data)}")

        # --- Extract Form Data ---
        fhir_server_url = form_data.get('fhir_server_url')
        auth_type = form_data.get('auth_type', 'none')
        auth_token = form_data.get('auth_token')
        username = form_data.get('username')
        password = form_data.get('password')
        upload_mode = form_data.get('upload_mode', 'individual')
        error_handling = form_data.get('error_handling', 'stop')
        validate_before_upload_str = form_data.get('validate_before_upload', 'false')
        validate_before_upload = validate_before_upload_str.lower() == 'true'
        validation_package_id = form_data.get('validation_package_id') if validate_before_upload else None
        use_conditional_uploads_str = form_data.get('use_conditional_uploads', 'false')
        use_conditional_uploads = use_conditional_uploads_str.lower() == 'true'

        logger.debug(f"API Upload Request Params: validate={validate_before_upload}, pkg_id={validation_package_id}, conditional={use_conditional_uploads}")

        # --- Basic Validation ---
        if not fhir_server_url or not fhir_server_url.startswith(('http://', 'https://')):
            return jsonify({"status": "error", "message": "Invalid Target FHIR Server URL."}), 400
        if auth_type not in ['none', 'bearerToken', 'basic']:
            return jsonify({"status": "error", "message": "Invalid Authentication Type."}), 400
        if auth_type == 'bearerToken' and not auth_token:
            return jsonify({"status": "error", "message": "auth_token required for bearerToken."}), 400
        if auth_type == 'basic' and (not username or not password):
            return jsonify({"status": "error", "message": "Username and Password required for Basic Authentication."}), 400
        if upload_mode not in ['individual', 'transaction']:
            return jsonify({"status": "error", "message": "Invalid Upload Mode."}), 400
        if error_handling not in ['stop', 'continue']:
            return jsonify({"status": "error", "message": "Invalid Error Handling mode."}), 400
        if validate_before_upload and not validation_package_id:
            return jsonify({"status": "error", "message": "Validation Package ID required."}), 400

        # --- Handle File Uploads ---
        uploaded_files = files_data.getlist('test_data_files')
        if not uploaded_files or all(f.filename == '' for f in uploaded_files):
            return jsonify({"status": "error", "message": "No files selected."}), 400

        temp_dir = tempfile.mkdtemp(prefix='fhirflare_upload_')
        saved_file_paths = []
        allowed_extensions = {'.json', '.xml', '.zip'}
        try:
            for file_storage in uploaded_files:
                if file_storage and file_storage.filename:
                    filename = secure_filename(file_storage.filename)
                    file_ext = os.path.splitext(filename)[1].lower()
                    if file_ext not in allowed_extensions:
                        raise ValueError(f"Invalid file type: '{filename}'. Only JSON, XML, ZIP allowed.")
                    save_path = os.path.join(temp_dir, filename)
                    file_storage.save(save_path)
                    saved_file_paths.append(save_path)
            if not saved_file_paths:
                raise ValueError("No valid files saved.")
            logger.debug(f"Saved {len(saved_file_paths)} files to {temp_dir}")
        except ValueError as ve:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
            logger.warning(f"Upload rejected: {ve}")
            return jsonify({"status": "error", "message": str(ve)}), 400
        except Exception as file_err:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
            logger.error(f"Error saving uploaded files: {file_err}", exc_info=True)
            return jsonify({"status": "error", "message": "Error saving uploaded files."}), 500

        # --- Prepare Server Info and Options ---
        server_info = {'url': fhir_server_url, 'auth_type': auth_type}
        if auth_type == 'bearer':
            server_info['auth_token'] = auth_token
        elif auth_type == 'basic':
            credentials = f"{username}:{password}"
            encoded_credentials = base64.b64encode(credentials.encode('utf-8')).decode('utf-8')
            server_info['auth_token'] = f"Basic {encoded_credentials}"
        options = {
            'upload_mode': upload_mode,
            'error_handling': error_handling,
            'validate_before_upload': validate_before_upload,
            'validation_package_id': validation_package_id,
            'use_conditional_uploads': use_conditional_uploads
        }

        # --- Call Service Function (Streaming Response) ---
        def generate_stream_wrapper():
            try:
                with app.app_context():
                    yield from services.process_and_upload_test_data(server_info, options, temp_dir)
            finally:
                try:
                    logger.debug(f"Cleaning up temp dir: {temp_dir}")
                    shutil.rmtree(temp_dir)
                except Exception as cleanup_e:
                    logger.error(f"Error cleaning up temp dir {temp_dir}: {cleanup_e}")

        return Response(generate_stream_wrapper(), mimetype='application/x-ndjson')

    except RequestEntityTooLarge as e:
        logger.error(f"RequestEntityTooLarge error in /api/upload-test-data despite custom parser: {e}", exc_info=True)
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except Exception as cleanup_e:
                logger.error(f"Error cleaning up temp dir during exception: {cleanup_e}")
        return jsonify({"status": "error", "message": f"Upload failed: Request entity too large. Try increasing parser limit or reducing files/size. ({str(e)})"}), 413

    except Exception as e:
        logger.error(f"Error in /api/upload-test-data: {e}", exc_info=True)
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except Exception as cleanup_e:
                logger.error(f"Error cleaning up temp dir during exception: {cleanup_e}")
        return jsonify({"status": "error", "message": f"Unexpected server error: {str(e)}"}), 500

@app.route('/retrieve-split-data', methods=['GET', 'POST'])
def retrieve_split_data():
    form = RetrieveSplitDataForm()
    if form.validate_on_submit():
        if form.submit_retrieve.data:
            session['retrieve_params'] = {
                'fhir_server_url': form.fhir_server_url.data,
                'validate_references': form.validate_references.data,
                'resources': request.form.getlist('resources')
            }
            if form.bundle_zip.data:
                # Save uploaded ZIP to temporary file
                temp_dir = tempfile.gettempdir()
                zip_path = os.path.join(temp_dir, 'uploaded_bundles.zip')
                form.bundle_zip.data.save(zip_path)
                session['retrieve_params']['bundle_zip_path'] = zip_path
            flash('Bundle retrieval initiated. Download will start after processing.', 'info')
        elif form.submit_split.data:
            # Save uploaded ZIP to temporary file
            temp_dir = tempfile.gettempdir()
            zip_path = os.path.join(temp_dir, 'split_bundles.zip')
            form.split_bundle_zip.data.save(zip_path)
            session['split_params'] = {'split_bundle_zip_path': zip_path}
            flash('Bundle splitting initiated. Download will start after processing.', 'info')
    return render_template('retrieve_split_data.html', form=form, site_name='FHIRFLARE IG Toolkit',
                          now=datetime.datetime.now(), app_mode=app.config['APP_MODE'],
                          api_key=app.config['API_KEY'])

@app.route('/api/retrieve-bundles', methods=['POST'])
@csrf.exempt
@swag_from({
    'tags': ['Test Data Management'],
    'summary': 'Retrieve FHIR resource bundles from a server.',
    'description': 'Fetches bundles for specified resource types from a FHIR server. Optionally fetches referenced resources. Returns an NDJSON stream and prepares a ZIP file for download.',
    'security': [{'ApiKeyAuth': []}],
    'consumes': ['application/x-www-form-urlencoded'], # Or multipart/form-data if files are involved
    'produces': ['application/x-ndjson'],
    'parameters': [
        {'name': 'fhir_server_url', 'in': 'formData', 'type': 'string', 'required': False, 'format': 'url', 'description': 'Target FHIR server URL. Defaults to local proxy (/fhir).'},
        {'name': 'resources', 'in': 'formData', 'type': 'array', 'items': {'type': 'string'}, 'collectionFormat': 'multi', 'required': True, 'description': 'List of resource types to retrieve (e.g., Patient, Observation).'},
        {'name': 'validate_references', 'in': 'formData', 'type': 'boolean', 'default': False, 'description': 'Fetch resources referenced by the initial bundles.'},
        {'name': 'fetch_reference_bundles', 'in': 'formData', 'type': 'boolean', 'default': False, 'description': 'If fetching references, get full bundles for referenced types instead of individual resources.'},
        {'name': 'auth_type', 'in': 'formData', 'type': 'string', 'enum': ['none', 'bearer', 'basic'], 'default': 'none'},
        {'name': 'bearer_token', 'in': 'formData', 'type': 'string', 'description': 'Bearer token if auth_type is bearer.'},
        {'name': 'username', 'in': 'formData', 'type': 'string', 'description': 'Username if auth_type is basic.'},
        {'name': 'password', 'in': 'formData', 'type': 'string', 'format': 'password', 'description': 'Password if auth_type is basic.'}
    ],
    'responses': {
        '200': {
            'description': 'NDJSON stream of retrieval progress. X-Zip-Path header indicates path to the created ZIP file.',
            'headers': {
                'X-Zip-Path': {'type': 'string', 'description': 'Server path to the generated ZIP file.'}
            }
        },
        '400': {'description': 'Invalid request parameters.'},
        '401': {'description': 'Authentication error.'},
        '500': {'description': 'Server error during retrieval.'}
    }
})
def api_retrieve_bundles():
    auth_error = check_api_key()
    if auth_error:
        return auth_error

    # Use request.form for standard form data
    params = request.form.to_dict()
    resources = request.form.getlist('resources')
    validate_references = params.get('validate_references', 'false').lower() == 'true'
    fetch_reference_bundles = params.get('fetch_reference_bundles', 'false').lower() == 'true'
    auth_type = params.get('auth_type', 'none')
    bearer_token = params.get('bearer_token')
    username = params.get('username')
    password = params.get('password')

    # Get FHIR server URL, default to '/fhir' (local proxy)
    fhir_server_url = params.get('fhir_server_url', '/fhir').strip()
    if not fhir_server_url:
        fhir_server_url = '/fhir'

    # Validation
    if not resources:
        return jsonify({"status": "error", "message": "No resources selected."}), 400
    valid_auth_types = ['none', 'bearer', 'basic']
    if auth_type not in valid_auth_types:
        return jsonify({"status": "error", "message": f"Invalid auth_type. Must be one of {valid_auth_types}."}), 400
    if auth_type == 'bearer' and not bearer_token:
        return jsonify({"status": "error", "message": "Bearer token required for bearer authentication."}), 400
    if auth_type == 'basic' and (not username or not password):
        return jsonify({"status": "error", "message": "Username and password required for basic authentication."}), 400

    # Handle authentication
    auth_token = None
    if auth_type == 'bearer':
        auth_token = f"Bearer {bearer_token}"
    elif auth_type == 'basic':
        credentials = f"{username}:{password}"
        auth_token = f"Basic {base64.b64encode(credentials.encode('utf-8')).decode('utf-8')}"

    logger.info(f"Retrieve API: Server='{fhir_server_url}', Resources={resources}, ValidateRefs={validate_references}, FetchRefBundles={fetch_reference_bundles}, AuthType={auth_type}")

    # Ensure the temp directory exists
    temp_dir = tempfile.gettempdir()
    zip_filename = f"retrieved_bundles_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}.zip"
    output_zip = os.path.join(temp_dir, zip_filename)

    def generate():
        # Push the application context manually for the generator's lifetime
        with app.app_context():
            try:
                yield from services.retrieve_bundles(
                    fhir_server_url=fhir_server_url,
                    resources=resources,
                    output_zip=output_zip,
                    validate_references=validate_references,
                    fetch_reference_bundles=fetch_reference_bundles,
                    auth_type=auth_type,
                    auth_token=auth_token
                )
            except Exception as e:
                logger.error(f"Error in retrieve_bundles: {e}", exc_info=True)
                yield json.dumps({"type": "error", "message": f"Unexpected error: {str(e)}"}) + "\n"

    response = Response(generate(), mimetype='application/x-ndjson')
    response.headers['X-Zip-Path'] = os.path.join('/tmp', zip_filename)
    return response

@app.route('/api/split-bundles', methods=['POST'])
@swag_from({
    'tags': ['Test Data Management'],
    'summary': 'Split FHIR bundles from a ZIP into individual resources.',
    'description': 'Takes a ZIP file containing FHIR bundles, extracts individual resources, and creates a new ZIP file with these resources. Returns an NDJSON stream of progress.',
    'security': [{'ApiKeyAuth': []}],
    'consumes': ['multipart/form-data'], # Assuming split_bundle_zip_path comes from a form that might include a file upload in other contexts, or it's a path string. If it's always a path string from a JSON body, change consumes.
    'produces': ['application/x-ndjson'],
    'parameters': [
        # If split_bundle_zip_path is a path sent in form data:
        {'name': 'split_bundle_zip_path', 'in': 'formData', 'type': 'string', 'required': True, 'description': 'Path to the input ZIP file containing bundles (server-side path).'},
        # If it's an uploaded file:
        # {'name': 'split_bundle_zip_file', 'in': 'formData', 'type': 'file', 'required': True, 'description': 'ZIP file containing bundles to split.'}
    ],
    'responses': {
        '200': {
            'description': 'NDJSON stream of splitting progress. X-Zip-Path header indicates path to the output ZIP file.',
            'headers': {
                'X-Zip-Path': {'type': 'string', 'description': 'Server path to the generated ZIP file with split resources.'}
            }
        },
        '400': {'description': 'Invalid request (e.g., missing input ZIP path/file).'},
        '401': {'description': 'Authentication error.'},
        '500': {'description': 'Server error during splitting.'}
    }
})
def api_split_bundles():
    auth_error = check_api_key()
    if auth_error:
        return auth_error
    params = request.form.to_dict()
    input_zip_path = params.get('split_bundle_zip_path')
    if not input_zip_path:
        return jsonify({"status": "error", "message": "Missing input ZIP file."}), 400
    temp_dir = tempfile.gettempdir()
    output_zip = os.path.join(temp_dir, 'split_resources.zip')
    def generate():
        for message in split_bundles(input_zip_path, output_zip):
            yield message
    response = Response(generate(), mimetype='application/x-ndjson')
    response.headers['X-Zip-Path'] = output_zip
    return response

@app.route('/tmp/<filename>', methods=['GET'])
def serve_zip(filename):
    file_path = os.path.join('/tmp', filename)
    if not os.path.exists(file_path):
        logger.error(f"ZIP file not found: {file_path}")
        return jsonify({'error': 'File not found'}), 404
    try:
        return send_file(file_path, as_attachment=True, download_name=filename)
    except Exception as e:
        logger.error(f"Error serving ZIP file {file_path}: {str(e)}")
        return jsonify({'error': 'Error serving file', 'details': str(e)}), 500

@app.route('/clear-session', methods=['POST'])
def clear_session():
    session.pop('retrieve_params', None)
    session.pop('split_params', None)
    return jsonify({"status": "success", "message": "Session cleared"})


@app.route('/api/package/<name>', methods=['GET'])
@swag_from({
    'tags': ['Package Management'],
    'summary': 'Get details for a specific FHIR package.',
    'description': 'Retrieves details for a FHIR IG package by its name. Data is sourced from ProcessedIg, CachedPackage, or fetched live from registries.',
    'parameters': [
        {'name': 'name', 'in': 'path', 'type': 'string', 'required': True, 'description': 'The canonical name of the package (e.g., hl7.fhir.us.core).'}
    ],
    'responses': {
        '200': {
            'description': 'Package details.',
            'schema': {
                'type': 'object',
                'properties': {
                    'name': {'type': 'string'},
                    'latest': {'type': 'string', 'description': 'Latest known version.'},
                    'author': {'type': 'string'},
                    'fhir_version': {'type': 'string'},
                    'version_count': {'type': 'integer'},
                    'url': {'type': 'string', 'format': 'url'}
                }
            }
        },
        '404': {'description': 'Package not found.'}
    }
})
def package_details(name):
    """
    Retrieve details for a specific FHIR Implementation Guide package by name.
    Fetches from ProcessedIg or CachedPackage if not found in the database.
    
    Args:
        name (str): The name of the package (e.g., 'hl7.fhir.us.core').
    
    Returns:
        JSON with package details (name, latest version, author, FHIR version, version count, URL)
        or a 404 error if the package is not found.
    """
    from services import fetch_packages_from_registries, normalize_package_data
    
    # Check ProcessedIg first (processed IGs)
    package = ProcessedIg.query.filter_by(package_name=name).first()
    if package:
        return jsonify({
            'name': package.package_name,
            'latest': package.version,
            'author': package.author,
            'fhir_version': package.fhir_version,
            'version_count': package.version_count,
            'url': package.url
        })
    
    # Check CachedPackage (cached packages)
    package = CachedPackage.query.filter_by(package_name=name).first()
    if package:
        return jsonify({
            'name': package.package_name,
            'latest': package.version,
            'author': package.author,
            'fhir_version': package.fhir_version,
            'version_count': package.version_count,
            'url': package.url
        })
    
    # Fetch from registries if not in database
    logger.info(f"Package {name} not found in database. Fetching from registries.")
    raw_packages = fetch_packages_from_registries(search_term=name)
    normalized_packages = normalize_package_data(raw_packages)
    package = next((pkg for pkg in normalized_packages if pkg['name'].lower() == name.lower()), None)
    
    if not package:
        return jsonify({'error': 'Package not found'}), 404
    
    return jsonify({
        'name': package['name'],
        'latest': package['version'],
        'author': package['author'],
        'fhir_version': package['fhir_version'],
        'version_count': package['version_count'],
        'url': package['url']
    })

@app.route('/search-and-import')
def search_and_import():
    """
    Render the Search and Import page. Uses the database (CachedPackage) to load the package cache if available.
    If not available, fetches from registries and caches the result. Displays latest official version if available,
    otherwise falls back to latest absolute version. Shows fire animation and logs during cache loading.
    """
    logger.debug("--- Entering search_and_import route (DB Cache Logic) ---")
    page = request.args.get('page', 1, type=int)
    per_page = 50

    in_memory_packages = app.config.get('MANUAL_PACKAGE_CACHE')
    in_memory_timestamp = app.config.get('MANUAL_CACHE_TIMESTAMP')
    db_timestamp_info = RegistryCacheInfo.query.first()
    db_timestamp = db_timestamp_info.last_fetch_timestamp if db_timestamp_info else None
    logger.debug(f"DB Timestamp: {db_timestamp}, In-Memory Timestamp: {in_memory_timestamp}")

    normalized_packages = None
    fetch_failed_flag = False
    display_timestamp = None
    is_fetching = False

    # Check if a fetch is in progress (stored in session)
    fetch_in_progress = session.get('fetch_in_progress', False)

    if fetch_in_progress and in_memory_packages is not None:
        # Fetch has completed, clear the session flag and proceed
        session['fetch_in_progress'] = False
        logger.info("Fetch completed, clearing fetch_in_progress flag.")
        normalized_packages = in_memory_packages
        display_timestamp = in_memory_timestamp
        fetch_failed_flag = session.get('fetch_failed', False)
    elif in_memory_packages is not None:
        logger.info(f"Using in-memory cached package list from {in_memory_timestamp}.")
        normalized_packages = in_memory_packages
        display_timestamp = in_memory_timestamp
        fetch_failed_flag = session.get('fetch_failed', False)
    else:
        # Check if there are cached packages in the database
        try:
            cached_packages = CachedPackage.query.all()
            if cached_packages:
                logger.info(f"Loading {len(cached_packages)} packages from CachedPackage table.")
                # Reconstruct the normalized package format from the database entries
                normalized_packages = []
                packages_by_name = {}
                for pkg in cached_packages:
                    # Use getattr to provide defaults for potentially missing fields
                    pkg_data = {
                        'name': pkg.package_name,
                        'version': pkg.version,
                        'latest_absolute_version': getattr(pkg, 'latest_absolute_version', pkg.version),
                        'latest_official_version': getattr(pkg, 'latest_official_version', None),
                        'author': getattr(pkg, 'author', ''),
                        'fhir_version': getattr(pkg, 'fhir_version', ''),
                        'url': getattr(pkg, 'url', ''),
                        'canonical': getattr(pkg, 'canonical', ''),
                        'dependencies': getattr(pkg, 'dependencies', []) or [],
                        'version_count': getattr(pkg, 'version_count', 1),
                        'all_versions': getattr(pkg, 'all_versions', [{'version': pkg.version, 'pubDate': ''}]) or [],
                        'versions_data': [],
                        'registry': getattr(pkg, 'registry', '')
                    }
                    # Group by package name to handle version aggregation
                    if pkg_data['name'] not in packages_by_name:
                        packages_by_name[pkg_data['name']] = pkg_data
                        normalized_packages.append(pkg_data)
                    else:
                        # Update all_versions for the existing package
                        existing_pkg = packages_by_name[pkg_data['name']]
                        if pkg_data['all_versions']:
                            existing_pkg['all_versions'].extend(pkg_data['all_versions'])
                        # Update version_count
                        existing_pkg['version_count'] = len(existing_pkg['all_versions'])

                # Sort all_versions within each package
                for pkg in normalized_packages:
                    pkg['all_versions'].sort(key=lambda x: safe_parse_version(x.get('version', '0.0.0a0')), reverse=True)

                app.config['MANUAL_PACKAGE_CACHE'] = normalized_packages
                app.config['MANUAL_CACHE_TIMESTAMP'] = db_timestamp or datetime.datetime.now(datetime.timezone.utc)
                display_timestamp = app.config['MANUAL_CACHE_TIMESTAMP']
                fetch_failed_flag = session.get('fetch_failed', False)
                logger.info(f"Loaded {len(normalized_packages)} packages into in-memory cache from database.")
            else:
                logger.info("No packages found in CachedPackage table. Fetching from registries...")
                is_fetching = True
        except Exception as db_err:
            logger.error(f"Error loading packages from CachedPackage table: {db_err}", exc_info=True)
            flash("Error loading package cache from database. Fetching from registries...", "warning")
            is_fetching = True

    # If no packages were loaded from the database, fetch from registries
    if normalized_packages is None:
        logger.info("Fetching package list from registries...")
        try:
            # Clear the log queue to capture fetch logs
            while not log_queue.empty():
                log_queue.get()

            # Set session flag to indicate fetch is in progress
            session['fetch_in_progress'] = True

            raw_packages = fetch_packages_from_registries(search_term='')
            logger.debug(f"fetch_packages_from_registries returned {len(raw_packages)} raw packages.")
            if not raw_packages:
                logger.warning("No packages returned from registries during refresh.")
                normalized_packages = []
                fetch_failed_flag = True
                session['fetch_failed'] = True
                app.config['MANUAL_PACKAGE_CACHE'] = []
                app.config['MANUAL_CACHE_TIMESTAMP'] = None
                display_timestamp = db_timestamp
            else:
                logger.debug("Normalizing fetched packages...")
                normalized_packages = normalize_package_data(raw_packages)
                logger.debug(f"Normalization resulted in {len(normalized_packages)} unique packages.")
                now_ts = datetime.datetime.now(datetime.timezone.utc)
                app.config['MANUAL_PACKAGE_CACHE'] = normalized_packages
                app.config['MANUAL_CACHE_TIMESTAMP'] = now_ts
                app_state['fetch_failed'] = False
                logger.info(f"Stored {len(normalized_packages)} packages in manual cache (memory).")

                # Save to CachedPackage table
                try:
                    cache_packages(normalized_packages, db, CachedPackage)
                except Exception as cache_err:
                    logger.error(f"Failed to cache packages in database: {cache_err}", exc_info=True)
                    flash("Error saving package cache to database.", "warning")

                if db_timestamp_info:
                    db_timestamp_info.last_fetch_timestamp = now_ts
                else:
                    db_timestamp_info = RegistryCacheInfo(last_fetch_timestamp=now_ts)
                    db.session.add(db_timestamp_info)
                try:
                    db.session.commit()
                    logger.info(f"Updated DB timestamp to {now_ts}")
                except Exception as db_err:
                    db.session.rollback()
                    logger.error(f"Failed to update DB timestamp: {db_err}", exc_info=True)
                    flash("Failed to save cache timestamp to database.", "warning")
                session['fetch_failed'] = False
                fetch_failed_flag = False
                display_timestamp = now_ts

                # Do not redirect here; let the template render with is_fetching=True
        except Exception as fetch_err:
            logger.error(f"Error during package fetch/normalization: {fetch_err}", exc_info=True)
            normalized_packages = []
            fetch_failed_flag = True
            session['fetch_failed'] = True
            app.config['MANUAL_PACKAGE_CACHE'] = []
            app.config['MANUAL_CACHE_TIMESTAMP'] = None
            display_timestamp = db_timestamp
            flash("Error fetching package list from registries.", "error")

    if not isinstance(normalized_packages, list):
        logger.error(f"normalized_packages is not a list (type: {type(normalized_packages)}). Using empty list.")
        normalized_packages = []
        fetch_failed_flag = True
        session['fetch_failed'] = True
        display_timestamp = None

    total_packages = len(normalized_packages) if normalized_packages else 0
    start = (page - 1) * per_page
    end = start + per_page
    packages_processed_for_page = []
    if normalized_packages:
        for pkg_data in normalized_packages:
            # Fall back to latest_absolute_version if latest_official_version is None
            display_version = pkg_data.get('latest_official_version') or pkg_data.get('latest_absolute_version') or 'N/A'
            pkg_data['display_version'] = display_version
            packages_processed_for_page.append(pkg_data)

    packages_on_page = packages_processed_for_page[start:end]
    total_pages_calc = max(1, (total_packages + per_page - 1) // per_page)

    def iter_pages(left_edge=1, left_current=1, right_current=2, right_edge=1):
        pages = []
        last_page = 0
        for i in range(1, min(left_edge + 1, total_pages_calc + 1)):
            pages.append(i)
            last_page = i
        if last_page < page - left_current - 1:
            pages.append(None)
        for i in range(max(last_page + 1, page - left_current), min(page + right_current + 1, total_pages_calc + 1)):
            pages.append(i)
            last_page = i
        if last_page < total_pages_calc - right_edge:
            pages.append(None)
        for i in range(max(last_page + 1, total_pages_calc - right_edge + 1), total_pages_calc + 1):
            pages.append(i)
        return pages

    pagination = SimpleNamespace(
        items=packages_on_page,
        page=page,
        pages=total_pages_calc,
        total=total_packages,
        per_page=per_page,
        has_prev=(page > 1),
        has_next=(page < total_pages_calc),
        prev_num=(page - 1 if page > 1 else None),
        next_num=(page + 1 if page < total_pages_calc else None),
        iter_pages=iter_pages()
    )

    form = IgImportForm()
    logger.debug(f"--- Rendering search_and_import template (Page: {page}, Total: {total_packages}, Failed Fetch: {fetch_failed_flag}, Display TS: {display_timestamp}) ---")

    return render_template('search_and_import_ig.html',
                           packages=packages_on_page,
                           pagination=pagination,
                           form=form,
                           fetch_failed=fetch_failed_flag,
                           last_cached_timestamp=display_timestamp,
                           is_fetching=is_fetching)

@app.route('/api/search-packages', methods=['GET'], endpoint='api_search_packages')
@swag_from({
    'tags': ['Package Management'],
    'summary': 'Search FHIR packages (HTMX).',
    'description': 'Searches the in-memory package cache. Returns an HTML fragment for HTMX to display matching packages. Primarily for UI interaction.',
    'parameters': [
        {'name': 'search', 'in': 'query', 'type': 'string', 'required': False, 'description': 'Search term for package name or author.'},
        {'name': 'page', 'in': 'query', 'type': 'integer', 'required': False, 'default': 1}
    ],
    'produces': ['text/html'],
    'responses': {
        '200': {'description': 'HTML fragment containing the search results table.'}
    }
})
def api_search_packages():
    """
    Handles HTMX search requests. Filters packages from the in-memory cache.
    Returns an HTML fragment (_search_results_table.html) displaying the
    latest official version if available, otherwise falls back to latest absolute version.
    """
    search_term = request.args.get('search', '').lower()
    page = request.args.get('page', 1, type=int)
    per_page = 50
    logger.debug(f"API search request: term='{search_term}', page={page}")

    all_cached_packages = app.config.get('MANUAL_PACKAGE_CACHE')
    if all_cached_packages is None:
        logger.warning("API search called but in-memory cache is empty. Returning no results.")
        return render_template('_search_results_table.html', packages=[], pagination=None)

    if search_term:
        filtered_packages_raw = [
            pkg for pkg in all_cached_packages
            if isinstance(pkg, dict) and (
                search_term in pkg.get('name', '').lower() or
                search_term in pkg.get('author', '').lower()
            )
        ]
        logger.debug(f"Filtered {len(all_cached_packages)} cached packages down to {len(filtered_packages_raw)} for term '{search_term}'")
    else:
        filtered_packages_raw = all_cached_packages
        logger.debug(f"No search term provided, using all {len(filtered_packages_raw)} cached packages.")

    filtered_packages_processed = []
    for pkg_data in filtered_packages_raw:
        # Fall back to latest_absolute_version if latest_official_version is None
        display_version = pkg_data.get('latest_official_version') or pkg_data.get('latest_absolute_version') or 'N/A'
        pkg_data['display_version'] = display_version
        filtered_packages_processed.append(pkg_data)

    total_filtered = len(filtered_packages_processed)
    start = (page - 1) * per_page
    end = start + per_page
    packages_on_page = filtered_packages_processed[start:end]
    total_pages_calc = max(1, (total_filtered + per_page - 1) // per_page)

    def iter_pages(left_edge=1, left_current=1, right_current=2, right_edge=1):
        pages = []
        last_page = 0
        for i in range(1, min(left_edge + 1, total_pages_calc + 1)):
            pages.append(i)
            last_page = i
        if last_page < page - left_current - 1:
            pages.append(None)
        for i in range(max(last_page + 1, page - left_current), min(page + right_current + 1, total_pages_calc + 1)):
            pages.append(i)
            last_page = i
        if last_page < total_pages_calc - right_edge:
            pages.append(None)
        for i in range(max(last_page + 1, total_pages_calc - right_edge + 1), total_pages_calc + 1):
            pages.append(i)
        return pages

    pagination = SimpleNamespace(
        items=packages_on_page,
        page=page,
        pages=total_pages_calc,
        total=total_filtered,
        per_page=per_page,
        has_prev=(page > 1),
        has_next=(page < total_pages_calc),
        prev_num=(page - 1 if page > 1 else None),
        next_num=(page + 1 if page < total_pages_calc else None),
        iter_pages=iter_pages()
    )

    logger.debug(f"Rendering _search_results_table.html for API response (found {len(packages_on_page)} packages for page {page})")
    html_response = render_template('_search_results_table.html',
                                    packages=packages_on_page,
                                    pagination=pagination)
    return html_response

def safe_parse_version_local(v_str): # Use different name
    """
    Local copy of safe version parser for package_details_view.
    """
    if not v_str or not isinstance(v_str, str):
        return pkg_version_local.parse("0.0.0a0")
    try:
        return pkg_version_local.parse(v_str)
    except pkg_version_local.InvalidVersion:
        original_v_str = v_str
        v_str_norm = v_str.lower()
        base_part = v_str_norm.split('-', 1)[0] if '-' in v_str_norm else v_str_norm
        suffix = v_str_norm.split('-', 1)[1] if '-' in v_str_norm else None
        if re.match(r'^\d+(\.\d+)*$', base_part):
            try:
                if suffix in ['dev', 'snapshot', 'ci-build']: return pkg_version_local.parse(f"{base_part}a0")
                elif suffix in ['draft', 'ballot', 'preview']: return pkg_version_local.parse(f"{base_part}b0")
                elif suffix and suffix.startswith('rc'): return pkg_version_local.parse(f"{base_part}rc{ ''.join(filter(str.isdigit, suffix)) or '0'}")
                return pkg_version_local.parse(base_part)
            except pkg_version_local.InvalidVersion: 
                logger_details.warning(f"[DetailsView] Invalid base version '{base_part}' after splitting '{original_v_str}'. Treating as alpha.")
                return pkg_version_local.parse("0.0.0a0")
            except Exception as e: 
                logger_details.error(f"[DetailsView] Unexpected error parsing FHIR-suffixed version '{original_v_str}': {e}")
                return pkg_version_local.parse("0.0.0a0")
        else: 
            logger_details.warning(f"[DetailsView] Unparseable version '{original_v_str}' (base '{base_part}' not standard). Treating as alpha.")
            return pkg_version_local.parse("0.0.0a0")
    except Exception as e: 
        logger_details.error(f"[DetailsView] Unexpected error in safe_parse_version_local for '{v_str}': {e}")
        return pkg_version_local.parse("0.0.0a0")
# --- End Local Helper Definition ---

@app.route('/package-details/<name>')
def package_details_view(name):
    """Renders package details, using cache/db/fetch."""
    from services import get_package_description
    packages = None
    source = "Not Found"

    def safe_parse_version_local(v_str):
        """
        Local version parser to handle FHIR package versions.
        Uses pkg_version from services or falls back to basic comparison.
        """
        if not v_str or not isinstance(v_str, str):
            logger.warning(f"Invalid version string: {v_str}. Treating as 0.0.0a0.")
            return pkg_version.parse("0.0.0a0")
        try:
            return pkg_version.parse(v_str)
        except pkg_version.InvalidVersion:
            original_v_str = v_str
            v_str_norm = v_str.lower()
            base_part = v_str_norm.split('-', 1)[0] if '-' in v_str_norm else v_str_norm
            suffix = v_str_norm.split('-', 1)[1] if '-' in v_str_norm else None
            if re.match(r'^\d+(\.\d+)+$', base_part):
                try:
                    if suffix in ['dev', 'snapshot', 'ci-build']:
                        return pkg_version.parse(f"{base_part}a0")
                    elif suffix in ['draft', 'ballot', 'preview']:
                        return pkg_version.parse(f"{base_part}b0")
                    elif suffix and suffix.startswith('rc'):
                        rc_num = ''.join(filter(str.isdigit, suffix)) or '0'
                        return pkg_version.parse(f"{base_part}rc{rc_num}")
                    return pkg_version.parse(base_part)
                except pkg_version.InvalidVersion:
                    logger.warning(f"Invalid base version '{base_part}' after splitting '{original_v_str}'. Treating as alpha.")
                    return pkg_version.parse("0.0.0a0")
                except Exception as e:
                    logger.error(f"Unexpected error parsing FHIR-suffixed version '{original_v_str}': {e}")
                    return pkg_version.parse("0.0.0a0")
            else:
                logger.warning(f"Unparseable version '{original_v_str}' (base '{base_part}' not standard). Treating as alpha.")
                return pkg_version.parse("0.0.0a0")
        except Exception as e:
            logger.error(f"Unexpected error in safe_parse_version_local for '{v_str}': {e}")
            return pkg_version.parse("0.0.0a0")

    in_memory_cache = app.config.get('MANUAL_PACKAGE_CACHE')
    if in_memory_cache:
        cached_data = [pkg for pkg in in_memory_cache if isinstance(pkg, dict) and pkg.get('name', '').lower() == name.lower()]
        if cached_data:
            packages = cached_data
            source = "In-Memory Cache"
            logger.debug(f"Package '{name}' found in in-memory cache.")

    if packages is None:
        logger.debug(f"Package '{name}' not in memory cache. Checking database.")
        try:
            db_packages = CachedPackage.query.filter(CachedPackage.package_name.ilike(name)).all()
            if db_packages:
                packages = db_packages
                source = "Database (CachedPackage)"
                logger.debug(f"Package '{name}' found in CachedPackage DB.")
        except Exception as db_err:
            logger.error(f"Database error querying package '{name}': {db_err}", exc_info=True)

    if packages is None:
        logger.info(f"Package '{name}' not found in cache or DB. Fetching from registries.")
        source = "Fetched from Registries"
        try:
            raw_packages = fetch_packages_from_registries(search_term=name)
            normalized_packages = normalize_package_data(raw_packages)
            packages = [pkg for pkg in normalized_packages if pkg.get('name', '').lower() == name.lower()]
            if not packages:
                logger.warning(f"Fetch/Normalization for '{name}' resulted in zero packages.")
            else:
                logger.debug(f"Fetch/Normalization successful for '{name}'. Found {len(packages)} versions.")
        except Exception as fetch_err:
            logger.error(f"Error fetching/normalizing from registries for '{name}': {fetch_err}", exc_info=True)
            flash(f"Error fetching package details for {name} from registries.", "error")
            return redirect(url_for('search_and_import'))

    if not packages:
        logger.warning(f"Package '{name}' could not be found from any source ({source}).")
        flash(f"Package {name} not found.", "error")
        return redirect(url_for('search_and_import'))

    is_dict_list = bool(isinstance(packages[0], dict))
    latest_absolute_version_str = None
    latest_official_version_str = None
    latest_absolute_data = None
    all_versions = []
    dependencies = []

    try:
        if is_dict_list:
            package = packages[0]
            latest_absolute_version_str = package.get('latest_absolute_version')
            latest_official_version_str = package.get('latest_official_version')
            latest_absolute_data = package
            all_versions = package.get('all_versions', [])
            dependencies = package.get('dependencies', [])
        else:
            package = packages[0]
            latest_absolute_version_str = getattr(package, 'version', None)
            latest_official_version_str = getattr(package, 'latest_official_version', None)
            latest_absolute_data = package
            all_versions = getattr(package, 'all_versions', [])
            dependencies = getattr(package, 'dependencies', [])

        if not all_versions:
            logger.error(f"No versions found for package '{name}'. Package data: {package}")
            flash(f"No versions found for package {name}.", "error")
            return redirect(url_for('search_and_import'))

    except Exception as e:
        logger.error(f"Error processing versions for {name}: {e}", exc_info=True)
        flash(f"Error determining latest versions for {name}.", "error")
        return redirect(url_for('search_and_import'))

    if not latest_absolute_data or not latest_absolute_version_str:
        logger.error(f"Failed to determine latest version for '{name}'. Latest data: {latest_absolute_data}, Version: {latest_absolute_version_str}")
        flash(f"Could not determine latest version details for {name}.", "error")
        return redirect(url_for('search_and_import'))

    actual_package_name = None
    package_json = {}
    if isinstance(latest_absolute_data, dict):
        actual_package_name = latest_absolute_data.get('name', name)
        package_json = {
            'name': actual_package_name,
            'version': latest_absolute_version_str,
            'author': latest_absolute_data.get('author'),
            'fhir_version': latest_absolute_data.get('fhir_version'),
            'canonical': latest_absolute_data.get('canonical', ''),
            'dependencies': latest_absolute_data.get('dependencies', []),
            'url': latest_absolute_data.get('url'),
            'registry': latest_absolute_data.get('registry', 'https://packages.simplifier.net'),
            'description': get_package_description(actual_package_name, latest_absolute_version_str, app.config['FHIR_PACKAGES_DIR'])
        }
    else:
        actual_package_name = getattr(latest_absolute_data, 'package_name', getattr(latest_absolute_data, 'name', name))
        package_json = {
            'name': actual_package_name,
            'version': latest_absolute_version_str,
            'author': getattr(latest_absolute_data, 'author', None),
            'fhir_version': getattr(latest_absolute_data, 'fhir_version', None),
            'canonical': getattr(latest_absolute_data, 'canonical', ''),
            'dependencies': getattr(latest_absolute_data, 'dependencies', []),
            'url': getattr(latest_absolute_data, 'url', None),
            'registry': getattr(latest_absolute_data, 'registry', 'https://packages.simplifier.net'),
            'description': get_package_description(actual_package_name, latest_absolute_version_str, app.config['FHIR_PACKAGES_DIR'])
        }

    # Since all_versions now contains dictionaries with version and pubDate, extract just the version for display
    versions_sorted = []
    try:
        versions_sorted = sorted(all_versions, key=lambda x: safe_parse_version_local(x['version']), reverse=True)
    except Exception as sort_err:
        logger.warning(f"Version sorting failed for {name}: {sort_err}. Using basic reverse sort.")
        versions_sorted = sorted(all_versions, key=lambda x: x['pubDate'], reverse=True)

    logger.info(f"Rendering details for package '{package_json.get('name')}' (Source: {source}). Latest: {latest_absolute_version_str}, Official: {latest_official_version_str}")
    return render_template('package_details.html',
                           package_json=package_json,
                           dependencies=dependencies,
                           versions=[v['version'] for v in versions_sorted],
                           package_name=actual_package_name,
                           latest_official_version=latest_official_version_str)

@app.route('/ig-configurator', methods=['GET', 'POST'])
def ig_configurator():
    form = IgYamlForm()
    packages_dir = current_app.config['FHIR_PACKAGES_DIR']
    loaded_igs = services.parse_and_list_igs(packages_dir)
    
    form.igs.choices = [(name, f"{name} ({info['version']})") for name, info in loaded_igs.items()]
    
    yaml_output = None
    
    if form.validate_on_submit():
        selected_ig_names = form.igs.data
        
        try:
            yaml_output = generate_ig_yaml(selected_ig_names, loaded_igs)
            flash("YAML configuration generated successfully!", "success")
        except Exception as e:
            flash(f"Error generating YAML: {str(e)}", "error")
            logger.error(f"Error generating YAML: {str(e)}", exc_info=True)

    return render_template('ig_configurator.html', form=form, yaml_output=yaml_output)

@app.route('/download/<path:filename>')
def download_file(filename):
    """
    Serves a file from the temporary directory created during validation.
    The filename includes the unique temporary directory name to prevent
    unauthorized file access and to correctly locate the file.
    """
    # Use the stored temporary directory path from app.config
    temp_dir = current_app.config.get('VALIDATION_TEMP_DIR')
    
    # If no temporary directory is set, or it's a security risk, return a 404.
    if not temp_dir or not os.path.exists(temp_dir):
        logger.error(f"Download request failed: Temporary directory not found or expired. Path: {temp_dir}")
        return jsonify({"error": "File not found or validation session expired."}), 404
        
    # Construct the full file path.
    file_path = os.path.join(temp_dir, filename)

    if not os.path.exists(file_path):
        logger.error(f"File not found for download: {file_path}")
        return jsonify({"error": "File not found."}), 404
        
    try:
        # Use send_file with as_attachment=True to trigger a download
        return send_file(file_path, as_attachment=True, download_name=filename)
    except Exception as e:
        logger.error(f"Error serving file {file_path}: {str(e)}")
        return jsonify({"error": "Error serving file.", "details": str(e)}), 500

@app.route('/api/clear-validation-results', methods=['POST'])
@csrf.exempt
def clear_validation_results():
    """
    Clears the temporary directory containing the last validation run's results.
    """
    temp_dir = current_app.config.get('VALIDATION_TEMP_DIR')
    if temp_dir and os.path.exists(temp_dir):
        try:
            shutil.rmtree(temp_dir)
            current_app.config['VALIDATION_TEMP_DIR'] = None
            return jsonify({"status": "success", "message": f"Results cleared successfully."})
        except Exception as e:
            logger.error(f"Failed to clear validation results at {temp_dir}: {e}")
            return jsonify({"status": "error", "message": f"Failed to clear results: {e}"}), 500
    else:
        return jsonify({"status": "success", "message": "No results to clear."})

@app.route('/favicon.ico')
def favicon():
    return send_file(os.path.join(app.static_folder, 'favicon.ico'), mimetype='image/x-icon')


if __name__ == '__main__':
    with app.app_context():
        logger.debug(f"Instance path configuration: {app.instance_path}")
        logger.debug(f"Database URI: {app.config['SQLALCHEMY_DATABASE_URI']}")
        logger.debug(f"Packages path: {app.config['FHIR_PACKAGES_DIR']}")
        logger.debug(f"Flask instance folder path: {app.instance_path}")
        logger.debug(f"Directories created/verified: Instance: {app.instance_path}, Packages: {app.config['FHIR_PACKAGES_DIR']}")
        logger.debug(f"Attempting to create database tables for URI: {app.config['SQLALCHEMY_DATABASE_URI']}")
        db.create_all()
        logger.info("Database tables created successfully (if they didn't exist).")
    app.run(host='0.0.0.0', port=5000, debug=False)
