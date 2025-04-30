Instance: banks-mia-leanne
InstanceOf: Patient
Usage: #example
* meta.profile = "http://hl7.org.au/fhir/core/StructureDefinition/au-core-patient"
* extension[0].url = "http://hl7.org/fhir/StructureDefinition/individual-genderIdentity"
* extension[=].extension.url = "value"
* extension[=].extension.valueCodeableConcept = $sct#446141000124107 "Identifies as female gender"
* extension[+].url = "http://hl7.org/fhir/StructureDefinition/individual-pronouns"
* extension[=].extension.url = "value"
* extension[=].extension.valueCodeableConcept = $loinc#LA29519-8 "she/her/her/hers/herself"
* extension[+].url = "http://hl7.org/fhir/StructureDefinition/individual-recordedSexOrGender"
* extension[=].extension[0].url = "value"
* extension[=].extension[=].valueCodeableConcept = $sct#248152002
* extension[=].extension[=].valueCodeableConcept.text = "Female"
* extension[=].extension[+].url = "type"
* extension[=].extension[=].valueCodeableConcept = $sct#1515311000168102 "Biological sex at birth"
* identifier.extension[0].url = "http://hl7.org.au/fhir/StructureDefinition/ihi-status"
* identifier.extension[=].valueCoding = $ihi-status-1#active
* identifier.extension[+].url = "http://hl7.org.au/fhir/StructureDefinition/ihi-record-status"
* identifier.extension[=].valueCoding = $ihi-record-status-1#verified "verified"
* identifier.type = $v2-0203#NI
* identifier.type.text = "IHI"
* identifier.system = "http://ns.electronichealth.net.au/id/hi/ihi/1.0"
* identifier.value = "8003608333647261"
* name.use = #usual
* name.family = "Banks"
* name.given[0] = "Mia"
* name.given[+] = "Leanne"
* telecom[0].system = #phone
* telecom[=].value = "0270102724"
* telecom[=].use = #work
* telecom[+].system = #phone
* telecom[=].value = "0491574632"
* telecom[=].use = #mobile
* telecom[+].system = #phone
* telecom[=].value = "0270107520"
* telecom[=].use = #home
* telecom[+].system = #email
* telecom[=].value = "mia.banks@myownpersonaldomain.com"
* telecom[+].system = #phone
* telecom[=].value = "270107520"
* telecom[=].use = #home
* gender = #female
* birthDate = "1983-08-25"
* address.line = "50 Sebastien St"
* address.city = "Minjary"
* address.state = "NSW"
* address.postalCode = "2720"
* address.country = "AU"