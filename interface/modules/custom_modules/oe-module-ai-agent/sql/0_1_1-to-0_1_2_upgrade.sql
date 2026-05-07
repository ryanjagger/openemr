-- ---------------------------------------------------------------------
-- Grant the AI-agent OAuth client the additional scopes its sub-routes
-- need under OpenEMR's URL-derived scope inference. The Python sidecar
-- now calls /api/ai/documents/recent/:pid (extractor: list unindexed),
-- /api/ai/documents/ingest/:pid (extractor: kick off job), and
-- /api/ai/documents/jobs/:pid/:jobId (extractor: poll). The dispatcher
-- derives the required scope from the path's last non-parameter segment
-- via HttpRestParsedRoute::parseRouteParams, which yields:
--   /recent/:pid          -> user/recent.read
--   /ingest/:pid          -> user/ingest.write
--   /jobs/:pid/:jobId     -> user/jobs.read
-- These are internal scope identifiers for our private OAuth client, not
-- real OpenEMR resources, so granting them here is safe and isolated.
-- ---------------------------------------------------------------------
UPDATE `oauth_clients`
SET `scope` = 'openid api:fhir api:oemr user/Patient.read user/Appointment.read user/CarePlan.read user/Condition.read user/MedicationRequest.read user/AllergyIntolerance.read user/Encounter.read user/Goal.read user/Observation.read user/DocumentReference.read user/ServiceRequest.read user/Procedure.read user/Immunization.read user/document.read user/document.write user/recent.read user/ingest.write user/jobs.read'
WHERE `client_id` = 'oe-module-ai-agent-internal';
