Instance: aspirin
InstanceOf: AllergyIntolerance
Usage: #example
* meta.profile = "http://hl7.org.au/fhir/core/StructureDefinition/au-core-allergyintolerance"
* clinicalStatus = $allergyintolerance-clinical#active
* clinicalStatus.text = "Active"
* verificationStatus = $allergyintolerance-verification#confirmed
* verificationStatus.text = "Confirmed"
* category = #medication
* criticality = #unable-to-assess
* code = $sct#387458008
* code.text = "Aspirin allergy"
* patient = Reference(Patient/hayes-arianne)
* recordedDate = "2024-02-10"
* recorder = Reference(PractitionerRole/specialistphysicians-swanborough-erick)
* asserter = Reference(PractitionerRole/specialistphysicians-swanborough-erick)