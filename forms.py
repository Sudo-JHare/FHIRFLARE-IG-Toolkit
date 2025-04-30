# forms.py
from flask_wtf import FlaskForm
from wtforms import StringField, SelectField, TextAreaField, BooleanField, SubmitField, FileField
from wtforms.validators import DataRequired, Regexp, ValidationError, URL, Optional, InputRequired
from flask import request # Import request for file validation in FSHConverterForm
import json
import xml.etree.ElementTree as ET
import re
import logging # Import logging

logger = logging.getLogger(__name__) # Setup logger if needed elsewhere

# Existing forms (IgImportForm, ValidationForm) remain unchanged
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
        # Removed lambda validator for simplicity, can be added back if needed
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
    fhir_version = SelectField('FHIR Version', choices=[ # Corrected label
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
        """Custom validation for FSH Converter Form."""
        # Run default validators first
        if not super().validate(extra_validators):
            return False

        # Check file/text input based on mode
        # Need to check request.files for file uploads as self.fhir_file.data might be None during initial POST validation
        has_file_in_request = request and request.files and self.fhir_file.name in request.files and request.files[self.fhir_file.name].filename != ''
        if self.input_mode.data == 'file' and not has_file_in_request:
            # If it's not in request.files, check if data is already populated (e.g., on re-render after error)
            if not self.fhir_file.data:
                 self.fhir_file.errors.append('File is required when input mode is Upload File.')
                 return False
        if self.input_mode.data == 'text' and not self.fhir_text.data:
            self.fhir_text.errors.append('Text input is required when input mode is Paste Text.')
            return False

        # Validate text input format
        if self.input_mode.data == 'text' and self.fhir_text.data:
            try:
                content = self.fhir_text.data.strip()
                if not content: # Empty text is technically valid but maybe not useful
                     pass # Allow empty text for now
                elif content.startswith('{'):
                    json.loads(content)
                elif content.startswith('<'):
                    ET.fromstring(content) # Basic XML check
                else:
                    # If content exists but isn't JSON or XML, it's an error
                    self.fhir_text.errors.append('Text input must be valid JSON or XML.')
                    return False
            except (json.JSONDecodeError, ET.ParseError):
                self.fhir_text.errors.append('Invalid JSON or XML format.')
                return False

        # Validate dependency format
        if self.dependencies.data:
            for dep in self.dependencies.data.splitlines():
                dep = dep.strip()
                # Allow versions like 'current', 'dev', etc. but require package@version format
                if dep and not re.match(r'^[a-zA-Z0-9\-\.]+@[a-zA-Z0-9\.\-]+$', dep):
                    self.dependencies.errors.append(f'Invalid dependency format: "{dep}". Use package@version (e.g., hl7.fhir.us.core@6.1.0).')
                    return False

        # Validate alias file extension (optional, basic check)
        # Check request.files for alias file as well
        has_alias_file_in_request = request and request.files and self.alias_file.name in request.files and request.files[self.alias_file.name].filename != ''
        alias_file_data = self.alias_file.data or (request.files.get(self.alias_file.name) if request else None)

        if alias_file_data and alias_file_data.filename:
             if not alias_file_data.filename.lower().endswith('.fsh'):
                  self.alias_file.errors.append('Alias file should have a .fsh extension.')
                  # return False # Might be too strict, maybe just warn?

        return True


class TestDataUploadForm(FlaskForm):
    """Form for uploading FHIR test data."""
    fhir_server_url = StringField('Target FHIR Server URL', validators=[DataRequired(), URL()],
                                  render_kw={'placeholder': 'e.g., http://localhost:8080/fhir'})

    auth_type = SelectField('Authentication Type', choices=[
        ('none', 'None'),
        ('bearerToken', 'Bearer Token')
    ], default='none')

    auth_token = StringField('Bearer Token', validators=[Optional()],
                             render_kw={'placeholder': 'Enter Bearer Token', 'type': 'password'})

    test_data_file = FileField('Select Test Data File(s)', validators=[InputRequired("Please select at least one file.")],
                              render_kw={'multiple': True, 'accept': '.json,.xml,.zip'})

    validate_before_upload = BooleanField('Validate Resources Before Upload?', default=False,
                                          description="Validate resources against selected package profile before uploading.")
    validation_package_id = SelectField('Validation Profile Package (Optional)',
                                        choices=[('', '-- Select Package for Validation --')],
                                        validators=[Optional()],
                                        description="Select the processed IG package to use for validation.")

    upload_mode = SelectField('Upload Mode', choices=[
        ('individual', 'Individual Resources'), # Simplified label
        ('transaction', 'Transaction Bundle') # Simplified label
    ], default='individual')

    # --- NEW FIELD for Conditional Upload ---
    use_conditional_uploads = BooleanField('Use Conditional Upload (Individual Mode Only)?', default=True,
                                           description="If checked, checks resource existence (GET) and uses If-Match (PUT) or creates (PUT). If unchecked, uses simple PUT for all.")
    # --- END NEW FIELD ---

    error_handling = SelectField('Error Handling', choices=[
        ('stop', 'Stop on First Error'),
        ('continue', 'Continue on Error')
    ], default='stop')

    submit = SubmitField('Upload and Process')

    def validate(self, extra_validators=None):
        """Custom validation for Test Data Upload Form."""
        if not super().validate(extra_validators): return False
        if self.validate_before_upload.data and not self.validation_package_id.data:
            self.validation_package_id.errors.append('Please select a package to validate against when pre-upload validation is enabled.')
            return False
        # Add check: Conditional uploads only make sense for individual mode
        if self.use_conditional_uploads.data and self.upload_mode.data == 'transaction':
             self.use_conditional_uploads.errors.append('Conditional Uploads only apply to the "Individual Resources" mode.')
             # We might allow this combination but warn the user it has no effect,
             # or enforce it here. Let's enforce for clarity.
             # return False # Optional: Make this a hard validation failure
             # Or just let it pass and ignore the flag in the backend for transaction mode.
             pass # Let it pass for now, backend will ignore if mode is transaction

        return True
