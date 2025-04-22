import sys
import os
sys.path.append(os.path.abspath(os.path.dirname(__file__)))
import datetime
import shutil
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, Response, current_app, session, send_file, make_response
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import FlaskForm
from flask_wtf.csrf import CSRFProtect
import tarfile
import json
import logging
import requests
import re
import services  # Restore full module import
from services import services_bp  # Keep Blueprint import
from forms import IgImportForm, ValidationForm, FSHConverterForm
from wtforms import SubmitField
import tempfile

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-fallback-secret-key-here')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:////app/instance/fhir_ig.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['FHIR_PACKAGES_DIR'] = '/app/instance/fhir_packages'
app.config['API_KEY'] = os.environ.get('API_KEY', 'your-fallback-api-key-here')
app.config['VALIDATE_IMPOSED_PROFILES'] = True
app.config['DISPLAY_PROFILE_RELATIONSHIPS'] = True
app.config['UPLOAD_FOLDER'] = '/app/static/uploads'  # For GoFSH output

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

# Register Blueprint
app.register_blueprint(services_bp, url_prefix='/api')

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
    __table_args__ = (db.UniqueConstraint('package_name', 'version', name='uq_package_version'),)

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
                    pkg_json_member = tar.getmember("package/package.json")
                    fileobj = tar.extractfile(pkg_json_member)
                    if fileobj:
                        pkg_data = json.loads(fileobj.read().decode('utf-8-sig'))
                        name = pkg_data.get('name', name)
                        version = pkg_data.get('version', version)
                        fileobj.close()
            except (KeyError, tarfile.TarError, json.JSONDecodeError, UnicodeDecodeError) as e:
                logger.warning(f"Could not read package.json from {filename}: {e}")
                errors.append(f"Error reading {filename}: {str(e)}")
            except Exception as e:
                logger.error(f"Unexpected error reading package.json from {filename}: {e}", exc_info=True)
                errors.append(f"Unexpected error for {filename}: {str(e)}")
            if name and version:
                packages.append({'name': name, 'version': version, 'filename': filename})
            else:
                logger.warning(f"Skipping package {filename} due to invalid name ('{name}') or version ('{version}')")
                errors.append(f"Invalid package {filename}: name='{name}', version='{version}'")
    name_counts = {}
    for pkg in packages:
        name = pkg['name']
        name_counts[name] = name_counts.get(name, 0) + 1
    for name, count in name_counts.items():
        if count > 1:
            duplicate_groups[name] = sorted([p['version'] for p in packages if p['name'] == name])
    logger.debug(f"Found packages: {packages}")
    logger.debug(f"Errors during package listing: {errors}")
    logger.debug(f"Duplicate groups: {duplicate_groups}")
    return packages, errors, duplicate_groups

@app.route('/')
def index():
    return render_template('index.html', site_name='FHIRFLARE IG Toolkit', now=datetime.datetime.now())

