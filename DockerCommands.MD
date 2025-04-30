Docker Commands.MD


<HAPI-server.>
to pull and clone:
git clone https://github.com/hapifhir/hapi-fhir-jpaserver-starter.git hapi-fhir-jpaserver
 
to build:
mvn clean package -DskipTests=true -Pboot
 
to run:
java -jar target/ROOT.war


<rest-of-the-app:>

docker-compose build --no-cache
docker-compose  up -d



<useful-stuff:>

cp <CONTAINERID>:/app/PATH/Filename.ext .     - . copies to the root folder you ran it from

docker exec -it <CONTAINERID> bash - to get a bash - session in the container  - 