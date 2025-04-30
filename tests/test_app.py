import unittest
import os
import sys
import json
import tarfile
import shutil
import io
import requests
from unittest.mock import patch, MagicMock, mock_open, call
from flask import Flask, session
from flask.testing import FlaskClient
from datetime import datetime, timezone

# Add the parent directory (/app) to sys.path
APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from app import app, db, ProcessedIg
import services

# Helper function to parse NDJSON stream
def parse_ndjson(byte_stream):
    decoded_stream = byte_stream.decode('utf-8').strip()
    if not decoded_stream:
        return []
    lines = decoded_stream.split('\n')
    return [json.loads(line) for line in lines if line.strip()]

class TestFHIRFlareIGToolkit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config['TESTING'] = True
        app.config['WTF_CSRF_ENABLED'] = False
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        cls.test_packages_dir = os.path.join(os.path.dirname(__file__), 'test_fhir_packages_temp')
        app.config['FHIR_PACKAGES_DIR'] = cls.test_packages_dir
        app.config['SECRET_KEY'] = 'test-secret-key'
        app.config['API_KEY'] = 'test-api-key'
        app.config['VALIDATE_IMPOSED_PROFILES'] = True
        app.config['DISPLAY_PROFILE_RELATIONSHIPS'] = True

        cls.app_context = app.app_context()
        cls.app_context.push()
        db.create_all()
        cls.client = app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.app_context.pop()
        if os.path.exists(cls.test_packages_dir):
            shutil.rmtree(cls.test_packages_dir)

    def setUp(self):
        if os.path.exists(self.test_packages_dir):
            shutil.rmtree(self.test_packages_dir)
        os.makedirs(self.test_packages_dir, exist_ok=True)
        with self.app_context:
            for item in db.session.query(ProcessedIg).all():
                db.session.delete(item)
            db.session.commit()

    def tearDown(self):
        pass

    # Helper Method
    def create_mock_tgz(self, filename, files_content):
        tgz_path = os.path.join(app.config['FHIR_PACKAGES_DIR'], filename)
        with tarfile.open(tgz_path, "w:gz") as tar:
            for name, content in files_content.items():
                if isinstance(content, (dict, list)):
                    data_bytes = json.dumps(content).encode('utf-8')
                elif isinstance(content, str):
                    data_bytes = content.encode('utf-8')
                else:
                    raise TypeError(f"Unsupported type for mock file '{name}': {type(content)}")
                file_io = io.BytesIO(data_bytes)
                tarinfo = tarfile.TarInfo(name=name)
                tarinfo.size = len(data_bytes)
                tarinfo.mtime = int(datetime.now(timezone.utc).timestamp())
                tar.addfile(tarinfo, file_io)
        return tgz_path

    # --- Phase 1 Tests ---

    def test_01_navigate_fhir_path(self):
        resource = {
            "resourceType": "Patient",
            "name": [{"given": ["John"]}],
            "identifier": [{"system": "http://hl7.org/fhir/sid/us-ssn", "sliceName": "us-ssn"}],
            "extension": [{"url": "http://hl7.org/fhir/StructureDefinition/patient-birthPlace", "valueAddress": {"city": "Boston"}}]
        }
        self.assertEqual(services.navigate_fhir_path(resource, "Patient.name[0].given"), ["John"])
        self.assertEqual(services.navigate_fhir_path(resource, "Patient.identifier:us-ssn.system"), "http://hl7.org/fhir/sid/us-ssn")
        self.assertEqual(services.navigate_fhir_path(resource, "Patient.extension", extension_url="http://hl7.org/fhir/StructureDefinition/patient-birthPlace")["valueAddress"]["city"], "Boston")
        with patch('fhirpath.evaluate', side_effect=Exception("fhirpath error")):
            self.assertEqual(services.navigate_fhir_path(resource, "Patient.name[0].given"), ["John"])

    def test_02_render_node_as_li(self):
        node = {
            "element": {"path": "Patient.identifier", "id": "Patient.identifier", "sliceName": "us-ssn", "min": 0, "max": "*", "type": [{"code": "Identifier"}]},
            "name": "identifier",
            "children": {}
        }
        must_support_paths = {"Patient.identifier:us-ssn"}
        with app.app_context:
            html = render_template('cp_view_processed_ig.html', processed_ig=MagicMock(must_support_elements={"USCorePatientProfile": ["Patient.identifier:us-ssn"]}), profile_list=[{"name": "USCorePatientProfile"}], base_list=[])
            self.assertIn("identifier:us-ssn", html)
            self.assertIn("list-group-item-warning", html)
            self.assertIn("Must Support (Slice: us-ssn)", html)

    # --- Basic Page Rendering Tests ---

    def test_03_homepage(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'FHIRFLARE IG Toolkit', response.data)

    def test_04_import_ig_page(self):
        response = self.client.get('/import-ig')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Import IG', response.data)
        self.assertIn(b'Package Name', response.data)
        self.assertIn(b'Package Version', response.data)
        self.assertIn(b'name="dependency_mode"', response.data)

    @patch('app.list_downloaded_packages', return_value=([], [], {}))
    def test_05_view_igs_no_packages(self, mock_list_pkgs):
        response = self.client.get('/view-igs')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'<th>Package Name</th>', response.data)
        self.assertIn(b'No packages downloaded yet.', response.data)
        mock_list_pkgs.assert_called_once()

    def test_06_view_igs_with_packages(self):
        self.create_mock_tgz('hl7.fhir.us.core-6.1.0.tgz', {'package/package.json': {'name': 'hl7.fhir.us.core', 'version': '6.1.0'}})
        response = self.client.get('/view-igs')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'hl7.fhir.us.core', response.data)
        self.assertIn(b'6.1.0', response.data)
        self.assertIn(b'<th>Package Name</th>', response.data)

    @patch('app.render_template')
    def test_07_push_igs_page(self, mock_render_template):
        mock_render_template.return_value = "Mock Render"
        response = self.client.get('/push-igs')
        self.assertEqual(response.status_code, 200)
        mock_render_template.assert_called()
        call_args, call_kwargs = mock_render_template.call_args
        self.assertEqual(call_args[0], 'cp_push_igs.html')

    # --- UI Form Tests ---

    @patch('app.services.import_package_and_dependencies')
    def test_10_import_ig_form_success(self, mock_import):
        mock_import.return_value = {'requested': ('hl7.fhir.us.core', '6.1.0'), 'processed': {('hl7.fhir.us.core', '6.1.0')}, 'downloaded': {('hl7.fhir.us.core', '6.1.0'): 'path/pkg.tgz'}, 'all_dependencies': {}, 'dependencies': [], 'errors': []}
        response = self.client.post('/import-ig', data={'package_name': 'hl7.fhir.us.core', 'package_version': '6.1.0', 'dependency_mode': 'recursive'}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Successfully downloaded hl7.fhir.us.core#6.1.0 and dependencies! Mode: recursive', response.data)
        mock_import.assert_called_once_with('hl7.fhir.us.core', '6.1.0', dependency_mode='recursive')

    @patch('app.services.import_package_and_dependencies')
    def test_11_import_ig_form_failure_404(self, mock_import):
        mock_import.return_value = {'requested': ('invalid.package', '1.0.0'), 'processed': set(), 'downloaded': {}, 'all_dependencies': {}, 'dependencies': [], 'errors': ['HTTP error fetching package: 404 Client Error: Not Found for url: ...']}
        response = self.client.post('/import-ig', data={'package_name': 'invalid.package', 'package_version': '1.0.0', 'dependency_mode': 'recursive'}, follow_redirects=False)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Package not found on registry (404)', response.data)

    @patch('app.services.import_package_and_dependencies')
    def test_12_import_ig_form_failure_conn_error(self, mock_import):
        mock_import.return_value = {'requested': ('conn.error.pkg', '1.0.0'), 'processed': set(), 'downloaded': {}, 'all_dependencies': {}, 'dependencies': [], 'errors': ['Connection error: Cannot connect to registry...']}
        response = self.client.post('/import-ig', data={'package_name': 'conn.error.pkg', 'package_version': '1.0.0', 'dependency_mode': 'recursive'}, follow_redirects=False)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Could not connect to the FHIR package registry', response.data)

    def test_13_import_ig_form_invalid_input(self):
        response = self.client.post('/import-ig', data={'package_name': 'invalid@package', 'package_version': '1.0.0', 'dependency_mode': 'recursive'}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Error in Package Name: Invalid package name format.', response.data)

    @patch('app.services.process_package_file')
    @patch('app.services.parse_package_filename')
    def test_20_process_ig_success(self, mock_parse, mock_process):
        pkg_name = 'hl7.fhir.us.core'
        pkg_version = '6.1.0'
        filename = f'{pkg_name}-{pkg_version}.tgz'
        mock_parse.return_value = (pkg_name, pkg_version)
        mock_process.return_value = {
            'resource_types_info': [{'name': 'Patient', 'type': 'Patient', 'is_profile': False, 'must_support': True, 'optional_usage': False}],
            'must_support_elements': {'Patient': ['Patient.name', 'Patient.identifier:us-ssn']},
            'examples': {'Patient': ['package/Patient-example.json']},
            'complies_with_profiles': [],
            'imposed_profiles': ['http://hl7.org/fhir/StructureDefinition/Patient'],
            'errors': []
        }
        self.create_mock_tgz(filename, {'package/package.json': {'name': pkg_name, 'version': pkg_version}})
        response = self.client.post('/process-igs', data={'filename': filename}, follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith('/view-igs'))
        with self.client.session_transaction() as sess:
            self.assertIn(('success', f'Successfully processed {pkg_name}#{pkg_version}!'), sess.get('_flashes', []))
        mock_parse.assert_called_once_with(filename)
        mock_process.assert_called_once_with(os.path.join(app.config['FHIR_PACKAGES_DIR'], filename))
        processed_ig = db.session.query(ProcessedIg).filter_by(package_name=pkg_name, version=pkg_version).first()
        self.assertIsNotNone(processed_ig)
        self.assertEqual(processed_ig.package_name, pkg_name)
        self.assertIn('Patient.name', processed_ig.must_support_elements.get('Patient', []))

    def test_21_process_ig_file_not_found(self):
        response = self.client.post('/process-igs', data={'filename': 'nonexistent.tgz'}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Package file not found: nonexistent.tgz', response.data)

    def test_22_delete_ig_success(self):
        filename = 'hl7.fhir.us.core-6.1.0.tgz'
        metadata_filename = 'hl7.fhir.us.core-6.1.0.metadata.json'
        self.create_mock_tgz(filename, {'package/package.json': {'name': 'hl7.fhir.us.core', 'version': '6.1.0'}})
        metadata_path = os.path.join(app.config['FHIR_PACKAGES_DIR'], metadata_filename)
        open(metadata_path, 'w').write(json.dumps({'name': 'hl7.fhir.us.core'}))
        self.assertTrue(os.path.exists(os.path.join(app.config['FHIR_PACKAGES_DIR'], filename)))
        self.assertTrue(os.path.exists(metadata_path))
        response = self.client.post('/delete-ig', data={'filename': filename}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(f'Deleted: {filename}, {metadata_filename}'.encode('utf-8'), response.data)
        self.assertFalse(os.path.exists(os.path.join(app.config['FHIR_PACKAGES_DIR'], filename)))
        self.assertFalse(os.path.exists(metadata_path))

    def test_23_unload_ig_success(self):
        processed_ig = ProcessedIg(package_name='test.pkg', version='1.0', processed_date=datetime.now(timezone.utc), resource_types_info=[], must_support_elements={}, examples={})
        db.session.add(processed_ig)
        db.session.commit()
        ig_id = processed_ig.id
        self.assertIsNotNone(db.session.get(ProcessedIg, ig_id))
        response = self.client.post('/unload-ig', data={'ig_id': str(ig_id)}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Unloaded processed data for test.pkg#1.0', response.data)
        self.assertIsNone(db.session.get(ProcessedIg, ig_id))

    # --- Phase 2 Tests ---

    @patch('os.path.exists', return_value=True)
    @patch('tarfile.open')
    @patch('requests.put')
    def test_30_load_ig_to_hapi_success(self, mock_requests_put, mock_tarfile_open, mock_os_exists):
        pkg_name = 'hl7.fhir.us.core'
        pkg_version = '6.1.0'
        filename = f'{pkg_name}-{pkg_version}.tgz'
        self.create_mock_tgz(filename, {
            'package/package.json': {'name': pkg_name, 'version': pkg_version},
            'package/Patient-profile.json': {'resourceType': 'StructureDefinition', 'id': 'us-core-patient'}
        })
        mock_tar = MagicMock()
        profile_member = MagicMock(spec=tarfile.TarInfo)
        profile_member.name = 'package/Patient-profile.json'
        profile_member.isfile.return_value = True
        mock_tar.getmembers.return_value = [profile_member]
        mock_tar.extractfile.return_value = io.BytesIO(json.dumps({'resourceType': 'StructureDefinition', 'id': 'us-core-patient'}).encode('utf-8'))
        mock_tarfile_open.return_value.__enter__.return_value = mock_tar
        mock_requests_put.return_value = MagicMock(status_code=200)
        response = self.client.post(
            '/api/load-ig-to-hapi',
            data=json.dumps({'package_name': pkg_name, 'version': pkg_version}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data['status'], 'success')
        mock_requests_put.assert_called_once_with(
            'http://localhost:8080/fhir/StructureDefinition/us-core-patient',
            json={'resourceType': 'StructureDefinition', 'id': 'us-core-patient'},
            headers={'Content-Type': 'application/fhir+json'}
        )

    def test_31_load_ig_to_hapi_not_found(self):
        response = self.client.post(
            '/api/load-ig-to-hapi',
            data=json.dumps({'package_name': 'nonexistent', 'version': '1.0'}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 404)
        data = json.loads(response.data)
        self.assertEqual(data['error'], 'Package not found')

    @patch('os.path.exists', return_value=True)
    @patch('requests.post')
    def test_32_api_validate_sample_hapi_success(self, mock_requests_post, mock_os_exists):
        pkg_name = 'hl7.fhir.us.core'
        pkg_version = '6.1.0'
        sample_resource = {
            'resourceType': 'Patient',
            'id': 'valid1',
            'meta': {'profile': ['http://hl7.org/fhir/us/core/StructureDefinition/us-core-patient']},
            'name': [{'given': ['John'], 'family': 'Doe'}]
        }
        mock_requests_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                'resourceType': 'OperationOutcome',
                'issue': [{'severity': 'warning', 'diagnostics': 'Must Support element Patient.identifier missing'}]
            }
        )
        response = self.client.post(
            '/api/validate-sample',
            data=json.dumps({
                'package_name': pkg_name,
                'version': pkg_version,
                'sample_data': json.dumps(sample_resource),
                'mode': 'single',
                'include_dependencies': True
            }),
            content_type='application/json',
            headers={'X-API-Key': 'test-api-key'}
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data['valid'])
        self.assertEqual(data['warnings'], ['Must Support element Patient.identifier missing'])
        mock_requests_post.assert_called_once_with(
            'http://localhost:8080/fhir/Patient/$validate?profile=http://hl7.org/fhir/us/core/StructureDefinition/us-core-patient',
            json=sample_resource,
            headers={'Content-Type': 'application/fhir+json', 'Accept': 'application/fhir+json'},
            timeout=10
        )

    @patch('os.path.exists', return_value=True)
    @patch('requests.post', side_effect=requests.ConnectionError("HAPI down"))
    @patch('services.navigate_fhir_path')
    def test_33_api_validate_sample_hapi_fallback(self, mock_navigate_fhir_path, mock_requests_post, mock_os_exists):
        pkg_name = 'hl7.fhir.us.core'
        pkg_version = '6.1.0'
        sample_resource = {
            'resourceType': 'Patient',
            'id': 'valid1',
            'meta': {'profile': ['http://hl7.org/fhir/us/core/StructureDefinition/us-core-patient']}
        }
        mock_navigate_fhir_path.return_value = None
        self.create_mock_tgz(f'{pkg_name}-{pkg_version}.tgz', {
            'package/package.json': {'name': pkg_name, 'version': pkg_version},
            'package/StructureDefinition-us-core-patient.json': {
                'resourceType': 'StructureDefinition',
                'snapshot': {'element': [{'path': 'Patient.name', 'min': 1}, {'path': 'Patient.identifier', 'mustSupport': True}]}
            }
        })
        response = self.client.post(
            '/api/validate-sample',
            data=json.dumps({
                'package_name': pkg_name,
                'version': pkg_version,
                'sample_data': json.dumps(sample_resource),
                'mode': 'single',
                'include_dependencies': True
            }),
            content_type='application/json',
            headers={'X-API-Key': 'test-api-key'}
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertFalse(data['valid'])
        self.assertIn('Required element Patient.name missing', data['errors'])
        self.assertIn('HAPI validation failed', [d['issue'] for d in data['details']])

    # --- Phase 3 Tests ---

    @patch('requests.get')
    def test_34_hapi_status_check(self, mock_requests_get):
        mock_requests_get.return_value = MagicMock(status_code=200, json=lambda: {'resourceType': 'CapabilityStatement'})
        response = self.client.get('/fhir/metadata')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data['resourceType'], 'CapabilityStatement')
        mock_requests_get.side_effect = requests.ConnectionError("HAPI down")
        response = self.client.get('/fhir/metadata')
        self.assertEqual(response.status_code, 503)
        data = json.loads(response.data)
        self.assertIn('Unable to connect to HAPI FHIR server', data['error'])

    def test_35_validate_sample_ui_rendering(self):
        pkg_name = 'hl7.fhir.us.core'
        pkg_version = '6.1.0'
        sample_resource = {
            'resourceType': 'Patient',
            'id': 'test',
            'meta': {'profile': ['http://hl7.org/fhir/us/core/StructureDefinition/us-core-patient']}
        }
        self.create_mock_tgz(f'{pkg_name}-{pkg_version}.tgz', {
            'package/package.json': {'name': pkg_name, 'version': pkg_version},
            'package/StructureDefinition-us-core-patient.json': {
                'resourceType': 'StructureDefinition',
                'snapshot': {'element': [{'path': 'Patient.name', 'min': 1}, {'path': 'Patient.identifier', 'mustSupport': True}]}
            }
        })
        response = self.client.post(
            '/api/validate-sample',
            data=json.dumps({
                'package_name': pkg_name,
                'version': pkg_version,
                'sample_data': json.dumps(sample_resource),
                'mode': 'single',
                'include_dependencies': True
            }),
            content_type='application/json',
            headers={'X-API-Key': 'test-api-key'}
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertFalse(data['valid'])
        self.assertIn('Required element Patient.name missing', data['errors'])
        self.assertIn('Must Support element Patient.identifier missing', data['warnings'])
        response = self.client.get('/validate-sample')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'us-core-patient', response.data)

    def test_36_must_support_consistency(self):
        pkg_name = 'hl7.fhir.us.core'
        pkg_version = '6.1.0'
        filename = f'{pkg_name}-{pkg_version}.tgz'
        self.create_mock_tgz(filename, {
            'package/package.json': {'name': pkg_name, 'version': pkg_version},
            'package/StructureDefinition-us-core-patient.json': {
                'resourceType': 'StructureDefinition',
                'snapshot': {'element': [{'path': 'Patient.name', 'min': 1}, {'path': 'Patient.identifier', 'mustSupport': True, 'sliceName': 'us-ssn'}]}
            }
        })
        services.process_package_file(os.path.join(app.config['FHIR_PACKAGES_DIR'], filename))
        sample_resource = {
            'resourceType': 'Patient',
            'id': 'test',
            'meta': {'profile': ['http://hl7.org/fhir/us/core/StructureDefinition/us-core-patient']}
        }
        response = self.client.post(
            '/api/validate-sample',
            data=json.dumps({
                'package_name': pkg_name,
                'version': pkg_version,
                'sample_data': json.dumps(sample_resource),
                'mode': 'single',
                'include_dependencies': True
            }),
            content_type='application/json',
            headers={'X-API-Key': 'test-api-key'}
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertIn('Must Support element Patient.identifier missing', data['warnings'])
        with self.app_context:
            ig = ProcessedIg.query.filter_by(package_name=pkg_name, version=pkg_version).first()
            self.assertIsNotNone(ig)
            must_support_paths = ig.must_support_elements.get('Patient', [])
            self.assertIn('Patient.identifier:us-ssn', must_support_paths)
        response = self.client.get(f'/view-ig/{ig.id}')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Patient.identifier:us-ssn', response.data)
        self.assertIn(b'list-group-item-warning', response.data)

    # --- Existing API Tests ---

    @patch('app.list_downloaded_packages')
    @patch('app.services.process_package_file')
    @patch('app.services.import_package_and_dependencies')
    @patch('os.path.exists')
    def test_40_api_import_ig_success(self, mock_os_exists, mock_import, mock_process, mock_list_pkgs):
        pkg_name = 'api.test.pkg'
        pkg_version = '1.2.3'
        filename = f'{pkg_name}-{pkg_version}.tgz'
        pkg_path = os.path.join(app.config['FHIR_PACKAGES_DIR'], filename)
        mock_import.return_value = {'requested': (pkg_name, pkg_version), 'processed': {(pkg_name, pkg_version)}, 'downloaded': {(pkg_name, pkg_version): pkg_path}, 'all_dependencies': {}, 'dependencies': [], 'errors': []}
        mock_process.return_value = {'resource_types_info': [], 'must_support_elements': {}, 'examples': {}, 'complies_with_profiles': ['http://prof.com/a'], 'imposed_profiles': [], 'errors': []}
        mock_os_exists.return_value = True
        mock_list_pkgs.return_value = ([{'name': pkg_name, 'version': pkg_version, 'filename': filename}], [], {})
        response = self.client.post(
            '/api/import-ig',
            data=json.dumps({'package_name': pkg_name, 'version': pkg_version, 'dependency_mode': 'direct', 'api_key': 'test-api-key'}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['complies_with_profiles'], ['http://prof.com/a'])

    @patch('app.services.import_package_and_dependencies')
    def test_41_api_import_ig_failure(self, mock_import):
        mock_import.return_value = {'requested': ('bad.pkg', '1.0'), 'processed': set(), 'downloaded': {}, 'all_dependencies': {}, 'dependencies': [], 'errors': ['HTTP error: 404 Not Found']}
        response = self.client.post(
            '/api/import-ig',
            data=json.dumps({'package_name': 'bad.pkg', 'version': '1.0', 'api_key': 'test-api-key'}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 404)
        data = json.loads(response.data)
        self.assertIn('Failed to import bad.pkg#1.0: HTTP error: 404 Not Found', data['message'])

    def test_42_api_import_ig_invalid_key(self):
        response = self.client.post(
            '/api/import-ig',
            data=json.dumps({'package_name': 'a', 'version': '1', 'api_key': 'wrong'}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 401)

    def test_43_api_import_ig_missing_key(self):
        response = self.client.post(
            '/api/import-ig',
            data=json.dumps({'package_name': 'a', 'version': '1'}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 401)

    # --- API Push Tests ---

    @patch('os.path.exists', return_value=True)
    @patch('app.services.get_package_metadata')
    @patch('tarfile.open')
    @patch('requests.Session')
    def test_50_api_push_ig_success(self, mock_session, mock_tarfile_open, mock_get_metadata, mock_os_exists):
        pkg_name = 'push.test.pkg'
        pkg_version = '1.0.0'
        filename = f'{pkg_name}-{pkg_version}.tgz'
        fhir_server_url = 'http://fake-fhir.com/baseR4'
        mock_get_metadata.return_value = {'imported_dependencies': []}
        mock_tar = MagicMock()
        mock_patient = {'resourceType': 'Patient', 'id': 'pat1'}
        mock_obs = {'resourceType': 'Observation', 'id': 'obs1', 'status': 'final'}
        patient_member = MagicMock(spec=tarfile.TarInfo)
        patient_member.name = 'package/Patient-pat1.json'
        patient_member.isfile.return_value = True
        obs_member = MagicMock(spec=tarfile.TarInfo)
        obs_member.name = 'package/Observation-obs1.json'
        obs_member.isfile.return_value = True
        mock_tar.getmembers.return_value = [patient_member, obs_member]
        def mock_extractfile(member):
            if member.name == 'package/Patient-pat1.json':
                return io.BytesIO(json.dumps(mock_patient).encode('utf-8'))
            if member.name == 'package/Observation-obs1.json':
                return io.BytesIO(json.dumps(mock_obs).encode('utf-8'))
            return None
        mock_tar.extractfile.side_effect = mock_extractfile
        mock_tarfile_open.return_value.__enter__.return_value = mock_tar
        mock_session_instance = MagicMock()
        mock_put_response = MagicMock(status_code=200)
        mock_put_response.raise_for_status.return_value = None
        mock_session_instance.put.return_value = mock_put_response
        mock_session.return_value = mock_session_instance
        self.create_mock_tgz(filename, {'package/dummy.txt': 'content'})
        response = self.client.post(
            '/api/push-ig',
            data=json.dumps({
                'package_name': pkg_name,
                'version': pkg_version,
                'fhir_server_url': fhir_server_url,
                'include_dependencies': False,
                'api_key': 'test-api-key'
            }),
            content_type='application/json',
            headers={'X-API-Key': 'test-api-key', 'Accept': 'application/x-ndjson'}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'application/x-ndjson')
        streamed_data = parse_ndjson(response.data)
        complete_msg = next((item for item in streamed_data if item.get('type') == 'complete'), None)
        self.assertIsNotNone(complete_msg)
        summary = complete_msg.get('data', {})
        self.assertEqual(summary.get('status'), 'success')
        self.assertEqual(summary.get('success_count'), 2)
        self.assertEqual(len(summary.get('failed_details')), 0)
        mock_os_exists.assert_called_with(os.path.join(self.test_packages_dir, filename))

    @patch('os.path.exists', return_value=True)
    @patch('app.services.get_package_metadata')
    @patch('tarfile.open')
    @patch('requests.Session')
    def test_51_api_push_ig_with_failures(self, mock_session, mock_tarfile_open, mock_get_metadata, mock_os_exists):
        pkg_name = 'push.fail.pkg'
        pkg_version = '1.0.0'
        filename = f'{pkg_name}-{pkg_version}.tgz'
        fhir_server_url = 'http://fail-fhir.com/baseR4'
        mock_get_metadata.return_value = {'imported_dependencies': []}
        mock_tar = MagicMock()
        mock_ok_res = {'resourceType': 'Patient', 'id': 'ok1'}
        mock_fail_res = {'resourceType': 'Observation', 'id': 'fail1'}
        ok_member = MagicMock(spec=tarfile.TarInfo)
        ok_member.name = 'package/Patient-ok1.json'
        ok_member.isfile.return_value = True
        fail_member = MagicMock(spec=tarfile.TarInfo)
        fail_member.name = 'package/Observation-fail1.json'
        fail_member.isfile.return_value = True
        mock_tar.getmembers.return_value = [ok_member, fail_member]
        def mock_extractfile(member):
            if member.name == 'package/Patient-ok1.json':
                return io.BytesIO(json.dumps(mock_ok_res).encode('utf-8'))
            if member.name == 'package/Observation-fail1.json':
                return io.BytesIO(json.dumps(mock_fail_res).encode('utf-8'))
            return None
        mock_tar.extractfile.side_effect = mock_extractfile
        mock_tarfile_open.return_value.__enter__.return_value = mock_tar
        mock_session_instance = MagicMock()
        mock_ok_response = MagicMock(status_code=200)
        mock_ok_response.raise_for_status.return_value = None
        mock_fail_http_response = MagicMock(status_code=400)
        mock_fail_http_response.json.return_value = {'resourceType': 'OperationOutcome', 'issue': [{'severity': 'error', 'diagnostics': 'Validation failed'}]}
        mock_fail_exception = requests.exceptions.HTTPError(response=mock_fail_http_response)
        mock_fail_http_response.raise_for_status.side_effect = mock_fail_exception
        mock_session_instance.put.side_effect = [mock_ok_response, mock_fail_http_response]
        mock_session.return_value = mock_session_instance
        self.create_mock_tgz(filename, {'package/dummy.txt': 'content'})
        response = self.client.post(
            '/api/push-ig',
            data=json.dumps({
                'package_name': pkg_name,
                'version': pkg_version,
                'fhir_server_url': fhir_server_url,
                'include_dependencies': False,
                'api_key': 'test-api-key'
            }),
            content_type='application/json',
            headers={'X-API-Key': 'test-api-key', 'Accept': 'application/x-ndjson'}
        )
        self.assertEqual(response.status_code, 200)
        streamed_data = parse_ndjson(response.data)
        complete_msg = next((item for item in streamed_data if item.get('type') == 'complete'), None)
        self.assertIsNotNone(complete_msg)
        summary = complete_msg.get('data', {})
        self.assertEqual(summary.get('status'), 'partial')
        self.assertEqual(summary.get('success_count'), 1)
        self.assertEqual(summary.get('failure_count'), 1)
        self.assertEqual(len(summary.get('failed_details')), 1)
        self.assertEqual(summary['failed_details'][0].get('resource'), 'Observation/fail1')
        self.assertIn('Validation failed', summary['failed_details'][0].get('error', ''))
        mock_os_exists.assert_called_with(os.path.join(self.test_packages_dir, filename))

    @patch('os.path.exists', return_value=True)
    @patch('app.services.get_package_metadata')
    @patch('tarfile.open')
    @patch('requests.Session')
    def test_52_api_push_ig_with_dependency(self, mock_session, mock_tarfile_open, mock_get_metadata, mock_os_exists):
        main_pkg_name = 'main.dep.pkg'
        main_pkg_ver = '1.0'
        main_filename = f'{main_pkg_name}-{main_pkg_ver}.tgz'
        dep_pkg_name = 'dep.pkg'
        dep_pkg_ver = '1.0'
        dep_filename = f'{dep_pkg_name}-{dep_pkg_ver}.tgz'
        fhir_server_url = 'http://dep-fhir.com/baseR4'
        self.create_mock_tgz(main_filename, {'package/Patient-main.json': {'resourceType': 'Patient', 'id': 'main'}})
        self.create_mock_tgz(dep_filename, {'package/Observation-dep.json': {'resourceType': 'Observation', 'id': 'dep'}})
        mock_get_metadata.return_value = {'imported_dependencies': [{'name': dep_pkg_name, 'version': dep_pkg_ver}]}
        mock_main_tar = MagicMock()
        main_member = MagicMock(spec=tarfile.TarInfo)
        main_member.name = 'package/Patient-main.json'
        main_member.isfile.return_value = True
        mock_main_tar.getmembers.return_value = [main_member]
        mock_main_tar.extractfile.return_value = io.BytesIO(json.dumps({'resourceType': 'Patient', 'id': 'main'}).encode('utf-8'))
        mock_dep_tar = MagicMock()
        dep_member = MagicMock(spec=tarfile.TarInfo)
        dep_member.name = 'package/Observation-dep.json'
        dep_member.isfile.return_value = True
        mock_dep_tar.getmembers.return_value = [dep_member]
        mock_dep_tar.extractfile.return_value = io.BytesIO(json.dumps({'resourceType': 'Observation', 'id': 'dep'}).encode('utf-8'))
        def tar_opener(path, mode):
            mock_tar_ctx = MagicMock()
            if main_filename in path:
                mock_tar_ctx.__enter__.return_value = mock_main_tar
            elif dep_filename in path:
                mock_tar_ctx.__enter__.return_value = mock_dep_tar
            else:
                empty_mock_tar = MagicMock()
                empty_mock_tar.getmembers.return_value = []
                mock_tar_ctx.__enter__.return_value = empty_mock_tar
            return mock_tar_ctx
        mock_tarfile_open.side_effect = tar_opener
        mock_session_instance = MagicMock()
        mock_put_response = MagicMock(status_code=200)
        mock_put_response.raise_for_status.return_value = None
        mock_session_instance.put.return_value = mock_put_response
        mock_session.return_value = mock_session_instance
        response = self.client.post(
            '/api/push-ig',
            data=json.dumps({
                'package_name': main_pkg_name,
                'version': main_pkg_ver,
                'fhir_server_url': fhir_server_url,
                'include_dependencies': True,
                'api_key': 'test-api-key'
            }),
            content_type='application/json',
            headers={'X-API-Key': 'test-api-key', 'Accept': 'application/x-ndjson'}
        )
        self.assertEqual(response.status_code, 200)
        streamed_data = parse_ndjson(response.data)
        complete_msg = next((item for item in streamed_data if item.get('type') == 'complete'), None)
        self.assertIsNotNone(complete_msg)
        summary = complete_msg.get('data', {})
        self.assertEqual(summary.get('status'), 'success')
        self.assertEqual(summary.get('success_count'), 2)
        self.assertEqual(len(summary.get('pushed_packages_summary')), 2)
        mock_os_exists.assert_any_call(os.path.join(self.test_packages_dir, main_filename))
        mock_os_exists.assert_any_call(os.path.join(self.test_packages_dir, dep_filename))

    # --- Helper Route Tests ---

    @patch('app.ProcessedIg.query')
    @patch('app.services.find_and_extract_sd')
    @patch('os.path.exists')
    def test_60_get_structure_definition_success(self, mock_exists, mock_find_sd, mock_query):
        pkg_name = 'struct.test'
        pkg_version = '1.0'
        resource_type = 'Patient'
        mock_exists.return_value = True
        mock_sd_data = {'resourceType': 'StructureDefinition', 'snapshot': {'element': [{'id': 'Patient.name', 'min': 1}, {'id': 'Patient.birthDate', 'mustSupport': True}]}}
        mock_find_sd.return_value = (mock_sd_data, 'path/to/sd.json')
        mock_processed_ig = MagicMock()
        mock_processed_ig.must_support_elements = {resource_type: ['Patient.birthDate']}
        mock_query.filter_by.return_value.first.return_value = mock_processed_ig
        response = self.client.get(f'/get-structure?package_name={pkg_name}&package_version={pkg_version}&resource_type={resource_type}')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data['must_support_paths'], ['Patient.birthDate'])

    @patch('app.services.import_package_and_dependencies')
    @patch('app.services.find_and_extract_sd')
    @patch('os.path.exists')
    def test_61_get_structure_definition_fallback(self, mock_exists, mock_find_sd, mock_import):
        pkg_name = 'struct.test'
        pkg_version = '1.0'
        core_pkg_name, core_pkg_version = services.CANONICAL_PACKAGE
        resource_type = 'Observation'
        def exists_side_effect(path):
            return True
        mock_exists.side_effect = exists_side_effect
        mock_core_sd_data = {'resourceType': 'StructureDefinition', 'snapshot': {'element': [{'id': 'Observation.status'}]}}
        def find_sd_side_effect(path, identifier, profile_url=None):
            if f"{pkg_name}-{pkg_version}.tgz" in path:
                return (None, None)
            if f"{core_pkg_name}-{core_pkg_version}.tgz" in path:
                return (mock_core_sd_data, 'path/obs.json')
            return (None, None)
        mock_find_sd.side_effect = find_sd_side_effect
        with patch('app.ProcessedIg.query') as mock_query:
            mock_query.filter_by.return_value.first.return_value = None
            response = self.client.get(f'/get-structure?package_name={pkg_name}&package_version={pkg_version}&resource_type={resource_type}')
            self.assertEqual(response.status_code, 200)
            data = json.loads(response.data)
            self.assertTrue(data['fallback_used'])

    @patch('app.services.find_and_extract_sd', return_value=(None, None))
    @patch('app.services.import_package_and_dependencies')
    @patch('os.path.exists')
    def test_62_get_structure_definition_not_found_anywhere(self, mock_exists, mock_import, mock_find_sd):
        pkg_name = 'no.sd.pkg'
        pkg_version = '1.0'
        core_pkg_name, core_pkg_version = services.CANONICAL_PACKAGE
        def exists_side_effect(path):
            if f"{pkg_name}-{pkg_version}.tgz" in path:
                return True
            if f"{core_pkg_name}-{core_pkg_version}.tgz" in path:
                return False
            return False
        mock_exists.side_effect = exists_side_effect
        mock_import.return_value = {'errors': ['Download failed'], 'downloaded': False}
        response = self.client.get(f'/get-structure?package_name={pkg_name}&package_version={pkg_version}&resource_type=Whatever')
        self.assertEqual(response.status_code, 500)
        data = json.loads(response.data)
        self.assertIn('failed to download core package', data['error'])

    def test_63_get_example_content_success(self):
        pkg_name = 'example.test'
        pkg_version = '1.0'
        filename = f"{pkg_name}-{pkg_version}.tgz"
        example_path = 'package/Patient-example.json'
        example_content = {'resourceType': 'Patient', 'id': 'example'}
        self.create_mock_tgz(filename, {example_path: example_content})
        response = self.client.get(f'/get-example?package_name={pkg_name}&package_version={pkg_version}&filename={example_path}')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data, example_content)

    def test_64_get_package_metadata_success(self):
        pkg_name = 'metadata.test'
        pkg_version = '1.0'
        metadata_filename = f"{pkg_name}-{pkg_version}.metadata.json"
        metadata_content = {'package_name': pkg_name, 'version': pkg_version, 'dependency_mode': 'tree-shaking'}
        metadata_path = os.path.join(app.config['FHIR_PACKAGES_DIR'], metadata_filename)
        open(metadata_path, 'w').write(json.dumps(metadata_content))
        response = self.client.get(f'/get-package-metadata?package_name={pkg_name}&version={pkg_version}')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data.get('dependency_mode'), 'tree-shaking')

if __name__ == '__main__':
    unittest.main()
