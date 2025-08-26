# forms.py
from flask_wtf import FlaskForm
from wtforms import StringField, SelectField, TextAreaField, BooleanField, SubmitField, FileField, PasswordField, SelectMultipleField
from wtforms.validators import DataRequired, Regexp, ValidationError, URL, Optional, InputRequired
from flask import request
import json
import xml.etree.ElementTree as ET
import re
import logging
import os

logger = logging.getLogger(__name__)

class RetrieveSplitDataForm(FlaskForm):
    """Form for retrieving FHIR bundles and splitting them into individual resources."""
    fhir_server_url = StringField('FHIR Server URL', validators=[URL(), Optional()],
                                 render_kw={'placeholder': 'e.g., https://hapi.fhir.org/baseR4'})
    auth_type = SelectField('Authentication Type (for Custom URL)', choices=[
        ('none', 'None'),
        ('bearerToken', 'Bearer Token'),
        ('basicAuth', 'Basic Authentication')
    ], default='none', validators=[Optional()])
    auth_token = StringField('Bearer Token', validators=[Optional()],
                             render_kw={'placeholder': 'Enter Bearer Token', 'type': 'password'})
    basic_auth_username = StringField('Username', validators=[Optional()],
                                   render_kw={'placeholder': 'Enter Basic Auth Username'})
    basic_auth_password = PasswordField('Password', validators=[Optional()],
                                     render_kw={'placeholder': 'Enter Basic Auth Password'})
    validate_references = BooleanField('Fetch Referenced Resources', default=False,
                                      description="If checked, fetches resources referenced by the initial bundles.")
    fetch_reference_bundles = BooleanField('Fetch Full Reference Bundles (instead of individual resources)', default=False,
                                           description="Requires 'Fetch Referenced Resources'. Fetches e.g. /Patient instead of Patient/id for each reference.",
                                           render_kw={'data-dependency': 'validate_references'})
    split_bundle_zip = FileField('Upload Bundles to Split (ZIP)', validators=[Optional()],
                                render_kw={'accept': '.zip'})
    submit_retrieve = SubmitField('Retrieve Bundles')
    submit_split = SubmitField('Split Bundles')

    def validate(self, extra_validators=None):
        if not super().validate(extra_validators):
            return False
        if self.fetch_reference_bundles.data and not self.validate_references.data:
            self.fetch_reference_bundles.errors.append('Cannot fetch full reference bundles unless "Fetch Referenced Resources" is also checked.')
            return False
        if self.auth_type.data == 'bearerToken' and self.submit_retrieve.data and not self.auth_token.data:
            self.auth_token.errors.append('Bearer Token is required when Bearer Token authentication is selected.')
            return False
        if self.auth_type.data == 'basicAuth' and self.submit_retrieve.data:
            if not self.basic_auth_username.data:
                self.basic_auth_username.errors.append('Username is required for Basic Authentication.')
                return False
            if not self.basic_auth_password.data:
                self.basic_auth_password.errors.append('Password is required for Basic Authentication.')
                return False
        if self.split_bundle_zip.data:
            if not self.split_bundle_zip.data.filename.lower().endswith('.zip'):
                self.split_bundle_zip.errors.append('File must be a ZIP file.')
                return False
        return True

class IgImportForm(FlaskForm):
    """Form for importing Implementation Guides."""
    package_name = StringField('Package Name', validators=[
        DataRequired(),
        Regexp(r'^[a-zA-Z0-9][a-zA-Z0-9\-\.]*[a-zA-Z0-9]$', message="Invalid package name format.")
    ], render_kw={'placeholder': 'e.g., hl7.fhir.au.core'})
    package_version = StringField('Package Version', validators=[
        DataRequired(),
        Regexp(r'^[a-zA-Z0-9\.\-]+$', message="Invalid version format. Use alphanumeric characters, dots, or hyphens (e.g., 1.2.3, 1.1.0-preview, current).")
    ], render_kw={'placeholder': 'e.g., 1.1.0-preview'})
    dependency_mode = SelectField('Dependency Mode', choices=[
        ('recursive', 'Current Recursive'),
        ('patch-canonical', 'Patch Canonical Versions'),
        ('tree-shaking', 'Tree Shaking (Only Used Dependencies)')
    ], default='recursive')
    submit = SubmitField('Import')