@app.route('/import-ig', methods=['GET', 'POST'])
def import_ig():
    form = IgImportForm()
    if form.validate_on_submit():
        name = form.package_name.data
        version = form.package_version.data
        dependency_mode = form.dependency_mode.data
        try:
            result = services.import_package_and_dependencies(name, version, dependency_mode=dependency_mode)
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
            else:
                if result['errors']:
                    flash(f"Partially imported {name}#{version} with errors during dependency processing. Check logs.", "warning")
                    for err in result['errors']:
                        logger.warning(f"Import warning for {name}#{version}: {err}")
                else:
                    flash(f"Successfully downloaded {name}#{version} and dependencies! Mode: {dependency_mode}", "success")
                return redirect(url_for('view_igs'))
        except Exception as e:
            logger.error(f"Unexpected error during IG import: {str(e)}", exc_info=True)
            flash(f"An unexpected error occurred downloading the IG: {str(e)}", "error")
    else:
        for field, errors in form.errors.items():
            for error in errors:
                flash(f"Error in {getattr(form, field).label.text}: {error}", "danger")
    return render_template('import_ig.html', form=form, site_name='FHIRFLARE IG Toolkit', now=datetime.datetime.now())

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
    form = FlaskForm()
    if form.validate_on_submit():
        filename = request.form.get('filename')
        if not filename or not filename.endswith('.tgz'):
            flash("Invalid package file selected.", "error")
            return redirect(url_for('view_igs'))
        tgz_path = os.path.join(app.config['FHIR_PACKAGES_DIR'], filename)
        if not os.path.exists(tgz_path):
            flash(f"Package file not found: {filename}", "error")
            return redirect(url_for('view_igs'))
        name, version = services.parse_package_filename(filename)
        if not name:
            name = filename[:-4].replace('_', '.')
            version = 'unknown'
            logger.warning(f"Using fallback naming for {filename} -> {name}#{version}")
        try:
            logger.info(f"Starting processing for {name}#{version} from file {filename}")
            package_info = services.process_package_file(tgz_path)
            if package_info.get('errors'):
                flash(f"Processing completed with errors for {name}#{version}: {', '.join(package_info['errors'])}", "warning")
            optional_usage_dict = {
                info['name']: True
                for info in package_info.get('resource_types_info', [])
                if info.get('optional_usage')
            }
            logger.debug(f"Optional usage elements identified: {optional_usage_dict}")
            existing_ig = ProcessedIg.query.filter_by(package_name=name, version=version).first()
            if existing_ig:
                logger.info(f"Updating existing processed record for {name}#{version}")
                existing_ig.processed_date = datetime.datetime.now(tz=datetime.timezone.utc)
                existing_ig.resource_types_info = package_info.get('resource_types_info', [])
                existing_ig.must_support_elements = package_info.get('must_support_elements')
                existing_ig.examples = package_info.get('examples')
                existing_ig.complies_with_profiles = package_info.get('complies_with_profiles', [])
                existing_ig.imposed_profiles = package_info.get('imposed_profiles', [])
                existing_ig.optional_usage_elements = optional_usage_dict
            else:
                logger.info(f"Creating new processed record for {name}#{version}")
                processed_ig = ProcessedIg(
                    package_name=name,
                    version=version,
                    processed_date=datetime.datetime.now(tz=datetime.timezone.utc),
                    resource_types_info=package_info.get('resource_types_info', []),
                    must_support_elements=package_info.get('must_support_elements'),
                    examples=package_info.get('examples'),
                    complies_with_profiles=package_info.get('complies_with_profiles', []),
                    imposed_profiles=package_info.get('imposed_profiles', []),
                    optional_usage_elements=optional_usage_dict
                )
                db.session.add(processed_ig)
            db.session.commit()
            flash(f"Successfully processed {name}#{version}!", "success")
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error processing IG {filename}: {str(e)}", exc_info=True)
            flash(f"Error processing IG '{filename}': {str(e)}", "error")
    else:
        logger.warning(f"Form validation failed for process-igs: {form.errors}")
        flash("CSRF token missing or invalid, or other form error.", "error")
    return redirect(url_for('view_igs'))

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

#---------------------------------------------------------------------------------------OLD backup-----------------------------------
# @app.route('/get-structure')
# def get_structure():
#     package_name = request.args.get('package_name')
#     package_version = request.args.get('package_version')
#     resource_type = request.args.get('resource_type')
#     # Keep view parameter for potential future use or caching, though not used directly in this revised logic
#     view = request.args.get('view', 'snapshot') # Default to snapshot view processing

#     if not all([package_name, package_version, resource_type]):
#         logger.warning("get_structure: Missing query parameters: package_name=%s, package_version=%s, resource_type=%s", package_name, package_version, resource_type)
#         return jsonify({"error": "Missing required query parameters: package_name, package_version, resource_type"}), 400

#     packages_dir = current_app.config.get('FHIR_PACKAGES_DIR')
#     if not packages_dir:
#         logger.error("FHIR_PACKAGES_DIR not configured.")
#         return jsonify({"error": "Server configuration error: Package directory not set."}), 500

#     tgz_filename = services.construct_tgz_filename(package_name, package_version)
#     tgz_path = os.path.join(packages_dir, tgz_filename)
#     sd_data = None
#     fallback_used = False
#     source_package_id = f"{package_name}#{package_version}"
#     logger.debug(f"Attempting to find SD for '{resource_type}' in {tgz_filename}")

#     # --- Fetch SD Data (Keep existing logic including fallback) ---
#     if os.path.exists(tgz_path):
#         try:
#             sd_data, _ = services.find_and_extract_sd(tgz_path, resource_type)
#         # Add error handling as before...
#         except Exception as e:
#             logger.error(f"Unexpected error extracting SD '{resource_type}' from {tgz_path}: {e}", exc_info=True)
#             return jsonify({"error": f"Unexpected error reading StructureDefinition: {str(e)}"}), 500
#     else:
#          logger.warning(f"Package file not found: {tgz_path}")
#          # Try fallback... (keep existing fallback logic)
#     if sd_data is None:
#         logger.info(f"SD for '{resource_type}' not found in {source_package_id}. Attempting fallback to {services.CANONICAL_PACKAGE_ID}.")
#         core_package_name, core_package_version = services.CANONICAL_PACKAGE
#         core_tgz_filename = services.construct_tgz_filename(core_package_name, core_package_version)
#         core_tgz_path = os.path.join(packages_dir, core_tgz_filename)
#         if not os.path.exists(core_tgz_path):
#             # Handle missing core package / download if needed...
#             logger.error(f"Core package {services.CANONICAL_PACKAGE_ID} not found locally.")
#             return jsonify({"error": f"SD for '{resource_type}' not found in primary package, and core package is missing."}), 500
#             # (Add download logic here if desired)
#         try:
#             sd_data, _ = services.find_and_extract_sd(core_tgz_path, resource_type)
#             if sd_data is not None:
#                 fallback_used = True
#                 source_package_id = services.CANONICAL_PACKAGE_ID
#                 logger.info(f"Found SD for '{resource_type}' in fallback package {source_package_id}.")
#             # Add error handling as before...
#         except Exception as e:
#              logger.error(f"Unexpected error extracting SD '{resource_type}' from fallback {core_tgz_path}: {e}", exc_info=True)
#              return jsonify({"error": f"Unexpected error reading fallback StructureDefinition: {str(e)}"}), 500

