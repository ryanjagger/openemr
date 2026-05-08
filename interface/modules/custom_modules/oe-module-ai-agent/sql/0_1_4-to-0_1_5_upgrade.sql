-- @package   OpenEMR
-- @link      https://www.open-emr.org
-- @author    Ryan Jagger <jagger@fastmail.com>
-- @copyright Copyright (c) 2026 Ryan Jagger <jagger@fastmail.com>
-- @license   GNU General Public License 3
--
-- Grant the internal AI agent OAuth client the FHIR scope it needs to
-- read the QuestionnaireResponse resource, which is how AI-extracted
-- intake form answers surface to the chat agent (see Phase 4 of the
-- native+FHIR ingestion refactor and the get_questionnaire_responses
-- chat tool).
--
-- Idempotent — safe to run multiple times.

UPDATE `oauth_clients`
SET `scope` = CONCAT(`scope`, ' user/QuestionnaireResponse.read')
WHERE `client_id` = 'oe-module-ai-agent-internal'
  AND `scope` NOT LIKE '%user/QuestionnaireResponse.read%';
