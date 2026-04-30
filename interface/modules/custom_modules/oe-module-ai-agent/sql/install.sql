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
    'openid api:fhir user/Patient.read user/Condition.read user/MedicationRequest.read user/AllergyIntolerance.read user/Encounter.read user/Observation.read user/DocumentReference.read user/ServiceRequest.read user/Procedure.read user/Immunization.read',
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