class ManualIgImportForm(FlaskForm):
    """Form for manual importing Implementation Guides via file or URL."""
    import_mode = SelectField('Import Mode', choices=[
        ('file', 'Upload File'),
        ('url', 'From URL')
    ], default='file', validators=[DataRequired()])
    tgz_file = FileField('IG Package File (.tgz)', validators=[Optional()],
                         render_kw={'accept': '.tgz'})
    tgz_url = StringField('IG Package URL', validators=[Optional(), URL()],
                          render_kw={'placeholder': 'e.g., https://example.com/hl7.fhir.au.core-1.1.0-preview.tgz'})
    dependency_mode = SelectField('Dependency Mode', choices=[
        ('recursive', 'Current Recursive'),
        ('patch-canonical', 'Patch Canonical Versions'),
        ('tree-shaking', 'Tree Shaking (Only Used Dependencies)')
    ], default='recursive')
    resolve_dependencies = BooleanField('Resolve Dependencies', default=True,
                                       render_kw={'class': 'form-check-input'})
    submit = SubmitField('Import')

    def validate(self, extra_validators=None):
        if not super().validate(extra_validators):
            return False
        mode = self.import_mode.data
        has_file = request and request.files and self.tgz_file.name in request.files and request.files[self.tgz_file.name].filename != ''
        has_url = bool(self.tgz_url.data)  # Convert to boolean: True if non-empty string

        # Ensure exactly one input method is used
        inputs_provided = sum([has_file, has_url])
        if inputs_provided != 1:
            if inputs_provided == 0:
                self.import_mode.errors.append('Please provide input for one import method (File or URL).')
            else:
                self.import_mode.errors.append('Please use only one import method at a time.')
            return False

        # Validate based on import mode
        if mode == 'file':
            if not has_file:
                self.tgz_file.errors.append('A .tgz file is required for File import.')
                return False
            if not self.tgz_file.data.filename.lower().endswith('.tgz'):
                self.tgz_file.errors.append('File must be a .tgz file.')
                return False
        elif mode == 'url':
            if not has_url:
                self.tgz_url.errors.append('A valid URL is required for URL import.')
                return False
            if not self.tgz_url.data.lower().endswith('.tgz'):
                self.tgz_url.errors.append('URL must point to a .tgz file.')
                return False
        else:
            self.import_mode.errors.append('Invalid import mode selected.')
            return False

        return True

class ValidationForm(FlaskForm):
    """Form for validating FHIR samples."""
    package_name = StringField('Package Name', validators=[DataRequired()])
    version = StringField('Package Version', validators=[DataRequired()])
    include_dependencies = BooleanField('Include Dependencies', default=True)
    mode = SelectField('Validation Mode', choices=[
        ('single', 'Single Resource'),
        ('bundle', 'Bundle')
    ], default='single')
    sample_input = TextAreaField('Sample Input', validators=[
        DataRequired(),
    ])
    submit = SubmitField('Validate')

