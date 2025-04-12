import sys
import os
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from flask import Flask, render_template, render_template_string, request, redirect, url_for, flash, jsonify, Response
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import FlaskForm
from flask_wtf.csrf import CSRFProtect
import tarfile
import json
from datetime import datetime
import services
import logging
import requests
import re
from forms import IgImportForm, ValidationForm

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////app/instance/fhir_ig.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['FHIR_PACKAGES_DIR'] = '/app/instance/fhir_packages'
app.config['API_KEY'] = 'your-api-key-here'
app.config['VALIDATE_IMPOSED_PROFILES'] = True
app.config['DISPLAY_PROFILE_RELATIONSHIPS'] = True

# Ensure directories exist and are writable
instance_path = '/app/instance'
db_path = '/app/instance/fhir_ig.db'
packages_path = app.config['FHIR_PACKAGES_DIR']

logger.debug(f"Instance path: {instance_path}")
logger.debug(f"Database path: {db_path}")
logger.debug(f"Packages path: {packages_path}")

try:
    os.makedirs(instance_path, exist_ok=True)
    os.makedirs(packages_path, exist_ok=True)
    os.chmod(instance_path, 0o755)
    os.chmod(packages_path, 0o755)
    logger.debug(f"Directories created: {os.listdir(os.path.dirname(__file__))}")
    logger.debug(f"Instance contents: {os.listdir(instance_path)}")
except Exception as e:
    logger.error(f"Failed to create directories: {e}")
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////tmp/fhir_ig.db'
    logger.warning("Falling back to /tmp/fhir_ig.db")

db = SQLAlchemy(app)
csrf = CSRFProtect(app)

