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
    'openid api:fhir api:oemr user/Patient.read user/Appointment.read user/CarePlan.read user/Condition.read user/MedicationRequest.read user/AllergyIntolerance.read user/Encounter.read user/Goal.read user/Observation.read user/DocumentReference.read user/ServiceRequest.read user/Procedure.read user/Immunization.read user/document.read',
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

CREATE TABLE IF NOT EXISTS `ai_document_facts` (
    `id`             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `patient_id`     BIGINT UNSIGNED NOT NULL,
    `document_id`    BIGINT UNSIGNED NOT NULL,
    `document_uuid`  CHAR(36)        NOT NULL,
    `document_type`  ENUM('lab_report', 'intake_form') NOT NULL,
    `fact_type`      VARCHAR(64)     NOT NULL,
    `label`          VARCHAR(255)    NULL,
    `value_text`     TEXT            NULL,
    `value_numeric`  DECIMAL(18,6)   NULL,
    `unit`           VARCHAR(64)     NULL,
    `observed_on`    DATE            NULL,
    `metadata_json`  JSON            NULL,
    `created_at`     TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    INDEX `idx_ai_doc_facts_patient_created` (`patient_id`, `created_at`),
    INDEX `idx_ai_doc_facts_document` (`document_id`),
    INDEX `idx_ai_doc_facts_doc_uuid` (`document_uuid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `ai_document_source_snippets` (
    `id`            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `fact_id`       BIGINT UNSIGNED NULL,
    `patient_id`    BIGINT UNSIGNED NOT NULL,
    `document_id`   BIGINT UNSIGNED NOT NULL,
    `document_uuid` CHAR(36)        NOT NULL,
    `page_number`   INT UNSIGNED    NULL,
    `snippet_text`  TEXT            NOT NULL,
    `bbox_json`     JSON            NULL,
    `created_at`    TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    INDEX `idx_ai_doc_snippets_fact` (`fact_id`),
    INDEX `idx_ai_doc_snippets_document` (`document_id`),
    CONSTRAINT `fk_ai_doc_snippets_fact`
        FOREIGN KEY (`fact_id`) REFERENCES `ai_document_facts` (`id`)
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