class FSHConverterForm(FlaskForm):
    """Form for converting FHIR resources to FSH."""
    package = SelectField('FHIR Package (Optional)', choices=[('', 'None')], validators=[Optional()])
    input_mode = SelectField('Input Mode', choices=[
        ('file', 'Upload File'),
        ('text', 'Paste Text')
    ], validators=[DataRequired()])
    fhir_file = FileField('FHIR Resource File (JSON/XML)', validators=[Optional()])
    fhir_text = TextAreaField('FHIR Resource Text (JSON/XML)', validators=[Optional()])
    output_style = SelectField('Output Style', choices=[
        ('file-per-definition', 'File per Definition'),
        ('group-by-fsh-type', 'Group by FSH Type'),
        ('group-by-profile', 'Group by Profile'),
        ('single-file', 'Single File')
    ], validators=[DataRequired()])
    log_level = SelectField('Log Level', choices=[
        ('error', 'Error'),
        ('warn', 'Warn'),
        ('info', 'Info'),
        ('debug', 'Debug')
    ], validators=[DataRequired()])
    fhir_version = SelectField('FHIR Version', choices=[
        ('', 'Auto-detect'),
        ('4.0.1', 'R4'),
        ('4.3.0', 'R4B'),
        ('5.0.0', 'R5')
    ], validators=[Optional()])
    fishing_trip = BooleanField('Run Fishing Trip (Round-Trip Validation with SUSHI)', default=False)
    dependencies = TextAreaField('Dependencies (e.g., hl7.fhir.us.core@6.1.0)', validators=[Optional()])
    indent_rules = BooleanField('Indent Rules with Context Paths', default=False)
    meta_profile = SelectField('Meta Profile Handling', choices=[
        ('only-one', 'Only One Profile (Default)'),
        ('first', 'First Profile'),
        ('none', 'Ignore Profiles')
    ], validators=[DataRequired()])
    alias_file = FileField('Alias FSH File', validators=[Optional()])
    no_alias = BooleanField('Disable Alias Generation', default=False)
    submit = SubmitField('Convert to FSH')

    def validate(self, extra_validators=None):
        if not super().validate(extra_validators):
            return False
        has_file_in_request = request and request.files and self.fhir_file.name in request.files and request.files[self.fhir_file.name].filename != ''
        if self.input_mode.data == 'file' and not has_file_in_request:
            if not self.fhir_file.data:
                 self.fhir_file.errors.append('File is required when input mode is Upload File.')
                 return False
        if self.input_mode.data == 'text' and not self.fhir_text.data:
            self.fhir_text.errors.append('Text input is required when input mode is Paste Text.')
            return False
        if self.input_mode.data == 'text' and self.fhir_text.data:
            try:
                content = self.fhir_text.data.strip()
                if not content: pass
                elif content.startswith('{'): json.loads(content)
                elif content.startswith('<'): ET.fromstring(content)
                else:
                    self.fhir_text.errors.append('Text input must be valid JSON or XML.')
                    return False
            except (json.JSONDecodeError, ET.ParseError):
                self.fhir_text.errors.append('Invalid JSON or XML format.')
                return False
        if self.dependencies.data:
            for dep in self.dependencies.data.splitlines():
                dep = dep.strip()
                if dep and not re.match(r'^[a-zA-Z0-9\-\.]+@[a-zA-Z0-9\.\-]+$', dep):
                    self.dependencies.errors.append(f'Invalid dependency format: "{dep}". Use package@version (e.g., hl7.fhir.us.core@6.1.0).')
                    return False
        has_alias_file_in_request = request and request.files and self.alias_file.name in request.files and request.files[self.alias_file.name].filename != ''
        alias_file_data = self.alias_file.data or (request.files.get(self.alias_file.name) if request else None)
        if alias_file_data and alias_file_data.filename:
             if not alias_file_data.filename.lower().endswith('.fsh'):
                  self.alias_file.errors.append('Alias file should have a .fsh extension.')
        return True