class ProcessedIg(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    package_name = db.Column(db.String(128), nullable=False)
    version = db.Column(db.String(32), nullable=False)
    processed_date = db.Column(db.DateTime, nullable=False)
    resource_types_info = db.Column(db.JSON, nullable=False)
    must_support_elements = db.Column(db.JSON, nullable=True)
    examples = db.Column(db.JSON, nullable=True)

# Middleware to check API key
def check_api_key():
    api_key = request.json.get('api_key') if request.is_json else None
    if not api_key:
        logger.error("API key missing in request")
        return jsonify({"status": "error", "message": "API key missing"}), 401
    if api_key != app.config['API_KEY']:
        logger.error(f"Invalid API key provided: {api_key}")
        return jsonify({"status": "error", "message": "Invalid API key"}), 401
    logger.debug("API key validated successfully")
    return None

@app.route('/')
def index():
    return render_template('index.html', site_name='FHIRFLARE IG Toolkit', now=datetime.now())

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
                simplified_msg = error_msg.split(": ")[-1] if ": " in error_msg else error_msg
                flash(f"Failed to import {name}#{version}: {simplified_msg}", "error - check the name and version!")
                return redirect(url_for('import_ig'))
            flash(f"Successfully downloaded {name}#{version} and dependencies! Mode: {dependency_mode}", "success")
            return redirect(url_for('view_igs'))
        except Exception as e:
            flash(f"Error downloading IG: {str(e)}", "error")
    return render_template('import_ig.html', form=form, site_name='FHIRFLARE IG Toolkit', now=datetime.now())

@app.route('/view-igs')
def view_igs():
    form = FlaskForm()
    igs = ProcessedIg.query.all()
    processed_ids = {(ig.package_name, ig.version) for ig in igs}

    packages = []
    packages_dir = app.config['FHIR_PACKAGES_DIR']
    logger.debug(f"Scanning packages directory: {packages_dir}")
    if os.path.exists(packages_dir):
        for filename in os.listdir(packages_dir):
            if filename.endswith('.tgz'):
                last_hyphen_index = filename.rfind('-')
                if last_hyphen_index != -1 and filename.endswith('.tgz'):
                    name = filename[:last_hyphen_index]
                    version = filename[last_hyphen_index + 1:-4]
                    if version[0].isdigit() or version in ('preview', 'current', 'latest'):
                        name = name.replace('_', '.')
                        packages.append({'name': name, 'version': version, 'filename': filename})
                    else:
                        name = filename[:-4]
                        version = ''
                        logger.warning(f"Could not parse version from {filename}, treating as name only")
                        packages.append({'name': name, 'version': version, 'filename': filename})
                else:
                    name = filename[:-4]
                    version = ''
                    logger.warning(f"Could not parse version from {filename}, treating as name only")
                    packages.append({'name': name, 'version': version, 'filename': filename})
        logger.debug(f"Found packages: {packages}")
    else:
        logger.warning(f"Packages directory not found: {packages_dir}")

    # Calculate duplicate_names
    duplicate_names = {}
    for pkg in packages:
        name = pkg['name']
        if name not in duplicate_names:
            duplicate_names[name] = []
        duplicate_names[name].append(pkg)

    # Calculate duplicate_groups
    duplicate_groups = {}
    for name, pkgs in duplicate_names.items():
        if len(pkgs) > 1:
            duplicate_groups[name] = [pkg['version'] for pkg in pkgs]

    # Precompute group colors
    colors = ['bg-warning', 'bg-info', 'bg-success', 'bg-danger']
    group_colors = {}
    for i, name in enumerate(duplicate_groups.keys()):
        group_colors[name] = colors[i % len(colors)]

    return render_template('cp_downloaded_igs.html', form=form, packages=packages, processed_list=igs,
                         processed_ids=processed_ids, duplicate_names=duplicate_names,
                         duplicate_groups=duplicate_groups, group_colors=group_colors,
                         site_name='FHIRFLARE IG Toolkit', now=datetime.now(),
                         config=app.config)

@app.route('/push-igs', methods=['GET', 'POST'])
def push_igs():
    form = FlaskForm()
    igs = ProcessedIg.query.all()
    processed_ids = {(ig.package_name, ig.version) for ig in igs}

    packages = []
    packages_dir = app.config['FHIR_PACKAGES_DIR']
    logger.debug(f"Scanning packages directory: {packages_dir}")
    if os.path.exists(packages_dir):
        for filename in os.listdir(packages_dir):
            if filename.endswith('.tgz'):
                last_hyphen_index = filename.rfind('-')
                if last_hyphen_index != -1 and filename.endswith('.tgz'):
                    name = filename[:last_hyphen_index]
                    version = filename[last_hyphen_index + 1:-4]
                    if version[0].isdigit() or version in ('preview', 'current', 'latest'):
                        name = name.replace('_', '.')
                        packages.append({'name': name, 'version': version, 'filename': filename})
                    else:
                        name = filename[:-4]
                        version = ''
                        logger.warning(f"Could not parse version from {filename}, treating as name only")
                        packages.append({'name': name, 'version': version, 'filename': filename})
                else:
                    name = filename[:-4]
                    version = ''
                    logger.warning(f"Could not parse version from {filename}, treating as name only")
                    packages.append({'name': name, 'version': version, 'filename': filename})
        logger.debug(f"Found packages: {packages}")
    else:
        logger.warning(f"Packages directory not found: {packages_dir}")

    # Calculate duplicate_names
    duplicate_names = {}
    for pkg in packages:
        name = pkg['name']
        if name not in duplicate_names:
            duplicate_names[name] = []
        duplicate_names[name].append(pkg)

    # Calculate duplicate_groups
    duplicate_groups = {}
    for name, pkgs in duplicate_names.items():
        if len(pkgs) > 1:
            duplicate_groups[name] = [pkg['version'] for pkg in pkgs]

    # Precompute group colors
    colors = ['bg-warning', 'bg-info', 'bg-success', 'bg-danger']
    group_colors = {}
    for i, name in enumerate(duplicate_groups.keys()):
        group_colors[name] = colors[i % len(colors)]

    return render_template('cp_push_igs.html', form=form, packages=packages, processed_list=igs,
                         processed_ids=processed_ids, duplicate_names=duplicate_names,
                         duplicate_groups=duplicate_groups, group_colors=group_colors,
                         site_name='FHIRFLARE IG Toolkit', now=datetime.now(),
                         api_key=app.config['API_KEY'],
                         config=app.config)

@app.route('/process-igs', methods=['POST'])
def process_ig():
    form = FlaskForm()
    if form.validate_on_submit():
        filename = request.form.get('filename')
        if not filename or not filename.endswith('.tgz'):
            flash("Invalid package file.", "error")
            return redirect(url_for('view_igs'))
        
        tgz_path = os.path.join(app.config['FHIR_PACKAGES_DIR'], filename)
        if not os.path.exists(tgz_path):
            flash(f"Package file not found: {filename}", "error")
            return redirect(url_for('view_igs'))
        
        try:
            last_hyphen_index = filename.rfind('-')
            if last_hyphen_index != -1 and filename.endswith('.tgz'):
                name = filename[:last_hyphen_index]
                version = filename[last_hyphen_index + 1:-4]
                name = name.replace('_', '.')
            else:
                name = filename[:-4]
                version = ''
                logger.warning(f"Could not parse version from {filename} during processing")
            package_info = services.process_package_file(tgz_path)
            processed_ig = ProcessedIg(
                package_name=name,
                version=version,
                processed_date=datetime.now(),
                resource_types_info=package_info['resource_types_info'],
                must_support_elements=package_info.get('must_support_elements'),
                examples=package_info.get('examples')
            )
            db.session.add(processed_ig)
            db.session.commit()
            flash(f"Successfully processed {name}#{version}!", "success")
        except Exception as e:
            flash(f"Error processing IG: {str(e)}", "error")
    else:
        flash("CSRF token missing or invalid.", "error")
    return redirect(url_for('view_igs'))

@app.route('/delete-ig', methods=['POST'])
def delete_ig():
    form = FlaskForm()
    if form.validate_on_submit():
        filename = request.form.get('filename')
        if not filename or not filename.endswith('.tgz'):
            flash("Invalid package file.", "error")
            return redirect(url_for('view_igs'))
        
        tgz_path = os.path.join(app.config['FHIR_PACKAGES_DIR'], filename)
        metadata_path = tgz_path.replace('.tgz', '.metadata.json')
        if os.path.exists(tgz_path):
            try:
                os.remove(tgz_path)
                if os.path.exists(metadata_path):
                    os.remove(metadata_path)
                    logger.debug(f"Deleted metadata file: {metadata_path}")
                flash(f"Deleted {filename}", "success")
            except Exception as e:
                flash(f"Error deleting {filename}: {str(e)}", "error")
        else:
            flash(f"File not found: {filename}", "error")
    else:
        flash("CSRF token missing or invalid.", "error")
    return redirect(url_for('view_igs'))

@app.route('/unload-ig', methods=['POST'])
def unload_ig():
    form = FlaskForm()
    if form.validate_on_submit():
        ig_id = request.form.get('ig_id')
        if not ig_id:
            flash("Invalid package ID.", "error")
            return redirect(url_for('view_igs'))
        
        processed_ig = db.session.get(ProcessedIg, ig_id)
        if processed_ig:
            try:
                db.session.delete(processed_ig)
                db.session.commit()
                flash(f"Unloaded {processed_ig.package_name}#{processed_ig.version}", "success")
            except Exception as e:
                flash(f"Error unloading package: {str(e)}", "error")
        else:
            flash(f"Package not found with ID: {ig_id}", "error")
    else:
        flash("CSRF token missing or invalid.", "error")
    return redirect(url_for('view_igs'))

@app.route('/view-ig/<int:processed_ig_id>')
def view_ig(processed_ig_id):
    processed_ig = ProcessedIg.query.get_or_404(processed_ig_id)
    profile_list = [t for t in processed_ig.resource_types_info if t.get('is_profile')]
    base_list = [t for t in processed_ig.resource_types_info if not t.get('is_profile')]
    examples_by_type = processed_ig.examples or {}

    # Load metadata to get profile relationships
    package_name = processed_ig.package_name
    version = processed_ig.version
    metadata_filename = f"{services.sanitize_filename_part(package_name)}-{services.sanitize_filename_part(version)}.metadata.json"
    metadata_path = os.path.join(app.config['FHIR_PACKAGES_DIR'], metadata_filename)
    complies_with_profiles = []
    imposed_profiles = []
    if os.path.exists(metadata_path):
        with open(metadata_path, 'r') as f:
            metadata = json.load(f)
            complies_with_profiles = metadata.get('complies_with_profiles', [])
            imposed_profiles = metadata.get('imposed_profiles', [])

    return render_template('cp_view_processed_ig.html', title=f"View {processed_ig.package_name}#{processed_ig.version}",
                          processed_ig=processed_ig, profile_list=profile_list, base_list=base_list,
                          examples_by_type=examples_by_type, site_name='FHIRFLARE IG Toolkit', now=datetime.now(),
                          complies_with_profiles=complies_with_profiles, imposed_profiles=imposed_profiles,
                          config=app.config)

@app.route('/get-structure')
def get_structure_definition():
    package_name = request.args.get('package_name')
    package_version = request.args.get('package_version')
    resource_identifier = request.args.get('resource_type')
    if not all([package_name, package_version, resource_identifier]):
        return jsonify({"error": "Missing query parameters"}), 400
    
    tgz_path = os.path.join(app.config['FHIR_PACKAGES_DIR'], services._construct_tgz_filename(package_name, package_version))
    sd_data = None
    fallback_used = False
    
    if os.path.exists(tgz_path):
        sd_data, _ = services.find_and_extract_sd(tgz_path, resource_identifier)
    
    if sd_data is None:
        logger.debug(f"Structure definition for '{resource_identifier}' not found in {package_name}#{package_version}, attempting fallback to hl7.fhir.r4.core#4.0.1")
        core_package_name = "hl7.fhir.r4.core"
        core_package_version = "4.0.1"
        
        core_tgz_path = os.path.join(app.config['FHIR_PACKAGES_DIR'], services._construct_tgz_filename(core_package_name, core_package_version))
        if not os.path.exists(core_tgz_path):
            logger.debug(f"Core package {core_package_name}#{core_package_version} not found, attempting to download")
            try:
                result = services.import_package_and_dependencies(core_package_name, core_package_version)
                if result['errors'] and not result['downloaded']:
                    logger.error(f"Failed to download core package: {result['errors'][0]}")
                    return jsonify({"error": f"SD for '{resource_identifier}' not found in {package_name}#{package_version}, and failed to download core package: {result['errors'][0]}"}), 404
            except Exception as e:
                logger.error(f"Error downloading core package: {str(e)}")
                return jsonify({"error": f"SD for '{resource_identifier}' not found in {package_name}#{package_version}, and error downloading core package: {str(e)}"}), 500
        
        if os.path.exists(core_tgz_path):
            sd_data, _ = services.find_and_extract_sd(core_tgz_path, resource_identifier)
            if sd_data is None:
                return jsonify({"error": f"SD for '{resource_identifier}' not found in {package_name}#{package_version} or in core package {core_package_name}#{core_package_version}."}), 404
            fallback_used = True
        else:
            return jsonify({"error": f"SD for '{resource_identifier}' not found in {package_name}#{package_version}, and core package {core_package_name}#{core_package_version} could not be located."}), 404
    
    elements = sd_data.get('snapshot', {}).get('element', []) or sd_data.get('differential', {}).get('element', [])
    processed_ig = ProcessedIg.query.filter_by(package_name=package_name, version=package_version).first()
    must_support_paths = processed_ig.must_support_elements.get(resource_identifier, []) if processed_ig else []
    
    response = {
        "elements": elements,
        "must_support_paths": must_support_paths,
        "fallback_used": fallback_used,
        "source_package": f"{core_package_name}#{core_package_version}" if fallback_used else f"{package_name}#{package_version}"
    }
    return jsonify(response)

@app.route('/get-example')
def get_example_content():
    package_name = request.args.get('package_name')
    package_version = request.args.get('package_version')
    example_member_path = request.args.get('filename')
    if not all([package_name, package_version, example_member_path]):
        return jsonify({"error": "Missing query parameters"}), 400
    tgz_path = os.path.join(app.config['FHIR_PACKAGES_DIR'], services._construct_tgz_filename(package_name, package_version))
    if not os.path.exists(tgz_path):
        return jsonify({"error": f"Package file not found: {tgz_path}"}), 404
    if not example_member_path.startswith('package/') or '..' in example_member_path:
        return jsonify({"error": "Invalid example file path."}), 400
    try:
        with tarfile.open(tgz_path, "r:gz") as tar:
            try:
                example_member = tar.getmember(example_member_path)
            except KeyError:
                return jsonify({"error": f"Example file '{example_member_path}' not found."}), 404
            with tar.extractfile(example_member) as example_fileobj:
                content_bytes = example_fileobj.read()
            return content_bytes.decode('utf-8-sig')
    except tarfile.TarError as e:
        return jsonify({"error": f"Error reading {tgz_path}: {e}"}), 500

@app.route('/get-package-metadata')
def get_package_metadata():
    package_name = request.args.get('package_name')
    version = request.args.get('version')
    if not package_name or not version:
        return jsonify({'error': 'Missing package_name or version'}), 400
    metadata = services.get_package_metadata(package_name, version)
    if metadata:
        return jsonify({'dependency_mode': metadata['dependency_mode']})
    return jsonify({'error': 'Metadata not found'}), 404

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
            re.match(r'^[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)+$', package_name) and 
            re.match(r'^[a-zA-Z0-9\.\-]+$', version)):
        return jsonify({"status": "error", "message": "Invalid package name or version format"}), 400

    valid_modes = ['recursive', 'patch-canonical', 'tree-shaking']
    if dependency_mode not in valid_modes:
        return jsonify({"status": "error", "message": f"Invalid dependency mode: {dependency_mode}. Must be one of {valid_modes}"}), 400

    try:
        result = services.import_package_and_dependencies(package_name, version, dependency_mode=dependency_mode)
        if result['errors'] and not result['downloaded']:
            return jsonify({"status": "error", "message": f"Failed to import {package_name}#{version}: {result['errors'][0]}"}), 500

        # Process the package to get compliesWithProfile and imposeProfile
        package_filename = f"{services.sanitize_filename_part(package_name)}-{services.sanitize_filename_part(version)}.tgz"
        package_path = os.path.join(app.config['FHIR_PACKAGES_DIR'], package_filename)
        complies_with_profiles = []
        imposed_profiles = []
        if os.path.exists(package_path):
            process_result = services.process_package_file(package_path)
            complies_with_profiles = process_result.get('complies_with_profiles', [])
            imposed_profiles = process_result.get('imposed_profiles', [])
        else:
            logger.warning(f"Package file not found after import: {package_path}")

        # Check for duplicates
        packages = []
        packages_dir = app.config['FHIR_PACKAGES_DIR']
        if os.path.exists(packages_dir):
            for filename in os.listdir(packages_dir):
                if filename.endswith('.tgz'):
                    last_hyphen_index = filename.rfind('-')
                    if last_hyphen_index != -1 and filename.endswith('.tgz'):
                        name = filename[:last_hyphen_index]
                        version = filename[last_hyphen_index + 1:-4]
                        name = name.replace('_', '.')
                        if version[0].isdigit() or version in ('preview', 'current', 'latest'):
                            packages.append({'name': name, 'version': version, 'filename': filename})
                        else:
                            name = filename[:-4]
                            version = ''
                            packages.append({'name': name, 'version': version, 'filename': filename})
                    else:
                        name = filename[:-4]
                        version = ''
                        packages.append({'name': name, 'version': version, 'filename': filename})

        duplicate_names = {}
        for pkg in packages:
            name = pkg['name']
            if name not in duplicate_names:
                duplicate_names[name] = []
            duplicate_names[name].append(pkg)

        duplicates = []
        for name, pkgs in duplicate_names.items():
            if len(pkgs) > 1:
                versions = [pkg['version'] for pkg in pkgs]
                duplicates.append(f"{name} (exists as {', '.join(versions)})")

        seen = set()
        unique_dependencies = []
        for dep in result.get('dependencies', []):
            dep_str = f"{dep['name']}#{dep['version']}"
            if dep_str not in seen:
                seen.add(dep_str)
                unique_dependencies.append(dep_str)

        response = {
            "status": "success",
            "message": "Package imported successfully",
            "package_name": package_name,
            "version": version,
            "dependency_mode": dependency_mode,
            "dependencies": unique_dependencies,
            "complies_with_profiles": complies_with_profiles,
            "imposed_profiles": imposed_profiles,
            "duplicates": duplicates
        }
        return jsonify(response), 200

    except Exception as e:
        logger.error(f"Error in api_import_ig: {str(e)}")
        return jsonify({"status": "error", "message": f"Error importing package: {str(e)}"}), 500

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
    include_dependencies = data.get('include_dependencies', True)

    if not all([package_name, version, fhir_server_url]):
        return jsonify({"status": "error", "message": "Missing package_name, version, or fhir_server_url"}), 400

    if not (isinstance(package_name, str) and isinstance(version, str) and 
            re.match(r'^[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)+$', package_name) and 
            re.match(r'^[a-zA-Z0-9\.\-]+$', version)):
        return jsonify({"status": "error", "message": "Invalid package name or version format"}), 400

    tgz_filename = services._construct_tgz_filename(package_name, version)
    tgz_path = os.path.join(app.config['FHIR_PACKAGES_DIR'], tgz_filename)
    if not os.path.exists(tgz_path):
        return jsonify({"status": "error", "message": f"Package not found: {package_name}#{version}"}), 404

    def generate_stream():
        try:
            yield json.dumps({"type": "start", "message": f"Starting push for {package_name}#{version}..."}) + "\n"

            resources = []
            packages_to_push = [(package_name, version, tgz_path)]
            if include_dependencies:
                yield json.dumps({"type": "progress", "message": "Processing dependencies..."}) + "\n"
                metadata = services.get_package_metadata(package_name, version)
                dependencies = metadata.get('imported_dependencies', []) if metadata else []
                for dep in dependencies:
                    dep_name = dep['name']
                    dep_version = dep['version']
                    dep_tgz_filename = services._construct_tgz_filename(dep_name, dep_version)
                    dep_tgz_path = os.path.join(app.config['FHIR_PACKAGES_DIR'], dep_tgz_filename)
                    if os.path.exists(dep_tgz_path):
                        packages_to_push.append((dep_name, dep_version, dep_tgz_path))
                        yield json.dumps({"type": "progress", "message": f"Added dependency {dep_name}#{dep_version}"}) + "\n"
                    else:
                        yield json.dumps({"type": "warning", "message": f"Dependency {dep_name}#{dep_version} not found, skipping"}) + "\n"

            for pkg_name, pkg_version, pkg_path in packages_to_push:
                with tarfile.open(pkg_path, "r:gz") as tar:
                    for member in tar.getmembers():
                        if member.name.startswith('package/') and member.name.endswith('.json') and not member.name.endswith('package.json'):
                            with tar.extractfile(member) as f:
                                resource_data = json.load(f)
                                if 'resourceType' in resource_data:
                                    resources.append((resource_data, pkg_name, pkg_version))

            server_response = []
            success_count = 0
            failure_count = 0
            total_resources = len(resources)
            yield json.dumps({"type": "progress", "message": f"Found {total_resources} resources to upload"}) + "\n"

            pushed_packages = []
            for i, (resource, pkg_name, pkg_version) in enumerate(resources, 1):
                resource_type = resource.get('resourceType')
                resource_id = resource.get('id')
                if not resource_type or not resource_id:
                    yield json.dumps({"type": "warning", "message": f"Skipping invalid resource at index {i} from {pkg_name}#{pkg_version}"}) + "\n"
                    failure_count += 1
                    continue

                # Validate against the profile and imposed profiles
                validation_result = services.validate_resource_against_profile(pkg_name, pkg_version, resource, include_dependencies=False)
                if not validation_result['valid']:
                    yield json.dumps({"type": "error", "message": f"Validation failed for {resource_type}/{resource_id} in {pkg_name}#{pkg_version}: {', '.join(validation_result['errors'])}"}) + "\n"
                    failure_count += 1
                    continue

                resource_url = f"{fhir_server_url.rstrip('/')}/{resource_type}/{resource_id}"
                yield json.dumps({"type": "progress", "message": f"Uploading {resource_type}/{resource_id} ({i}/{total_resources}) from {pkg_name}#{pkg_version}..."}) + "\n"

                try:
                    response = requests.put(resource_url, json=resource, headers={'Content-Type': 'application/fhir+json'})
                    response.raise_for_status()
                    server_response.append(f"Uploaded {resource_type}/{resource_id} successfully")
                    yield json.dumps({"type": "success", "message": f"Uploaded {resource_type}/{resource_id} successfully"}) + "\n"
                    success_count += 1
                    if f"{pkg_name}#{pkg_version}" not in pushed_packages:
                        pushed_packages.append(f"{pkg_name}#{pkg_version}")
                except requests.exceptions.RequestException as e:
                    error_msg = f"Failed to upload {resource_type}/{resource_id}: {str(e)}"
                    server_response.append(error_msg)
                    yield json.dumps({"type": "error", "message": error_msg}) + "\n"
                    failure_count += 1

            summary = {
                "status": "success" if failure_count == 0 else "partial",
                "message": f"Push completed: {success_count} resources uploaded, {failure_count} failed",
                "package_name": package_name,
                "version": version,
                "pushed_packages": pushed_packages,
                "server_response": "; ".join(server_response) if server_response else "No resources uploaded",
                "success_count": success_count,
                "failure_count": failure_count
            }
            yield json.dumps({"type": "complete", "data": summary}) + "\n"

        except Exception as e:
            logger.error(f"Error in api_push_ig: {str(e)}")
            error_response = {
                "status": "error",
                "message": f"Error pushing package: {str(e)}"
            }
            yield json.dumps({"type": "error", "message": error_response["message"]}) + "\n"
            yield json.dumps({"type": "complete", "data": error_response}) + "\n"

    return Response(generate_stream(), mimetype='application/x-ndjson')

