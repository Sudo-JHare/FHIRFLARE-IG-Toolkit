Instance: vkc
InstanceOf: Condition
Usage: #example
* meta.profile = "http://hl7.org.au/fhir/core/StructureDefinition/au-core-condition"
* clinicalStatus = $condition-clinical#active "Active"
* category = $condition-category#encounter-diagnosis "Encounter Diagnosis"
* severity = $sct#24484000 "Severe"
* code = $sct#317349009 "Vernal keratoconjunctivitis"
* bodySite = $sct#368601006 "Entire conjunctiva of left eye"
* subject = Reference(Patient/italia-sofia)
* onsetDateTime = "2023-10-01"
* recordedDate = "2023-10-02"
* recorder = Reference(PractitionerRole/generalpractitioner-guthridge-jarred)
* asserter = Reference(PractitionerRole/generalpractitioner-guthridge-jarred)
* note.text = "Itchy and burning eye, foreign body sensation. Mucoid discharge."