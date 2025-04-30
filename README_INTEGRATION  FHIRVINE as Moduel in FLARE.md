# Integrating FHIRVINE as a Module in FHIRFLARE

## Overview

FHIRFLARE is a Flask-based FHIR Implementation Guide (IG) toolkit for managing and validating FHIR packages. This guide explains how to integrate FHIRVINE—a SMART on FHIR proxy—as a module within FHIRFLARE, enabling OAuth2 authentication and FHIR request proxying directly in the application. This modular approach embeds FHIRVINE’s functionality into FHIRFLARE, avoiding the need for a separate proxy service.

## Prerequisites

- FHIRFLARE repository cloned: `https://github.com/Sudo-JHare/FHIRFLARE-IG-Toolkit`.
- FHIRVINE repository cloned: `<fhirvine-repository-url>`.
- Python 3.11 and dependencies installed (`requirements.txt` from both projects).
- A FHIR server (e.g., `http://hapi.fhir.org/baseR4`).

## Integration Steps

### 1. Prepare FHIRFLARE Structure

Ensure FHIRFLARE’s file structure supports modular integration. It should look like:

```
FHIRFLARE-IG-Toolkit/
├── app.py
├── services.py
├── templates/
├── static/
└── requirements.txt
```

### 2. Copy FHIRVINE Files into FHIRFLARE

FHIRVINE’s core functionality (OAuth2 proxy, app registration) will be integrated as a Flask Blueprint.

- **Copy Files**:

  - Copy `smart_proxy.py`, `forms.py`, `models.py`, and `app.py` (relevant parts) from FHIRVINE into a new `fhirvine/` directory in FHIRFLARE:

    ```
    FHIRFLARE-IG-Toolkit/
    ├── fhirvine/
    │   ├── smart_proxy.py
    │   ├── forms.py
    │   ├── models.py
    │   └── __init__.py
    ```

  - Copy FHIRVINE’s templates (e.g., `app_gallery/`, `configure/`, `test_client.html`) into `FHIRFLARE-IG-Toolkit/templates/` while maintaining their folder structure.

- **Add Dependencies**:

  - Add FHIRVINE’s dependencies to `requirements.txt` (e.g., `authlib`, `flasgger`, `flask-sqlalchemy`).

### 3. Modify FHIRVINE Code as a Module

- **Create Blueprint in** `fhirvine/__init__.py`:

  ```python
  from flask import Blueprint
  
  fhirvine_bp = Blueprint('fhirvine', __name__, template_folder='templates')
  
  from .smart_proxy import *
  ```

  This registers FHIRVINE as a Flask Blueprint.

- **Update** `smart_proxy.py`:

  - Replace direct `app.route` decorators with `fhirvine_bp.route`. For example:

    ```python
    @fhirvine_bp.route('/authorize', methods=['GET', 'POST'])
    def authorize():
        # Existing authorization logic
    ```

### 4. Integrate FHIRVINE Blueprint into FHIRFLARE

- **Update** `app.py` **in FHIRFLARE**:

  - Import and register the FHIRVINE Blueprint:

    ```python
    from fhirvine import fhirvine_bp
    from fhirvine.models import database, RegisteredApp, OAuthToken, AuthorizationCode, Configuration
    from fhirvine.smart_proxy import configure_oauth
    
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY='your-secure-random-key',
        SQLALCHEMY_DATABASE_URI='sqlite:////app/instance/fhirflare.db',
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        FHIR_SERVER_URL='http://hapi.fhir.org/baseR4',
        PROXY_TIMEOUT=10,
        TOKEN_DURATION=3600,
        REFRESH_TOKEN_DURATION=86400,
        ALLOWED_SCOPES='openid profile launch launch/patient patient/*.read offline_access'
    )
    
    database.init_app(app)
    configure_oauth(app, db=database, registered_app_model=RegisteredApp, oauth_token_model=OAuthToken, auth_code_model=AuthorizationCode)
    
    app.register_blueprint(fhirvine_bp, url_prefix='/fhirvine')
    ```

### 5. Update FHIRFLARE Templates

- **Add FHIRVINE Links to Navbar**:

  - In `templates/base.html`, add links to FHIRVINE features:

    ```html
    <li class="nav-item">
        <a class="nav-link" href="{{ url_for('fhirvine.app_gallery') }}">App Gallery</a>
    </li>
    <li class="nav-item">
        <a class="nav-link" href="{{ url_for('fhirvine.test_client') }}">Test Client</a>
    </li>
    ```

### 6. Run and Test

- **Install Dependencies**:

  ```bash
  pip install -r requirements.txt
  ```

- **Run FHIRFLARE**:

  ```bash
  flask db upgrade
  flask run --host=0.0.0.0 --port=8080
  ```

- **Access FHIRVINE Features**:

  - App Gallery: `http://localhost:8080/fhirvine/app-gallery`
  - Test Client: `http://localhost:8080/fhirvine/test-client`
  - Proxy Requests: Use `/fhirvine/oauth2/proxy/<path>` within FHIRFLARE.

## Using FHIRVINE in FHIRFLARE

- **Register Apps**: Use `/fhirvine/app-gallery` to register SMART apps within FHIRFLARE.
- **Authenticate**: Use `/fhirvine/oauth2/authorize` for OAuth2 flows.
- **Proxy FHIR Requests**: FHIRFLARE can now make FHIR requests via `/fhirvine/oauth2/proxy`, leveraging FHIRVINE’s authentication.

## Troubleshooting

- **Route Conflicts**: Ensure no overlapping routes between FHIRFLARE and FHIRVINE.
- **Database Issues**: Verify `SQLALCHEMY_DATABASE_URI` points to the same database.
- **Logs**: Check `flask run` logs for errors.