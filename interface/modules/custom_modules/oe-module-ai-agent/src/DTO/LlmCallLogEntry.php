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
 * One row destined for the llm_call_log table.
 *
 * The HMAC integrity_checksum is computed by AuditLogService at insert time
 * over a canonical serialization of every field on this DTO except the
 * checksum itself, so a single-row tamper without the key is detectable.
 *
 * Observability fields ($latencyMs, $costUsdMicros, $stepsJson, $errorCode,
 * $errorDetail) come from the sidecar's ResponseMeta and are nullable so
 * pre-observability rows (and rows where the sidecar errored before
 * producing meta) still write cleanly.
 */
final readonly class LlmCallLogEntry
{
    /**
     * @param list<array<string, mixed>>|null $verificationFailures
     */
    public function __construct(
        public string $requestId,
        public int $userId,
        public int $patientId,
        public string $actionType,
        public string $modelId,
        public int $promptTokens,
        public int $completionTokens,
        public string $requestHash,
        public string $responseHash,
        public ?array $verificationFailures,
        public LlmCallVerificationStatus $verificationStatus,
        public ?string $conversationId = null,
        public ?int $latencyMs = null,
        public ?int $costUsdMicros = null,
        public ?string $stepsJson = null,
        public ?string $errorCode = null,
        public ?string $errorDetail = null,
    ) {
    }
}
