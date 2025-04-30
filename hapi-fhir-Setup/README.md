# Application Build and Run Guide - MANUAL STEPS

This guide outlines the steps to set up, build, and run the application, including the HAPI FHIR server component and the rest of the application managed via Docker Compose.

## Prerequisites

Before you begin, ensure you have the following installed on your system:

* [Git](https://git-scm.com/)
* [Maven](https://maven.apache.org/)
* [Java Development Kit (JDK)](https://www.oracle.com/java/technologies/downloads/) (Ensure compatibility with the HAPI FHIR version)
* [Docker](https://www.docker.com/products/docker-desktop/)
* [Docker Compose](https://docs.docker.com/compose/install/) (Often included with Docker Desktop)

## Setup and Build

Follow these steps to clone the necessary repository and build the components.

### 1. Clone and Build the HAPI FHIR Server

First, clone the HAPI FHIR JPA Server Starter project and build the server application.


# Step 1: Clone the repository
git clone https://github.com/hapifhir/hapi-fhir-jpaserver-starter.git hapi-fhir-jpaserver hapi-fhir-jpaserver

# Navigate into the cloned directory
cd hapi-fhir-jpaserver

copy the folder from hapi-fhir-setup/target/classes/application.yaml to the hapi-fhir-jpaserver/target/classes/application.yaml folder created above

# Step 2: Build the HAPI server package (skipping tests, using 'boot' profile)
# This creates the runnable WAR file in the 'target/' directory
mvn clean package -DskipTests=true -Pboot

# Return to the parent directory (or your project root)
cd ..
2. Build the Rest of the Application (Docker)
Next, build the Docker images for the remaining parts of the application as defined in your docker-compose.yml file. Run this command from the root directory where your docker-compose.yml file is located.



# Step 3: Build Docker images without using cache
docker-compose build --no-cache
Running the Application
Option A: Running the Full Application (Recommended)
Use Docker Compose to start all services, including (presumably) the HAPI FHIR server if it's configured in your docker-compose.yml. Run this from the root directory containing your docker-compose.yml.



# Step 4: Start all services defined in docker-compose.yml in detached mode
docker-compose up -d
Option B: Running the HAPI FHIR Server Standalone (Debugging Only)
This method runs only the HAPI FHIR server directly using the built WAR file. Use this primarily for debugging the server in isolation.



# Navigate into the HAPI server directory where you built it
cd hapi-fhir-jpaserver

# Run the WAR file directly using Java
java -jar target/ROOT.war

# Note: You might need to configure ports or database connections
# separately when running this way, depending on the application's needs.

# Remember to navigate back when done
# cd ..
Useful Docker Commands
Here are some helpful commands for interacting with your running Docker containers:

Copying files from a container:
To copy a file from a running container to your local machine's current directory:



# Syntax: docker cp <CONTAINER_ID_OR_NAME>:<PATH_IN_CONTAINER> <LOCAL_DESTINATION_PATH>
docker cp <CONTAINER_ID>:/app/PATH/Filename.ext .
(Replace <CONTAINER_ID>, /app/PATH/Filename.ext with actual values. . refers to the current directory on your host machine.)

Accessing a container's shell:
To get an interactive bash shell inside a running container:



# Syntax: docker exec -it <CONTAINER_ID_OR_NAME> bash
docker exec -it <CONTAINER_ID> bash
(Replace <CONTAINER_ID> with the actual container ID or name. You can find this using docker ps.)

Viewing running containers:



docker ps
Viewing application logs:



# Follow logs for all services
docker-compose logs -f

# Follow logs for a specific service
docker-compose logs -f <SERVICE_NAME>
(Replace <SERVICE_NAME> with the name defined in your docker-compose.yml)

Stopping the application:
To stop the services started with docker-compose up -d:



docker-compose down