#     # --- Check if SD data was found ---
#     if not sd_data:
#         logger.error(f"SD for '{resource_type}' not found in primary or fallback package.")
#         return jsonify({"error": f"StructureDefinition for '{resource_type}' not found."}), 404

#     # --- *** START Backend Modification *** ---
#     # Extract snapshot and differential elements
#     snapshot_elements = sd_data.get('snapshot', {}).get('element', [])
#     differential_elements = sd_data.get('differential', {}).get('element', [])

#     # Create a set of element IDs from the differential for efficient lookup
#     # Using element 'id' is generally more reliable than 'path' for matching
#     differential_ids = {el.get('id') for el in differential_elements if el.get('id')}
#     logger.debug(f"Found {len(differential_ids)} unique IDs in differential.")

#     enriched_elements = []
#     if snapshot_elements:
#         logger.debug(f"Processing {len(snapshot_elements)} snapshot elements to add isInDifferential flag.")
#         for element in snapshot_elements:
#             element_id = element.get('id')
#             # Add the isInDifferential flag
#             element['isInDifferential'] = bool(element_id and element_id in differential_ids)
#             enriched_elements.append(element)
#         # Clean narrative from enriched elements (should have been done in find_and_extract_sd, but double check)
#         enriched_elements = [services.remove_narrative(el) for el in enriched_elements]
#     else:
#         # Fallback: If no snapshot, log warning. Maybe return differential only?
#         # Returning only differential might break frontend filtering logic.
#         # For now, return empty, but log clearly.
#         logger.warning(f"No snapshot found for {resource_type} in {source_package_id}. Returning empty element list.")
#         enriched_elements = [] # Or consider returning differential and handle in JS

#     # --- *** END Backend Modification *** ---

#     # Retrieve must_support_paths from DB (keep existing logic)
#     must_support_paths = []
#     processed_ig = ProcessedIg.query.filter_by(package_name=package_name, version=package_version).first()
#     if processed_ig and processed_ig.must_support_elements:
#         # Use the profile ID (which is likely the resource_type for profiles) as the key
#         must_support_paths = processed_ig.must_support_elements.get(resource_type, [])
#         logger.debug(f"Retrieved {len(must_support_paths)} Must Support paths for '{resource_type}' from processed IG DB record.")
#     else:
#          logger.debug(f"No processed IG record or no must_support_elements found in DB for {package_name}#{package_version}, resource {resource_type}")


#     # Construct the response
#     response_data = {
#         'elements': enriched_elements, # Return the processed list
#         'must_support_paths': must_support_paths,
#         'fallback_used': fallback_used,
#         'source_package': source_package_id
#         # Removed raw structure_definition to reduce payload size, unless needed elsewhere
#     }

