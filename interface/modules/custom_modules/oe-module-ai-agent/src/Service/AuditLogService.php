<?php

/**
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Ryan Jagger <jagger@fastmail.com>
 * @copyright Copyright (c) 2026 Ryan Jagger <jagger@fastmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AiAgent\Service;

use OpenEMR\Common\Database\QueryUtils;
use OpenEMR\Modules\AiAgent\DTO\LlmCallLogEntry;
use RuntimeException;
use Throwable;

/**
 * Writes one row per agent invocation to the supplementary llm_call_log
 * table. Hashes only — no raw prompts or responses (ARCH §8.2). The
 * integrity_checksum HMAC (ARCH §8.3) detects single-row tampering by
 * anyone without the audit secret.
 */
final class AuditLogService
{
    public const ACTION_BRIEF_READ = 'brief.read';

    public function __construct(
        private readonly string $hmacSecret,
    ) {
    }

    public static function default(): self
    {
        // LLM_AUDIT_HMAC_SECRET is the production knob. INTERNAL_AUTH_SECRET is
        // an acceptable dev fallback so the dev-easy stack works without an
        // extra env var; production deployments must rotate this independently.
        $secret = (string) (getenv('LLM_AUDIT_HMAC_SECRET') ?: '');
        if ($secret === '') {
            $secret = (string) (getenv('INTERNAL_AUTH_SECRET') ?: '');
        }
        if ($secret === '') {
            throw new RuntimeException('LLM_AUDIT_HMAC_SECRET is not configured');
        }

        return new self($secret);
    }

    public function record(LlmCallLogEntry $entry): void
    {
        $createdAt = (new \DateTimeImmutable('now', new \DateTimeZone('UTC')))
            ->format('Y-m-d H:i:s');

        $row = [
            'request_id' => $entry->requestId,
            'user_id' => $entry->userId,
            'patient_id' => $entry->patientId,
            'action_type' => $entry->actionType,
            'model_id' => $entry->modelId,
            'prompt_tokens' => $entry->promptTokens,
            'completion_tokens' => $entry->completionTokens,
            'request_hash' => $entry->requestHash,
            'response_hash' => $entry->responseHash,
            'tool_calls' => null,
            'verification_status' => $entry->verificationStatus->value,
            'verification_failures' => $entry->verificationFailures,
            'prev_log_hash' => null,
            'created_at' => $createdAt,
        ];

        $checksum = $this->integrityChecksum($row);

        try {
            QueryUtils::sqlStatementThrowException(
                'INSERT INTO `llm_call_log` ('
                . '`request_id`, `user_id`, `patient_id`, `action_type`, `model_id`, '
                . '`prompt_tokens`, `completion_tokens`, `request_hash`, `response_hash`, '
                . '`tool_calls`, `verification_status`, `verification_failures`, '
                . '`integrity_checksum`, `prev_log_hash`, `created_at`'
                . ') VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                [
                    $row['request_id'],
                    $row['user_id'],
                    $row['patient_id'],
                    $row['action_type'],
                    $row['model_id'],
                    $row['prompt_tokens'],
                    $row['completion_tokens'],
                    $row['request_hash'],
                    $row['response_hash'],
                    $row['tool_calls'],
                    $row['verification_status'],
                    $entry->verificationFailures === null
                        ? null
                        : json_encode($entry->verificationFailures, JSON_THROW_ON_ERROR),
                    $checksum,
                    $row['prev_log_hash'],
                    $row['created_at'],
                ],
            );
        } catch (Throwable $e) {
            // Failing to write the audit row must not break the user flow,
            // but it must be loud enough to surface in error_log so the
            // operator can investigate. Caller already has the response.
            error_log('oe-module-ai-agent: audit insert failed: ' . $e->getMessage());
        }
    }

    /**
     * @param array<string, mixed> $row
     */
    private function integrityChecksum(array $row): string
    {
        // Canonical serialization: stable key order, JSON-encoded. Excludes
        // the checksum itself (computed over the rest of the row) and the
        // auto-incremented id (not yet known at hash time).
        $canonical = json_encode(
            $row,
            JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR,
        );

        return hash_hmac('sha256', $canonical, $this->hmacSecret);
    }
}