class TestDataUploadForm(FlaskForm):
    """Form for uploading FHIR test data."""
    fhir_server_url = StringField('Target FHIR Server URL', validators=[DataRequired(), URL()],
                                  render_kw={'placeholder': 'e.g., http://localhost:8080/fhir'})
    auth_type = SelectField('Authentication Type', choices=[
        ('none', 'None'),
        ('bearerToken', 'Bearer Token'),
        ('basic', 'Basic Authentication')
    ], default='none')
    auth_token = StringField('Bearer Token', validators=[Optional()],
                             render_kw={'placeholder': 'Enter Bearer Token', 'type': 'password'})
    username = StringField('Username', validators=[Optional()],
                          render_kw={'placeholder': 'Enter Basic Auth Username'})
    password = PasswordField('Password', validators=[Optional()],
                            render_kw={'placeholder': 'Enter Basic Auth Password'})
    test_data_file = FileField('Select Test Data File(s)', validators=[InputRequired("Please select at least one file.")],
                              render_kw={'multiple': True, 'accept': '.json,.xml,.zip'})
    validate_before_upload = BooleanField('Validate Resources Before Upload?', default=False,
                                          description="Validate resources against selected package profile before uploading.")
    validation_package_id = SelectField('Validation Profile Package (Optional)',
                                        choices=[('', '-- Select Package for Validation --')],
                                        validators=[Optional()],
                                        description="Select the processed IG package to use for validation.")
    upload_mode = SelectField('Upload Mode', choices=[
        ('individual', 'Individual Resources'),
        ('transaction', 'Transaction Bundle')
    ], default='individual')
    use_conditional_uploads = BooleanField('Use Conditional Upload (Individual Mode Only)?', default=True,
                                           description="If checked, checks resource existence (GET) and uses If-Match (PUT) or creates (PUT). If unchecked, uses simple PUT for all.")
    error_handling = SelectField('Error Handling', choices=[
        ('stop', 'Stop on First Error'),
        ('continue', 'Continue on Error')
    ], default='stop')
    submit = SubmitField('Upload and Process')

    def validate(self, extra_validators=None):
        if not super().validate(extra_validators):
            return False
        if self.validate_before_upload.data and not self.validation_package_id.data:
            self.validation_package_id.errors.append('Please select a package to validate against when pre-upload validation is enabled.')
            return False
        if self.use_conditional_uploads.data and self.upload_mode.data == 'transaction':
            self.use_conditional_uploads.errors.append('Conditional Uploads only apply to the "Individual Resources" mode.')
            return False
        if self.auth_type.data == 'bearerToken' and not self.auth_token.data:
            self.auth_token.errors.append('Bearer Token is required when Bearer Token authentication is selected.')
            return False
        if self.auth_type.data == 'basic':
            if not self.username.data:
                self.username.errors.append('Username is required for Basic Authentication.')
                return False
            if not self.password.data:
                self.password.errors.append('Password is required for Basic Authentication.')
                return False
        return True

class FhirRequestForm(FlaskForm):
    fhir_server_url = StringField('FHIR Server URL', validators=[URL(), Optional()],
                                 render_kw={'placeholder': 'e.g., https://hapi.fhir.org/baseR4'})
    auth_type = SelectField('Authentication Type (for Custom URL)', choices=[
        ('none', 'None'),
        ('bearerToken', 'Bearer Token'),
        ('basicAuth', 'Basic Authentication')
    ], default='none', validators=[Optional()])
    auth_token = StringField('Bearer Token', validators=[Optional()],
                             render_kw={'placeholder': 'Enter Bearer Token', 'type': 'password'})
    basic_auth_username = StringField('Username', validators=[Optional()],
                                   render_kw={'placeholder': 'Enter Basic Auth Username'})
    basic_auth_password = PasswordField('Password', validators=[Optional()],
                                     render_kw={'placeholder': 'Enter Basic Auth Password'})
    submit = SubmitField('Send Request')

    def validate(self, extra_validators=None):
        if not super().validate(extra_validators):
            return False
        if self.fhir_server_url.data:
            if self.auth_type.data == 'bearerToken' and not self.auth_token.data:
                self.auth_token.errors.append('Bearer Token is required when Bearer Token authentication is selected for a custom URL.')
                return False
            if self.auth_type.data == 'basicAuth':
                if not self.basic_auth_username.data:
                    self.basic_auth_username.errors.append('Username is required for Basic Authentication with a custom URL.')
                return False
                if not self.basic_auth_password.data:
                    self.basic_auth_password.errors.append('Password is required for Basic Authentication with a custom URL.')
                    return False
        return True

class IgYamlForm(FlaskForm):
    """Form to select IGs for YAML generation."""
    igs = SelectMultipleField('Select IGs to include', validators=[DataRequired()])
    generate_yaml = SubmitField('Generate YAML')