#     # Use Response object for consistent JSON formatting
#     return Response(json.dumps(response_data, indent=2), mimetype='application/json') # Use indent=2 for readability if debugging
#-----------------------------------------------------------------------------------------------------------------------------------
@app.route('/get-structure')
def get_structure():
    package_name = request.args.get('package_name')
    package_version = request.args.get('package_version')
    resource_type = request.args.get('resource_type') # This is the StructureDefinition ID/Name
    view = request.args.get('view', 'snapshot') # Keep for potential future use

    if not all([package_name, package_version, resource_type]):
        logger.warning("get_structure: Missing query parameters: package_name=%s, package_version=%s, resource_type=%s", package_name, package_version, resource_type)
        return jsonify({"error": "Missing required query parameters: package_name, package_version, resource_type"}), 400

    packages_dir = current_app.config.get('FHIR_PACKAGES_DIR')
    if not packages_dir:
        logger.error("FHIR_PACKAGES_DIR not configured.")
        return jsonify({"error": "Server configuration error: Package directory not set."}), 500

    # Paths for primary and core packages
    tgz_filename = services.construct_tgz_filename(package_name, package_version)
    tgz_path = os.path.join(packages_dir, tgz_filename)
    core_package_name, core_package_version = services.CANONICAL_PACKAGE
    core_tgz_filename = services.construct_tgz_filename(core_package_name, core_package_version)
    core_tgz_path = os.path.join(packages_dir, core_tgz_filename)

    sd_data = None
    search_params_data = [] # Initialize search params list
    fallback_used = False
    source_package_id = f"{package_name}#{package_version}"
    base_resource_type_for_sp = None # Variable to store the base type for SP search

    logger.debug(f"Attempting to find SD for '{resource_type}' in {tgz_filename}")

    # --- Fetch SD Data (Primary Package) ---
    primary_package_exists = os.path.exists(tgz_path)
    core_package_exists = os.path.exists(core_tgz_path)

    if primary_package_exists:
        try:
            sd_data, _ = services.find_and_extract_sd(tgz_path, resource_type)
            if sd_data:
                base_resource_type_for_sp = sd_data.get('type')
                logger.debug(f"Determined base resource type '{base_resource_type_for_sp}' from primary SD '{resource_type}'")
        except Exception as e:
            logger.error(f"Unexpected error extracting SD '{resource_type}' from primary package {tgz_path}: {e}", exc_info=True)
            sd_data = None # Ensure sd_data is None if extraction failed

    # --- Fallback SD Check (if primary failed or file didn't exist) ---
    if sd_data is None:
        logger.info(f"SD for '{resource_type}' not found or failed to load from {source_package_id}. Attempting fallback to {services.CANONICAL_PACKAGE_ID}.")
        if not core_package_exists:
            logger.error(f"Core package {services.CANONICAL_PACKAGE_ID} not found locally at {core_tgz_path}.")
            error_message = f"SD for '{resource_type}' not found in primary package, and core package is missing." if primary_package_exists else f"Primary package {package_name}#{package_version} and core package are missing."
            return jsonify({"error": error_message}), 500 if primary_package_exists else 404

        try:
            sd_data, _ = services.find_and_extract_sd(core_tgz_path, resource_type)
            if sd_data is not None:
                fallback_used = True
                source_package_id = services.CANONICAL_PACKAGE_ID
                base_resource_type_for_sp = sd_data.get('type') # Store base type from fallback SD
                logger.info(f"Found SD for '{resource_type}' in fallback package {source_package_id}. Base type: '{base_resource_type_for_sp}'")
        except Exception as e:
             logger.error(f"Unexpected error extracting SD '{resource_type}' from fallback {core_tgz_path}: {e}", exc_info=True)
             return jsonify({"error": f"Unexpected error reading fallback StructureDefinition: {str(e)}"}), 500

    # --- Check if SD data was ultimately found ---
    if not sd_data:
        # This case should ideally be covered by the checks above, but as a final safety net:
        logger.error(f"SD for '{resource_type}' could not be found in primary or fallback packages.")
        return jsonify({"error": f"StructureDefinition for '{resource_type}' not found."}), 404

    # --- Fetch Search Parameters (Primary Package First) ---
    if base_resource_type_for_sp and primary_package_exists:
         try:
              logger.info(f"Fetching SearchParameters for base type '{base_resource_type_for_sp}' from primary package {tgz_path}")
              search_params_data = services.find_and_extract_search_params(tgz_path, base_resource_type_for_sp)
         except Exception as e:
              logger.error(f"Error extracting SearchParameters for '{base_resource_type_for_sp}' from primary package {tgz_path}: {e}", exc_info=True)
              search_params_data = [] # Continue with empty list on error
    elif not primary_package_exists:
         logger.warning(f"Original package {tgz_path} not found, cannot search it for specific SearchParameters.")
    elif not base_resource_type_for_sp:
         logger.warning(f"Base resource type could not be determined for '{resource_type}', cannot search for SearchParameters.")

    # --- Fetch Search Parameters (Fallback to Core Package if needed) ---
    if not search_params_data and base_resource_type_for_sp and core_package_exists:
         logger.info(f"No relevant SearchParameters found in primary package for '{base_resource_type_for_sp}'. Searching core package {core_tgz_path}.")
         try:
              search_params_data = services.find_and_extract_search_params(core_tgz_path, base_resource_type_for_sp)
              if search_params_data:
                   logger.info(f"Found {len(search_params_data)} SearchParameters for '{base_resource_type_for_sp}' in core package.")
         except Exception as e:
              logger.error(f"Error extracting SearchParameters for '{base_resource_type_for_sp}' from core package {core_tgz_path}: {e}", exc_info=True)
              search_params_data = [] # Continue with empty list on error
    elif not search_params_data and not core_package_exists:
         logger.warning(f"Core package {core_tgz_path} not found, cannot perform fallback search for SearchParameters.")


    # --- Prepare Snapshot/Differential Elements (Existing Logic) ---
    snapshot_elements = sd_data.get('snapshot', {}).get('element', [])
    differential_elements = sd_data.get('differential', {}).get('element', [])
    differential_ids = {el.get('id') for el in differential_elements if el.get('id')}
    logger.debug(f"Found {len(differential_ids)} unique IDs in differential.")
    enriched_elements = []
    if snapshot_elements:
        logger.debug(f"Processing {len(snapshot_elements)} snapshot elements to add isInDifferential flag.")
        for element in snapshot_elements:
            element_id = element.get('id')
            element['isInDifferential'] = bool(element_id and element_id in differential_ids)
            enriched_elements.append(element)
        enriched_elements = [services.remove_narrative(el) for el in enriched_elements]
    else:
        logger.warning(f"No snapshot found for {resource_type} in {source_package_id}. Returning empty element list.")
        enriched_elements = []

    # --- Retrieve Must Support Paths from DB (Existing Logic - slightly refined key lookup) ---
    must_support_paths = []
    processed_ig = ProcessedIg.query.filter_by(package_name=package_name, version=package_version).first()
    if processed_ig and processed_ig.must_support_elements:
        ms_elements_dict = processed_ig.must_support_elements
        if resource_type in ms_elements_dict:
             must_support_paths = ms_elements_dict[resource_type]
             logger.debug(f"Retrieved {len(must_support_paths)} Must Support paths using profile key '{resource_type}' from processed IG DB record.")
        elif base_resource_type_for_sp and base_resource_type_for_sp in ms_elements_dict:
             must_support_paths = ms_elements_dict[base_resource_type_for_sp]
             logger.debug(f"Retrieved {len(must_support_paths)} Must Support paths using base type key '{base_resource_type_for_sp}' from processed IG DB record.")
        else:
             logger.debug(f"No specific Must Support paths found for keys '{resource_type}' or '{base_resource_type_for_sp}' in processed IG DB.")
    else:
         logger.debug(f"No processed IG record or no must_support_elements found in DB for {package_name}#{package_version}")

    # --- Construct the final response ---
    response_data = {
        'elements': enriched_elements,
        'must_support_paths': must_support_paths,
        'search_parameters': search_params_data, # Include potentially populated list
        'fallback_used': fallback_used,
        'source_package': source_package_id
    }

    # Use Response object for consistent JSON formatting and smaller payload
    return Response(json.dumps(response_data, indent=None, separators=(',', ':')), mimetype='application/json')

