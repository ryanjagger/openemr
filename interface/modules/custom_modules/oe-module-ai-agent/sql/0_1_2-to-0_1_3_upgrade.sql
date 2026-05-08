-- @package   OpenEMR
-- @link      https://www.open-emr.org
-- @author    Ryan Jagger <jagger@fastmail.com>
-- @copyright Copyright (c) 2026 Ryan Jagger <jagger@fastmail.com>
-- @license   GNU General Public License 3
--
-- Phase 0 of the native+FHIR ingestion refactor.
--
-- Lays the groundwork for AI-extracted facts to land in OpenEMR's native
-- clinical tables (procedure_result for labs, questionnaire_response for
-- intake) instead of the shadow ai_document_facts / ai_document_source_snippets
-- tables. This upgrade only adds storage; ingestion code keeps writing to
-- the shadow tables until Phase 1/3 cuts over.
--
-- Idempotent — safe to run multiple times.

-- ---------------------------------------------------------------------
-- proc_res_status enum gains an 'ai_extracted' option, used as the
-- procedure_result.result_status value for any row populated by AI
-- document ingestion. rhl7ReportStatus() in receive_hl7_results.inc.php
-- passes unknown OBR.25 values through verbatim, so the HL7 receive path
-- will store this value as-is.
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

-- ---------------------------------------------------------------------
-- Synthetic procedure_providers row representing AI document extraction.
-- Used as the lab_id argument when AiLabIngestionService calls
-- receive_hl7_results(), and as procedure_report.source so that lab rows
-- created from AI-extracted PDFs are attributable to this provider.
-- send_app_id 'OE-AI-INGEST' is the idempotency sentinel.
-- ---------------------------------------------------------------------
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

-- ---------------------------------------------------------------------
-- ai_result_provenance — bbox/page/snippet provenance for each
-- procedure_result row that was created by AI ingestion. Keyed 1:1 to
-- procedure_result.id (via UNIQUE), and joined back to the originating
-- ingestion job for user/patient context.
--
-- We deliberately do not FK to procedure_result.id (core table); the
-- existing AI-module tables follow the same pattern of FK-only-to-module
-- tables. The writer is responsible for ensuring the procedure_result
-- row exists.
-- ---------------------------------------------------------------------
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

-- ---------------------------------------------------------------------
-- ai_questionnaire_response_provenance — bbox/page/snippet provenance
-- for each answer (link_id) inside a questionnaire_response that was
-- created by AI intake-form extraction. Keyed UNIQUE on
-- (questionnaire_response_id, link_id) because one response carries
-- many answers, and each answer gets one provenance row.
-- ---------------------------------------------------------------------
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
