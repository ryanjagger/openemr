-- @package   OpenEMR
-- @link      https://www.open-emr.org
-- @author    Ryan Jagger <jagger@fastmail.com>
-- @copyright Copyright (c) 2026 Ryan Jagger <jagger@fastmail.com>
-- @license   GNU General Public License 3
--
-- Phase 5 of the native+FHIR ingestion refactor: drop the shadow
-- ai_document_facts and ai_document_source_snippets tables. AI-extracted
-- facts now live in procedure_result (labs, see AiLabIngestionService) and
-- questionnaire_response (intake, see AiIntakeIngestionService); provenance
-- is tracked in ai_result_provenance and
-- ai_questionnaire_response_provenance respectively.
--
-- Idempotent — safe to run multiple times.

DROP TABLE IF EXISTS `ai_document_source_snippets`;
DROP TABLE IF EXISTS `ai_document_facts`;