@app.route('/validate-sample', methods=['GET', 'POST'])
def validate_sample():
    form = ValidationForm()
    validation_report = None
    packages = []
    
    # Load available packages
    packages_dir = app.config['FHIR_PACKAGES_DIR']
    if os.path.exists(packages_dir):
        for filename in os.listdir(packages_dir):
            if filename.endswith('.tgz'):
                last_hyphen_index = filename.rfind('-')
                if last_hyphen_index != -1 and filename.endswith('.tgz'):
                    name = filename[:last_hyphen_index]
                    version = filename[last_hyphen_index + 1:-4]
                    name = name.replace('_', '.')
                    try:
                        with tarfile.open(os.path.join(packages_dir, filename), 'r:gz') as tar:
                            package_json = tar.extractfile('package/package.json')
                            pkg_info = json.load(package_json)
                            packages.append({'name': pkg_info['name'], 'version': pkg_info['version']})
                    except Exception as e:
                        logger.warning(f"Error reading package {filename}: {e}")
                        continue

    if form.validate_on_submit():
        package_name = form.package_name.data
        version = form.version.data
        include_dependencies = form.include_dependencies.data
        mode = form.mode.data
        try:
            sample_input = json.loads(form.sample_input.data)
            if mode == 'single':
                validation_report = services.validate_resource_against_profile(
                    package_name, version, sample_input, include_dependencies
                )
            else:  # mode == 'bundle'
                validation_report = services.validate_bundle_against_profile(
                    package_name, version, sample_input, include_dependencies
                )
            flash("Validation completed.", 'success')
        except json.JSONDecodeError:
            flash("Invalid JSON format in sample input.", 'error')
        except Exception as e:
            logger.error(f"Error validating sample: {e}")
            flash(f"Error validating sample: {str(e)}", 'error')
            validation_report = {'valid': False, 'errors': [str(e)], 'warnings': [], 'results': {}}

    return render_template(
        'validate_sample.html',
        form=form,
        packages=packages,
        validation_report=validation_report,
        site_name='FHIRFLARE IG Toolkit',
        now=datetime.now()
    )

with app.app_context():
    logger.debug(f"Creating database at: {app.config['SQLALCHEMY_DATABASE_URI']}")
    try:
        db.create_all()
        logger.debug("Database initialization complete")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise

if __name__ == '__main__':
    app.run(debug=True)