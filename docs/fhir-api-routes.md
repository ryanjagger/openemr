# OpenEMR FHIR API Routes

FHIR routes are defined in `apis/routes/_rest_routes_fhir_r4_us_core_3_1_0.inc.php`.

External requests include the site segment:

```text
/apis/{site}/fhir
```

Most local development examples use:

```text
/apis/default/fhir
```

So an internal route such as `GET /fhir/Patient` is called externally as:

```text
GET /apis/default/fhir/Patient
```

## Resource Routes

| Resource | Routes |
| --- | --- |
| AllergyIntolerance | `GET /fhir/AllergyIntolerance`<br>`GET /fhir/AllergyIntolerance/:uuid` |
| Appointment | `GET /fhir/Appointment`<br>`GET /fhir/Appointment/:uuid` |
| Binary | `GET /fhir/Binary/:id` |
| CarePlan | `GET /fhir/CarePlan`<br>`GET /fhir/CarePlan/:uuid` |
| CareTeam | `GET /fhir/CareTeam`<br>`GET /fhir/CareTeam/:uuid` |
| Condition | `GET /fhir/Condition`<br>`GET /fhir/Condition/:uuid` |
| Coverage | `GET /fhir/Coverage`<br>`GET /fhir/Coverage/:uuid` |
| Device | `GET /fhir/Device`<br>`GET /fhir/Device/:uuid` |
| DiagnosticReport | `GET /fhir/DiagnosticReport`<br>`GET /fhir/DiagnosticReport/:uuid` |
| DocumentReference | `GET /fhir/DocumentReference`<br>`GET /fhir/DocumentReference/:uuid`<br>`POST /fhir/DocumentReference/$docref` |
| Encounter | `GET /fhir/Encounter`<br>`GET /fhir/Encounter/:uuid` |
| Goal | `GET /fhir/Goal`<br>`GET /fhir/Goal/:uuid` |
| Group | `GET /fhir/Group`<br>`GET /fhir/Group/:uuid`<br>`GET /fhir/Group/:id/$export` |
| Immunization | `GET /fhir/Immunization`<br>`GET /fhir/Immunization/:uuid` |
| Location | `GET /fhir/Location`<br>`GET /fhir/Location/:uuid` |
| Media | `GET /fhir/Media`<br>`GET /fhir/Media/:uuid` |
| Medication | `GET /fhir/Medication`<br>`GET /fhir/Medication/:uuid` |
| MedicationDispense | `GET /fhir/MedicationDispense`<br>`GET /fhir/MedicationDispense/:uuid` |
| MedicationRequest | `GET /fhir/MedicationRequest`<br>`GET /fhir/MedicationRequest/:uuid` |
| Observation | `GET /fhir/Observation`<br>`GET /fhir/Observation/:uuid` |
| Organization | `GET /fhir/Organization`<br>`GET /fhir/Organization/:uuid`<br>`POST /fhir/Organization`<br>`PUT /fhir/Organization/:uuid` |
| Patient | `GET /fhir/Patient`<br>`GET /fhir/Patient/:uuid`<br>`POST /fhir/Patient`<br>`PUT /fhir/Patient/:uuid`<br>`GET /fhir/Patient/$export` |
| Person | `GET /fhir/Person`<br>`GET /fhir/Person/:uuid` |
| Practitioner | `GET /fhir/Practitioner`<br>`GET /fhir/Practitioner/:uuid`<br>`POST /fhir/Practitioner`<br>`PUT /fhir/Practitioner/:uuid` |
| PractitionerRole | `GET /fhir/PractitionerRole`<br>`GET /fhir/PractitionerRole/:uuid` |
| Procedure | `GET /fhir/Procedure`<br>`GET /fhir/Procedure/:uuid` |
| Provenance | `GET /fhir/Provenance`<br>`GET /fhir/Provenance/:uuid` |
| Questionnaire | `GET /fhir/Questionnaire` |
| QuestionnaireResponse | `GET /fhir/QuestionnaireResponse`<br>`GET /fhir/QuestionnaireResponse/:uuid` |
| RelatedPerson | `GET /fhir/RelatedPerson`<br>`GET /fhir/RelatedPerson/:uuid` |
| ServiceRequest | `GET /fhir/ServiceRequest`<br>`GET /fhir/ServiceRequest/:uuid` |
| Specimen | `GET /fhir/Specimen`<br>`GET /fhir/Specimen/:uuid` |
| ValueSet | `GET /fhir/ValueSet`<br>`GET /fhir/ValueSet/:uuid` |

## Metadata, SMART, and Operations

| Purpose | Routes |
| --- | --- |
| Capability statement | `GET /fhir/metadata` |
| SMART configuration | `GET /fhir/.well-known/smart-configuration` |
| Operation definitions | `GET /fhir/OperationDefinition`<br>`GET /fhir/OperationDefinition/:operation` |
| Bulk export | `GET /fhir/$export`<br>`GET /fhir/Patient/$export`<br>`GET /fhir/Group/:id/$export` |
| Bulk export status | `GET /fhir/$bulkdata-status`<br>`DELETE /fhir/$bulkdata-status` |

## Search Notes

Collection `GET` routes are FHIR search routes. For example:

```text
GET /apis/default/fhir/Patient?family=Smith
GET /apis/default/fhir/Observation?patient={patientUuid}&category=vital-signs
```

`POST /fhir/{Resource}/_search` is supported implicitly for resources with a matching `GET /fhir/{Resource}` search route. OpenEMR normalizes the POST search request into the equivalent GET search internally.
