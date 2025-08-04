Instance: discharge-1
InstanceOf: Encounter
Usage: #example
* meta.profile = "http://hl7.org.au/fhir/core/StructureDefinition/au-core-encounter"
* status = #finished
* class = $v3-ActCode#EMER "emergency"
* subject = Reference(Patient/ronny-irvine)
* period
  * start = "2023-02-20T06:15:00+10:00"
  * end = "2023-02-20T18:19:00+10:00"
* location.location = Reference(Location/murrabit-hospital)
* serviceProvider = Reference(Organization/murrabit-hospital)