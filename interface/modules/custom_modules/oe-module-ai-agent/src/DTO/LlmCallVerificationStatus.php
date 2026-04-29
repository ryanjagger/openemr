<?php

/**
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Ryan Jagger <jagger@fastmail.com>
 * @copyright Copyright (c) 2026 Ryan Jagger <jagger@fastmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AiAgent\DTO;

/**
 * Backed enum mirrors the llm_call_log.verification_status ENUM column so
 * the value persisted to the database matches the schema literally.
 */
enum LlmCallVerificationStatus: string
{
    case Passed = 'passed';
    case Partial = 'partial';
    case Failed = 'failed';
    case Denied = 'denied';
}
