# Base image with Python and Java
FROM tomcat:10.1-jdk17

# Install build dependencies, Node.js 18, and coreutils (for stdbuf)
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv curl coreutils \
    && curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install specific versions of GoFSH and SUSHI
# REMOVED pip install fhirpath from this line
RUN npm install -g gofsh fsh-sushi

# Set up Python environment
WORKDIR /app
RUN python3 -m venv /app/venv
ENV PATH="/app/venv/bin:$PATH"

# ADDED: Uninstall old fhirpath just in case it's in requirements.txt
RUN pip uninstall -y fhirpath || true
# ADDED: Install the new fhirpathpy library
RUN pip install --no-cache-dir fhirpathpy

# Copy Flask files
COPY requirements.txt .
# Install requirements (including Pydantic - check version compatibility if needed)
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY services.py .
COPY forms.py .
COPY templates/ templates/
COPY static/ static/
COPY tests/ tests/

# Ensure /tmp, /app/h2-data, /app/static/uploads, and /app/logs are writable
RUN mkdir -p /tmp /app/h2-data /app/static/uploads /app/logs && chmod 777 /tmp /app/h2-data /app/static/uploads /app/logs

# Copy pre-built HAPI WAR and configuration
COPY hapi-fhir-jpaserver/target/ROOT.war /usr/local/tomcat/webapps/
COPY hapi-fhir-jpaserver/target/classes/application.yaml /usr/local/tomcat/conf/
COPY hapi-fhir-jpaserver/target/classes/application.yaml /app/config/application.yaml
COPY hapi-fhir-jpaserver/target/classes/application.yaml /usr/local/tomcat/webapps/app/config/application.yaml
COPY hapi-fhir-jpaserver/custom/ /usr/local/tomcat/webapps/custom/

# Install supervisord
RUN pip install supervisor

# Configure supervisord
COPY supervisord.conf /etc/supervisord.conf

# Expose ports
EXPOSE 5000 8080

# Start supervisord
CMD ["supervisord", "-c", "/etc/supervisord.conf"]