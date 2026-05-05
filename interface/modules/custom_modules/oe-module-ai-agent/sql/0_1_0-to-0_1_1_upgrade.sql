-- ---------------------------------------------------------------------
-- Document ingestion for co-pilot chat. These tables intentionally store
-- extracted facts and source snippets only; the original uploaded binary
-- remains in the OpenEMR documents store.
-- ---------------------------------------------------------------------
UPDATE `oauth_clients`
SET `scope` = 'openid api:fhir api:oemr user/Patient.read user/Appointment.read user/CarePlan.read user/Condition.read user/MedicationRequest.read user/AllergyIntolerance.read user/Encounter.read user/Goal.read user/Observation.read user/DocumentReference.read user/ServiceRequest.read user/Procedure.read user/Immunization.read user/document.read'
WHERE `client_id` = 'oe-module-ai-agent-internal';

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
