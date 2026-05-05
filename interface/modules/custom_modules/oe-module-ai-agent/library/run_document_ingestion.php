<?php

/**
 * Background-service entry point for AI document ingestion.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Ryan Jagger <jagger@fastmail.com>
 * @copyright Copyright (c) 2026 Ryan Jagger <jagger@fastmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

use OpenEMR\Modules\AiAgent\Service\DocumentIngestionWorker;

if (!function_exists('doAiDocumentIngestionTask')) {
    function doAiDocumentIngestionTask(): void
    {
        DocumentIngestionWorker::default()->processPendingJobs();
    }
}
