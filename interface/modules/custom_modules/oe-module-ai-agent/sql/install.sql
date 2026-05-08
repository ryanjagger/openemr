-- @package   OpenEMR
-- @link      https://www.open-emr.org
-- @author    Ryan Jagger <jagger@fastmail.com>
-- @copyright Copyright (c) 2026 Ryan Jagger <jagger@fastmail.com>
-- @license   GNU General Public License 3
--
-- Module install script for oe-module-ai-agent.
-- Idempotent — safe to run multiple times.

-- ---------------------------------------------------------------------
-- Internal OAuth2 client used by BearerTokenMinter to issue user-scoped
-- short-lived access tokens that the Python sidecar uses to call the
-- OpenEMR FHIR API on the user's behalf. Public client (no secret) —
-- the trust boundary is enforced by INTERNAL_AUTH_SECRET on the sidecar
-- and by the user's session having already authenticated.
-- ---------------------------------------------------------------------
INSERT INTO `oauth_clients` (
    `client_id`,
    `client_role`,
    `client_name`,
    `client_secret`,
    `redirect_uri`,
    `grant_types`,
    `scope`,
    `is_confidential`,
    `is_enabled`,
    `register_date`
) VALUES (
    'oe-module-ai-agent-internal',
    'users',
    'OpenEMR AI Agent (Internal)',
    NULL,
    '',
    '',
    -- The /api/ai/documents/* sub-routes have OpenEMR's URL-derived scope
    -- inference fall back to the path's last non-parameter segment as the
    -- resource name (HttpRestParsedRoute::parseRouteParams). That makes
    -- /recent/:pid require user/recent.read, /ingest/:pid require
    -- user/ingest.write, /jobs/:pid/:jobId require user/jobs.read, and
    -- /auto-ingest/.../document require user/document.write. These are
    -- internal scope identifiers for our private OAuth client, not real
    -- OpenEMR resources, so it is safe to grant them here.
    'openid api:fhir api:oemr user/Patient.read user/Appointment.read user/CarePlan.read user/Condition.read user/MedicationRequest.read user/AllergyIntolerance.read user/Encounter.read user/Goal.read user/Observation.read user/DocumentReference.read user/ServiceRequest.read user/Procedure.read user/Immunization.read user/QuestionnaireResponse.read user/document.read user/document.write user/recent.read user/ingest.write user/jobs.read',
    0,
    1,
    NOW()
)
ON DUPLICATE KEY UPDATE
    `is_enabled` = 1,
    `scope` = VALUES(`scope`);

-- ---------------------------------------------------------------------
-- llm_call_log — supplementary audit trail for LLM-mediated reads.
-- One row per agent invocation. Hashes only in MVP (no raw prompts or
-- responses). integrity_checksum is HMAC-SHA256 over the canonical row
-- and detects single-row tampering by anyone without the key.
-- prev_log_hash is reserved for hash-chaining in a follow-up (ARCH §8.3).
-- request_id joins back to OpenEMR's existing api_log via the
-- X-Request-Id header that the sidecar attaches to FHIR calls.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `llm_call_log` (
    `id`                       BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `request_id`               CHAR(36)        NOT NULL,
    `conversation_id`          VARCHAR(36)     NULL,
    `user_id`                  BIGINT UNSIGNED NOT NULL,
    `patient_id`               BIGINT UNSIGNED NOT NULL,
    `action_type`              VARCHAR(32)     NOT NULL,
    `model_id`                 VARCHAR(128)    NOT NULL,
    `prompt_tokens`            INT             NOT NULL DEFAULT 0,
    `completion_tokens`        INT             NOT NULL DEFAULT 0,
    `latency_ms`               INT UNSIGNED    NULL,
    `cost_usd_micros`          BIGINT UNSIGNED NULL,
    `request_hash`             CHAR(64)        NOT NULL,
    `response_hash`            CHAR(64)        NOT NULL,
    `tool_calls`               JSON            NULL,
    `steps_json`               LONGTEXT        NULL,
    `verification_status`      ENUM('passed', 'partial', 'failed', 'denied') NOT NULL,
    `verification_failures`    JSON            NULL,
    `error_code`               VARCHAR(64)     NULL,
    `error_detail`             TEXT            NULL,
    `integrity_checksum`       CHAR(64)        NOT NULL,
    `prev_log_hash`            CHAR(64)        NULL,
    `created_at`               TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    INDEX `idx_patient_user_time` (`patient_id`, `user_id`, `created_at`),
    INDEX `idx_request_id` (`request_id`),
    INDEX `idx_conversation_id` (`conversation_id`),
    INDEX `idx_action_created` (`action_type`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Idempotent column add for installs that pre-date the chat surface.
SET @col_exists := (
    SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
    WHERE `TABLE_SCHEMA` = DATABASE()
      AND `TABLE_NAME` = 'llm_call_log'
      AND `COLUMN_NAME` = 'conversation_id'
);
SET @sql := IF(
    @col_exists = 0,
    'ALTER TABLE `llm_call_log` ADD COLUMN `conversation_id` VARCHAR(36) NULL AFTER `request_id`, ADD INDEX `idx_conversation_id` (`conversation_id`)',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Idempotent column add for the observability fields (latency / cost / steps / error).
-- Each ALTER guards on COLUMN existence so re-running this script is safe.
SET @col_exists := (
    SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
    WHERE `TABLE_SCHEMA` = DATABASE()
      AND `TABLE_NAME` = 'llm_call_log'
      AND `COLUMN_NAME` = 'latency_ms'
);
SET @sql := IF(
    @col_exists = 0,
    'ALTER TABLE `llm_call_log` ADD COLUMN `latency_ms` INT UNSIGNED NULL AFTER `completion_tokens`',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @col_exists := (
    SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
    WHERE `TABLE_SCHEMA` = DATABASE()
      AND `TABLE_NAME` = 'llm_call_log'
      AND `COLUMN_NAME` = 'cost_usd_micros'
);
SET @sql := IF(
    @col_exists = 0,
    'ALTER TABLE `llm_call_log` ADD COLUMN `cost_usd_micros` BIGINT UNSIGNED NULL AFTER `latency_ms`',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @col_exists := (
    SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
    WHERE `TABLE_SCHEMA` = DATABASE()
      AND `TABLE_NAME` = 'llm_call_log'
      AND `COLUMN_NAME` = 'steps_json'
);
SET @sql := IF(
    @col_exists = 0,
    'ALTER TABLE `llm_call_log` ADD COLUMN `steps_json` LONGTEXT NULL AFTER `tool_calls`',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @col_exists := (
    SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
    WHERE `TABLE_SCHEMA` = DATABASE()
      AND `TABLE_NAME` = 'llm_call_log'
      AND `COLUMN_NAME` = 'error_code'
);
SET @sql := IF(
    @col_exists = 0,
    'ALTER TABLE `llm_call_log` ADD COLUMN `error_code` VARCHAR(64) NULL AFTER `verification_failures`, ADD COLUMN `error_detail` TEXT NULL AFTER `error_code`',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @idx_exists := (
    SELECT COUNT(*) FROM `information_schema`.`STATISTICS`
    WHERE `TABLE_SCHEMA` = DATABASE()
      AND `TABLE_NAME` = 'llm_call_log'
      AND `INDEX_NAME` = 'idx_action_created'
);
SET @sql := IF(
    @idx_exists = 0,
    'ALTER TABLE `llm_call_log` ADD INDEX `idx_action_created` (`action_type`, `created_at`)',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- ---------------------------------------------------------------------
-- Document ingestion for co-pilot chat. These tables intentionally store
-- extracted facts and source snippets only; the original uploaded binary
-- remains in the OpenEMR documents store.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `ai_document_ingestion_jobs` (
    `id`               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `job_uuid`         CHAR(36)        NOT NULL,
    `patient_id`       BIGINT UNSIGNED NOT NULL,
    `user_id`          BIGINT UNSIGNED NOT NULL,
    `status`           ENUM('pending', 'processing', 'completed', 'partial', 'failed') NOT NULL DEFAULT 'pending',
    `document_count`   INT UNSIGNED    NOT NULL DEFAULT 0,
    `processed_count`  INT UNSIGNED    NOT NULL DEFAULT 0,
    `failed_count`     INT UNSIGNED    NOT NULL DEFAULT 0,
    `error_detail`     TEXT            NULL,
    `created_at`       TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`       TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    `completed_at`     TIMESTAMP       NULL,
    PRIMARY KEY (`id`),
    UNIQUE KEY `idx_ai_doc_ingestion_job_uuid` (`job_uuid`),
    INDEX `idx_ai_doc_ingestion_patient_created` (`patient_id`, `created_at`),
    INDEX `idx_ai_doc_ingestion_status_created` (`status`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `ai_document_ingestion_documents` (
    `id`             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `job_id`         BIGINT UNSIGNED NOT NULL,
    `patient_id`     BIGINT UNSIGNED NOT NULL,
    `document_id`    BIGINT UNSIGNED NOT NULL,
    `document_uuid`  CHAR(36)        NOT NULL,
    `document_type`  ENUM('lab_report', 'intake_form') NOT NULL,
    `status`         ENUM('pending', 'processing', 'completed', 'failed') NOT NULL DEFAULT 'pending',
    `filename`       VARCHAR(255)    NOT NULL,
    `mimetype`       VARCHAR(128)    NOT NULL,
    `docdate`        DATE            NULL,
    `model_id`       VARCHAR(128)    NULL,
    `error_detail`   TEXT            NULL,
    `created_at`     TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`     TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `idx_ai_doc_ingestion_job_doc` (`job_id`, `document_id`),
    INDEX `idx_ai_doc_ingestion_doc_uuid` (`document_uuid`),
    INDEX `idx_ai_doc_ingestion_doc_patient_status` (`patient_id`, `status`, `updated_at`),
    CONSTRAINT `fk_ai_doc_ingestion_documents_job`
        FOREIGN KEY (`job_id`) REFERENCES `ai_document_ingestion_jobs` (`id`)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO `background_services` (
    `name`,
    `title`,
    `active`,
    `running`,
    `next_run`,
    `execute_interval`,
    `function`,
    `require_once`,
    `sort_order`
) VALUES (
    'AI_Document_Ingestion_Task',
    'AI Document Ingestion',
    1,
    0,
    NOW(),
    1,
    'doAiDocumentIngestionTask',
    '/interface/modules/custom_modules/oe-module-ai-agent/library/run_document_ingestion.php',
    100
)
ON DUPLICATE KEY UPDATE
    `title` = VALUES(`title`),
    `function` = VALUES(`function`),
    `require_once` = VALUES(`require_once`),
    `execute_interval` = VALUES(`execute_interval`);

-- ---------------------------------------------------------------------
-- Native+FHIR ingestion targets (Phase 0 of the refactor that moves
-- AI-extracted facts out of ai_document_facts and into procedure_result /
-- questionnaire_response). The seeds and side tables here are used by
-- AiLabIngestionService and AiIntakeIngestionService once Phase 1/3 land.
-- ---------------------------------------------------------------------
INSERT INTO `list_options` (
    `list_id`, `option_id`, `title`, `seq`, `is_default`, `activity`
) VALUES (
    'proc_res_status', 'ai_extracted', 'AI Extracted', 70, 0, 1
)
ON DUPLICATE KEY UPDATE
    `title`    = VALUES(`title`),
    `seq`      = VALUES(`seq`),
    `activity` = VALUES(`activity`);

INSERT INTO `procedure_providers` (
    `name`, `send_app_id`, `recv_app_id`, `direction`, `protocol`, `active`, `notes`
)
SELECT
    'AI Document Extraction',
    'OE-AI-INGEST',
    'OPENEMR',
    'R',
    'DL',
    1,
    'Synthetic provider used by oe-module-ai-agent to ingest AI-extracted lab results via the HL7 receive path. Do not edit.'
WHERE NOT EXISTS (
    SELECT 1 FROM `procedure_providers` WHERE `send_app_id` = 'OE-AI-INGEST'
);

CREATE TABLE IF NOT EXISTS `ai_result_provenance` (
    `id`                    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `procedure_result_id`   BIGINT UNSIGNED NOT NULL,
    `document_id`           BIGINT UNSIGNED NOT NULL,
    `extraction_job_id`     BIGINT UNSIGNED NOT NULL,
    `page_number`           INT UNSIGNED    NULL,
    `bbox_json`             JSON            NULL,
    `snippet_text`          TEXT            NULL,
    `extraction_confidence` DECIMAL(4,3)    NULL,
    `extraction_model`      VARCHAR(128)    NULL,
    `extracted_at`          TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `idx_ai_result_prov_result` (`procedure_result_id`),
    INDEX `idx_ai_result_prov_document` (`document_id`),
    INDEX `idx_ai_result_prov_job` (`extraction_job_id`),
    CONSTRAINT `fk_ai_result_prov_job`
        FOREIGN KEY (`extraction_job_id`) REFERENCES `ai_document_ingestion_jobs` (`id`)
        ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `ai_questionnaire_response_provenance` (
    `id`                          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `questionnaire_response_id`   BIGINT UNSIGNED NOT NULL,
    `link_id`                     VARCHAR(255)    NOT NULL,
    `document_id`                 BIGINT UNSIGNED NOT NULL,
    `extraction_job_id`           BIGINT UNSIGNED NOT NULL,
    `page_number`                 INT UNSIGNED    NULL,
    `bbox_json`                   JSON            NULL,
    `snippet_text`                TEXT            NULL,
    `extraction_confidence`       DECIMAL(4,3)    NULL,
    `extraction_model`            VARCHAR(128)    NULL,
    `extracted_at`                TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `idx_ai_qr_prov_resp_link` (`questionnaire_response_id`, `link_id`),
    INDEX `idx_ai_qr_prov_document` (`document_id`),
    INDEX `idx_ai_qr_prov_job` (`extraction_job_id`),
    CONSTRAINT `fk_ai_qr_prov_job`
        FOREIGN KEY (`extraction_job_id`) REFERENCES `ai_document_ingestion_jobs` (`id`)
        ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
