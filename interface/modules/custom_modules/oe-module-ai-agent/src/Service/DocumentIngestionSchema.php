<?php

/**
 * Idempotent schema guard for AI document ingestion.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Ryan Jagger <jagger@fastmail.com>
 * @copyright Copyright (c) 2026 Ryan Jagger <jagger@fastmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AiAgent\Service;

use OpenEMR\Common\Database\QueryUtils;

final class DocumentIngestionSchema
{
    private static bool $installed = false;

    public function ensureInstalled(): void
    {
        if (self::$installed) {
            return;
        }

        foreach ($this->statements() as $statement) {
            QueryUtils::sqlStatementThrowException($statement, [], true);
        }

        self::$installed = true;
    }

    /**
     * @return list<string>
     */
    private function statements(): array
    {
        return [
            <<<'SQL'
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
SQL,
            <<<'SQL'
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
SQL,
            <<<'SQL'
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
    `execute_interval` = VALUES(`execute_interval`)
SQL,
            <<<'SQL'
INSERT INTO `list_options` (
    `list_id`, `option_id`, `title`, `seq`, `is_default`, `activity`
) VALUES (
    'proc_res_status', 'ai_extracted', 'AI Extracted', 70, 0, 1
)
ON DUPLICATE KEY UPDATE
    `title`    = VALUES(`title`),
    `seq`      = VALUES(`seq`),
    `activity` = VALUES(`activity`)
SQL,
            <<<'SQL'
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
)
SQL,
            <<<'SQL'
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
SQL,
            <<<'SQL'
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
SQL,
        ];
    }
}
