<?php

/**
 * Starts the AI document ingestion background service after a job is queued.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Ryan Jagger <jagger@fastmail.com>
 * @copyright Copyright (c) 2026 Ryan Jagger <jagger@fastmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AiAgent\Service;

use OpenEMR\Core\OEGlobalsBag;
use Throwable;

final class DocumentIngestionLauncher
{
    public const BACKGROUND_SERVICE_NAME = 'AI_Document_Ingestion_Task';

    public function launch(): void
    {
        if (!function_exists('exec')) {
            error_log('oe-module-ai-agent: exec() is unavailable; document ingestion will wait for cron.');
            return;
        }

        try {
            $command = $this->backgroundCommand();
        } catch (Throwable $e) {
            error_log('oe-module-ai-agent: failed to build document ingestion command: ' . $e->getMessage());
            return;
        }

        $output = [];
        $exitCode = 0;
        exec($command . ' > /dev/null 2>&1 &', $output, $exitCode);
        if ($exitCode !== 0) {
            error_log('oe-module-ai-agent: failed to launch document ingestion background service.');
        }
    }

    private function backgroundCommand(): string
    {
        $console = rtrim(OEGlobalsBag::getInstance()->getProjectDir(), '/') . '/bin/console';

        return implode(' ', [
            escapeshellarg($this->phpBinary()),
            escapeshellarg($console),
            'background:services',
            'run',
            '--name=' . escapeshellarg(self::BACKGROUND_SERVICE_NAME),
            '--json',
        ]);
    }

    private function phpBinary(): string
    {
        $binary = PHP_BINARY;
        if ($binary !== '' && is_file($binary) && is_executable($binary) && !str_contains(basename($binary), 'php-fpm')) {
            return $binary;
        }

        return 'php';
    }
}