@app.route('/get-example')
def get_example():
    package_name = request.args.get('package_name')
    version = request.args.get('package_version')
    filename = request.args.get('filename')
    if not all([package_name, version, filename]):
        logger.warning("get_example: Missing query parameters: package_name=%s, version=%s, filename=%s", package_name, version, filename)
        return jsonify({"error": "Missing required query parameters: package_name, package_version, filename"}), 400
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
                # Parse JSON to remove narrative
                content = json.loads(content_string)
                if 'text' in content:
                    logger.debug(f"Removing narrative text from example '{filename}'")
                    del content['text']
                # Return filtered JSON content as a compact string
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

@app.route('/get-package-metadata')
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
def api_push_ig():
    auth_error = check_api_key()
    if auth_error:
        return auth_error
    if not request.is_json:
        return jsonify({"status": "error", "message": "Request must be JSON"}), 400
    data = request.get_json()
    package_name = data.get('package_name')
    version = data.get('version')
    fhir_server_url = data.get('fhir_server_url')
    include_dependencies = data.get('include_dependencies', True) # Default to True if not provided

    # --- Input Validation ---
    if not all([package_name, version, fhir_server_url]):
        return jsonify({"status": "error", "message": "Missing package_name, version, or fhir_server_url"}), 400
    if not (isinstance(fhir_server_url, str) and fhir_server_url.startswith(('http://', 'https://'))):
        return jsonify({"status": "error", "message": "Invalid fhir_server_url format."}), 400
    if not (isinstance(package_name, str) and isinstance(version, str) and
            re.match(r'^[a-zA-Z0-9\-\.]+$', package_name) and
            re.match(r'^[a-zA-Z0-9\.\-\+]+$', version)):
        return jsonify({"status": "error", "message": "Invalid characters in package name or version"}), 400

    # --- File Path Setup ---
    packages_dir = current_app.config.get('FHIR_PACKAGES_DIR')
    if not packages_dir:
        logger.error("[API Push] FHIR_PACKAGES_DIR not configured.")
        return jsonify({"status": "error", "message": "Server configuration error: Package directory not set."}), 500

    tgz_filename = services.construct_tgz_filename(package_name, version)
    tgz_path = os.path.join(packages_dir, tgz_filename)
    if not os.path.exists(tgz_path):
        logger.error(f"[API Push] Main package not found: {tgz_path}")
        return jsonify({"status": "error", "message": f"Package not found locally: {package_name}#{version}"}), 404

    # --- Streaming Response ---
    def generate_stream():
        pushed_packages_info = []
        success_count = 0
        failure_count = 0
        validation_failure_count = 0 # Note: This count is not currently incremented anywhere.
        total_resources_attempted = 0
        processed_resources = set()
        failed_uploads_details = [] # <<<<< Initialize list for failure details

        try:
            yield json.dumps({"type": "start", "message": f"Starting push for {package_name}#{version} to {fhir_server_url}"}) + "\n"
            packages_to_push = [(package_name, version, tgz_path)]
            dependencies_to_include = []

            # --- Dependency Handling ---
            if include_dependencies:
                yield json.dumps({"type": "progress", "message": "Checking dependencies..."}) + "\n"
                metadata = services.get_package_metadata(package_name, version)
                if metadata and metadata.get('imported_dependencies'):
                    dependencies_to_include = metadata['imported_dependencies']
                    yield json.dumps({"type": "info", "message": f"Found {len(dependencies_to_include)} dependencies in metadata."}) + "\n"
                    for dep in dependencies_to_include:
                        dep_name = dep.get('name')
                        dep_version = dep.get('version')
                        if not dep_name or not dep_version:
                            continue
                        dep_tgz_filename = services.construct_tgz_filename(dep_name, dep_version)
                        dep_tgz_path = os.path.join(packages_dir, dep_tgz_filename)
                        if os.path.exists(dep_tgz_path):
                            # Add dependency package to the list of packages to process
                            if (dep_name, dep_version, dep_tgz_path) not in packages_to_push: # Avoid adding duplicates if listed multiple times
                                packages_to_push.append((dep_name, dep_version, dep_tgz_path))
                                yield json.dumps({"type": "progress", "message": f"Queued dependency: {dep_name}#{dep_version}"}) + "\n"
                        else:
                            yield json.dumps({"type": "warning", "message": f"Dependency package file not found, skipping: {dep_name}#{dep_version} ({dep_tgz_filename})"}) + "\n"
                else:
                    yield json.dumps({"type": "info", "message": "No dependency metadata found or no dependencies listed."}) + "\n"

            # --- Resource Extraction ---
            resources_to_upload = []
            seen_resource_files = set() # Track processed filenames to avoid duplicates across packages
            for pkg_name, pkg_version, pkg_path in packages_to_push:
                yield json.dumps({"type": "progress", "message": f"Extracting resources from {pkg_name}#{pkg_version}..."}) + "\n"
                try:
                    with tarfile.open(pkg_path, "r:gz") as tar:
                        for member in tar.getmembers():
                            if (member.isfile() and
                                member.name.startswith('package/') and
                                member.name.lower().endswith('.json') and
                                os.path.basename(member.name).lower() not in ['package.json', '.index.json', 'validation-summary.json', 'validation-oo.json']): # Skip common metadata files

                                if member.name in seen_resource_files: # Skip if already seen from another package (e.g., core resource in multiple IGs)
                                    continue
                                seen_resource_files.add(member.name)

                                try:
                                    with tar.extractfile(member) as f:
                                        resource_data = json.load(f) # Read and parse JSON content
                                        # Basic check for a valid FHIR resource structure
                                        if isinstance(resource_data, dict) and 'resourceType' in resource_data and 'id' in resource_data:
                                            resources_to_upload.append({
                                                "data": resource_data,
                                                "source_package": f"{pkg_name}#{pkg_version}",
                                                "source_filename": member.name
                                            })
                                        else:
                                            yield json.dumps({"type": "warning", "message": f"Skipping invalid/incomplete resource in {member.name} from {pkg_name}#{pkg_version}"}) + "\n"
                                except (json.JSONDecodeError, UnicodeDecodeError) as json_e:
                                    yield json.dumps({"type": "warning", "message": f"Skipping non-JSON or corrupt file {member.name} from {pkg_name}#{pkg_version}: {json_e}"}) + "\n"
                                except Exception as extract_e: # Catch potential errors during file extraction within the tar
                                    yield json.dumps({"type": "warning", "message": f"Error extracting file {member.name} from {pkg_name}#{pkg_version}: {extract_e}"}) + "\n"
                except (tarfile.TarError, FileNotFoundError) as tar_e:
                     # Error reading the package itself
                     error_msg = f"Error reading package {pkg_name}#{pkg_version}: {tar_e}. Skipping its resources."
                     yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                     failure_count += 1 # Increment general failure count
                     failed_uploads_details.append({'resource': f"Package: {pkg_name}#{pkg_version}", 'error': f"TarError/FileNotFound: {tar_e}"}) # <<<<< ADDED package level error
                     continue # Skip to the next package if one fails to open

            total_resources_attempted = len(resources_to_upload)
            yield json.dumps({"type": "info", "message": f"Found {total_resources_attempted} potential resources to upload across all packages."}) + "\n"

            # --- Resource Upload Loop ---
            session = requests.Session() # Use a session for potential connection reuse
            headers = {'Content-Type': 'application/fhir+json', 'Accept': 'application/fhir+json'}

            for i, resource_info in enumerate(resources_to_upload, 1):
                resource = resource_info["data"]
                source_pkg = resource_info["source_package"]
                # source_file = resource_info["source_filename"] # Available if needed
                resource_type = resource.get('resourceType')
                resource_id = resource.get('id')
                resource_log_id = f"{resource_type}/{resource_id}"

                # Skip if already processed (e.g., if same resource ID appeared in multiple packages)
                if resource_log_id in processed_resources:
                    yield json.dumps({"type": "info", "message": f"Skipping duplicate resource ID: {resource_log_id} (already attempted/uploaded)"}) + "\n"
                    continue
                processed_resources.add(resource_log_id)

                resource_url = f"{fhir_server_url.rstrip('/')}/{resource_type}/{resource_id}" # Construct PUT URL
                yield json.dumps({"type": "progress", "message": f"Uploading {resource_log_id} ({i}/{total_resources_attempted}) from {source_pkg}..."}) + "\n"

                try:
                    # Attempt to PUT the resource
                    response = session.put(resource_url, json=resource, headers=headers, timeout=30)
                    response.raise_for_status() # Raises HTTPError for bad responses (4xx or 5xx)

                    yield json.dumps({"type": "success", "message": f"Uploaded {resource_log_id} successfully (Status: {response.status_code})"}) + "\n"
                    success_count += 1

                    # Track which packages contributed successful uploads
                    if source_pkg not in [p["id"] for p in pushed_packages_info]:
                        pushed_packages_info.append({"id": source_pkg, "resource_count": 1})
                    else:
                        for p in pushed_packages_info:
                            if p["id"] == source_pkg:
                                p["resource_count"] += 1
                                break
                # --- Error Handling for Upload ---
                except requests.exceptions.Timeout:
                    error_msg = f"Timeout uploading {resource_log_id} to {resource_url}"
                    yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                    failure_count += 1
                    failed_uploads_details.append({'resource': resource_log_id, 'error': error_msg}) # <<<<< ADDED
                except requests.exceptions.ConnectionError as e:
                    error_msg = f"Connection error uploading {resource_log_id}: {e}"
                    yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                    failure_count += 1
                    failed_uploads_details.append({'resource': resource_log_id, 'error': error_msg}) # <<<<< ADDED
                except requests.exceptions.HTTPError as e:
                    outcome_text = ""
                    # Try to parse OperationOutcome from response
                    try:
                        outcome = e.response.json()
                        if outcome and outcome.get('resourceType') == 'OperationOutcome' and outcome.get('issue'):
                             outcome_text = "; ".join([f"{issue.get('severity', 'info')}: {issue.get('diagnostics') or issue.get('details', {}).get('text', 'No details')}" for issue in outcome['issue']])
                    except ValueError: # Handle cases where response is not JSON
                        outcome_text = e.response.text[:200] # Fallback to raw text
                    error_msg = f"Failed to upload {resource_log_id} (Status: {e.response.status_code}): {outcome_text or str(e)}"
                    yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                    failure_count += 1
                    failed_uploads_details.append({'resource': resource_log_id, 'error': error_msg}) # <<<<< ADDED
                except requests.exceptions.RequestException as e: # Catch other potential request errors
                    error_msg = f"Request error uploading {resource_log_id}: {str(e)}"
                    yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                    failure_count += 1
                    failed_uploads_details.append({'resource': resource_log_id, 'error': error_msg}) # <<<<< ADDED
                except Exception as e: # Catch unexpected errors during the PUT request
                    error_msg = f"Unexpected error uploading {resource_log_id}: {str(e)}"
                    yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                    failure_count += 1
                    failed_uploads_details.append({'resource': resource_log_id, 'error': error_msg}) # <<<<< ADDED
                    logger.error(f"[API Push] Unexpected upload error for {resource_log_id}: {e}", exc_info=True)

            # --- Final Summary ---
            # Determine overall status based on counts
            final_status = "success" if failure_count == 0 and total_resources_attempted > 0 else \
                           "partial" if success_count > 0 else \
                           "failure"
            summary_message = f"Push finished: {success_count} succeeded, {failure_count} failed out of {total_resources_attempted} resources attempted."
            if validation_failure_count > 0: # This count isn't used currently, but keeping structure
                 summary_message += f" ({validation_failure_count} failed validation)."

            # Create final summary payload
            summary = {
                "status": final_status,
                "message": summary_message,
                "target_server": fhir_server_url,
                "package_name": package_name, # Initial requested package
                "version": version,           # Initial requested version
                "included_dependencies": include_dependencies,
                "resources_attempted": total_resources_attempted,
                "success_count": success_count,
                "failure_count": failure_count,
                "validation_failure_count": validation_failure_count,
                "failed_details": failed_uploads_details, # <<<<< ADDED Failure details list
                "pushed_packages_summary": pushed_packages_info # Summary of packages contributing uploads
            }
            yield json.dumps({"type": "complete", "data": summary}) + "\n"
            logger.info(f"[API Push] Completed for {package_name}#{version}. Status: {final_status}. {summary_message}")

        except Exception as e: # Catch critical errors during the entire stream generation
            logger.error(f"[API Push] Critical error during push stream generation for {package_name}#{version}: {str(e)}", exc_info=True)
            # Attempt to yield a final error message to the stream
            error_response = {
                "status": "error",
                "message": f"Server error during push operation: {str(e)}"
                # Optionally add failed_details collected so far if needed
                # "failed_details": failed_uploads_details
            }
            try:
                # Send error info both as a stream message and in the complete data
                yield json.dumps({"type": "error", "message": error_response["message"]}) + "\n"
                yield json.dumps({"type": "complete", "data": error_response}) + "\n"
            except GeneratorExit: # If the client disconnects
                logger.warning("[API Push] Stream closed before final error could be sent.")
            except Exception as yield_e: # Error during yielding the error itself
                logger.error(f"[API Push] Error yielding final error message: {yield_e}")

    # Return the streaming response
    return Response(generate_stream(), mimetype='application/x-ndjson')

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

def create_db():
    logger.debug(f"Attempting to create database tables for URI: {app.config['SQLALCHEMY_DATABASE_URI']}")
    try:
        db.create_all()
        logger.info("Database tables created successfully (if they didn't exist).")
    except Exception as e:
        logger.error(f"Failed to initialize database tables: {e}", exc_info=True)
        raise

with app.app_context():
    create_db()


class FhirRequestForm(FlaskForm):
    submit = SubmitField('Send Request')

@app.route('/fhir')
def fhir_ui():
    form = FhirRequestForm()
    return render_template('fhir_ui.html', form=form, site_name='FHIRFLARE IG Toolkit', now=datetime.datetime.now(), app_mode=app.config['APP_MODE'])

@app.route('/fhir-ui-operations')
def fhir_ui_operations():
    form = FhirRequestForm()
    return render_template('fhir_ui_operations.html', form=form, site_name='FHIRFLARE IG Toolkit', now=datetime.datetime.now(), app_mode=app.config['APP_MODE'])

@app.route('/fhir/<path:subpath>', methods=['GET', 'POST', 'PUT', 'DELETE'])
def proxy_hapi(subpath):
    # Clean subpath to remove r4/, fhir/, leading/trailing slashes
    clean_subpath = subpath.replace('r4/', '').replace('fhir/', '').strip('/')
    hapi_url = f"http://localhost:8080/fhir/{clean_subpath}" if clean_subpath else "http://localhost:8080/fhir"
    headers = {k: v for k, v in request.headers.items() if k != 'Host'}
    logger.debug(f"Proxying request: {request.method} {hapi_url}")
    try:
        response = requests.request(
            method=request.method,
            url=hapi_url,
            headers=headers,
            data=request.get_data(),
            cookies=request.cookies,
            allow_redirects=False,
            timeout=5
        )
        response.raise_for_status()
        # Strip hop-by-hop headers to avoid chunked encoding issues
        response_headers = {
            k: v for k, v in response.headers.items()
            if k.lower() not in (
                'transfer-encoding', 'connection', 'content-encoding',
                'content-length', 'keep-alive', 'proxy-authenticate',
                'proxy-authorization', 'te', 'trailers', 'upgrade'
            )
        }
        response_headers['Content-Length'] = str(len(response.content))
        logger.debug(f"HAPI response: {response.status_code} {response.reason}")
        return response.content, response.status_code, response_headers.items()
    except requests.RequestException as e:
        logger.error(f"HAPI proxy error for {subpath}: {str(e)}")
        error_message = "HAPI FHIR server is unavailable. Please check server status."
        if clean_subpath == 'metadata':
            error_message = "Unable to connect to HAPI FHIR server for status check. Local validation will be used."
        return jsonify({'error': error_message, 'details': str(e)}), 503


@app.route('/api/load-ig-to-hapi', methods=['POST'])
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
                            f"http://localhost:8080/fhir/{resource_type}/{resource_id}",
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