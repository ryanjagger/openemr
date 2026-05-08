<?php

/**
 * Lands AI-extracted lab results in OpenEMR's native procedure_* tables.
 *
 * Generates an ORU^R01 HL7 message from the sidecar's extracted facts and
 * feeds it through the existing receive_hl7_results() pipeline. After the
 * receiver inserts procedure_order / procedure_report / procedure_result
 * rows, this service writes the bbox/page/snippet provenance into
 * ai_result_provenance and links each procedure_result row back to the
 * source document via procedure_result.document_id.
 *
 * The procedure_provider used for the lab_id argument is the synthetic
 * 'AI Document Extraction' row seeded by Phase 0 (send_app_id =
 * 'OE-AI-INGEST'). procedure_result.result_status is set to 'ai_extracted'
 * so downstream consumers can distinguish AI-derived rows from
 * clinician-entered or HL7-vendor results.
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
use OpenEMR\Modules\AiAgent\DTO\LabFact;
use OpenEMR\Modules\AiAgent\DTO\LabIngestionRequest;
use OpenEMR\Modules\AiAgent\DTO\LabSourceSnippet;
use Ramsey\Uuid\Uuid;
use RuntimeException;

final class AiLabIngestionService
{
    public const PROVIDER_SEND_APP_ID = 'OE-AI-INGEST';
    public const RESULT_STATUS = 'ai_extracted';
    private const RECEIVE_HL7_PATH = __DIR__ . '/../../../../../orders/receive_hl7_results.inc.php';

    public static function default(): self
    {
        return new self();
    }

    /**
     * @return array{procedure_order_id:int,procedure_report_id:int,procedure_result_ids:list<int>}
     */
    public function ingest(LabIngestionRequest $request): array
    {
        if ($request->facts === []) {
            // Nothing to ingest — caller still marks the document completed.
            return [
                'procedure_order_id' => 0,
                'procedure_report_id' => 0,
                'procedure_result_ids' => [],
            ];
        }

        $providerId = $this->resolveProviderId();
        $patient = $this->fetchPatient($request->patientId);
        $controlId = $this->buildControlId($request);

        $hl7 = $this->buildHl7Message($request, $patient, $controlId);
        $rhl7Return = $this->invokeReceiver($hl7, $providerId);

        if (!empty($rhl7Return['fatal'])) {
            $messages = $rhl7Return['mssgs'] ?? [];
            throw new RuntimeException(
                'HL7 receive failed: ' . (is_array($messages) ? implode(' | ', $messages) : 'unknown error'),
            );
        }

        [$orderId, $reportId, $resultIds] = $this->locateInsertedRows($providerId, $controlId);
        if ($reportId === 0 || $resultIds === []) {
            throw new RuntimeException('HL7 receive completed but inserted procedure_report/result rows were not found.');
        }

        $this->attributeReportSource($reportId, $request->userId);
        $this->linkDocumentToResults($resultIds, $request->documentId);
        $this->writeProvenance($request, $resultIds);

        return [
            'procedure_order_id' => $orderId,
            'procedure_report_id' => $reportId,
            'procedure_result_ids' => $resultIds,
        ];
    }

    private function resolveProviderId(): int
    {
        $row = QueryUtils::fetchSingleValue(
            'SELECT `ppid` FROM `procedure_providers` WHERE `send_app_id` = ? LIMIT 1',
            'ppid',
            [self::PROVIDER_SEND_APP_ID],
        );
        if ($row === null) {
            throw new RuntimeException(
                "Synthetic procedure_providers row '" . self::PROVIDER_SEND_APP_ID
                . "' is missing. Run the Phase 0 schema upgrade.",
            );
        }

        return (int) $row;
    }

    /**
     * @return array{ss:string,fname:string,lname:string,mname:string,dob:string,sex:string,pubpid:string}
     */
    private function fetchPatient(int $patientId): array
    {
        $row = QueryUtils::fetchRecords(
            'SELECT `ss`, `fname`, `lname`, `mname`, `DOB`, `sex`, `pubpid` '
            . 'FROM `patient_data` WHERE `pid` = ? LIMIT 1',
            [$patientId],
        );
        if ($row === []) {
            throw new RuntimeException("Patient {$patientId} not found in patient_data.");
        }

        $r = $row[0];

        return [
            'ss' => (string) ($r['ss'] ?? ''),
            'fname' => (string) ($r['fname'] ?? ''),
            'lname' => (string) ($r['lname'] ?? ''),
            'mname' => (string) ($r['mname'] ?? ''),
            'dob' => (string) ($r['DOB'] ?? ''),
            'sex' => (string) ($r['sex'] ?? ''),
            'pubpid' => (string) ($r['pubpid'] ?? ''),
        ];
    }

    private function buildControlId(LabIngestionRequest $request): string
    {
        // Unique per (job, document) so post-receive lookup by (lab_id, control_id)
        // is unambiguous. Stays under 199 chars (procedure_order.control_id limit).
        return sprintf('AI-J%d-D%d', $request->jobId, $request->documentId);
    }

    /**
     * @param array{ss:string,fname:string,lname:string,mname:string,dob:string,sex:string,pubpid:string} $patient
     */
    private function buildHl7Message(LabIngestionRequest $request, array $patient, string $controlId): string
    {
        $cr = "\r";
        $now = date('YmdHis');
        $msgControlId = substr(Uuid::uuid4()->toString(), 0, 20);

        $observationDt = $this->resolveObservationDateTime($request);
        $sex = $this->mapSexToHl7($patient['sex']);
        $dob = str_replace('-', '', $patient['dob']);

        $msh = $this->joinSegment([
            'MSH',
            '^~\\&',
            self::PROVIDER_SEND_APP_ID,
            'OPENEMR',
            'OPENEMR',
            'OPENEMR',
            $now,
            '',
            'ORU^R01',
            $msgControlId,
            'P',
            '2.3',
        ]);
        $pid = $this->joinSegment([
            'PID',
            '1',
            $patient['pubpid'],
            $patient['pubpid'],
            preg_replace('/[^0-9]/', '', $patient['ss']) ?? '',
            $this->escape($patient['lname']) . '^' . $this->escape($patient['fname']) . '^' . $this->escape($patient['mname']),
            '',
            $dob,
            $sex,
            '',  // PID-9 patient alias
            '',  // PID-10 race
            '',  // PID-11 patient address (street^^city^state^zip)
            '',  // PID-12 country code
            '',  // PID-13 phone home
            '',  // PID-14 phone business
        ]);
        $obr = $this->joinSegment([
            'OBR',
            '1',
            '',
            $controlId,
            'AI-EXTRACTED^AI Extracted Lab Report^L',
            '',
            '',
            $observationDt,
            '',
            '',
            '',
            '',
            '',
            '',
            '',
            '',
            '',
            '',
            '',
            '',
            '',
            '',
            $now,
            '',
            '',
            self::RESULT_STATUS,
        ]);

        $segments = [$msh, $pid, $obr];
        $sequence = 1;
        foreach ($request->facts as $fact) {
            $segments[] = $this->buildObxSegment($fact, $sequence, $observationDt);
            $sequence++;
        }

        return implode($cr, $segments) . $cr;
    }

    private function buildObxSegment(LabFact $fact, int $sequence, string $defaultObservedDt): string
    {
        $observedDt = $fact->observedOn !== null
            ? str_replace('-', '', $fact->observedOn) . '000000'
            : $defaultObservedDt;
        $value = $this->valueForObx($fact);
        $abnormal = $this->mapAbnormalToHl7($fact->flag);
        $resultCode = $this->slugify($fact->label);

        return $this->joinSegment([
            'OBX',
            (string) $sequence,
            'ST',
            $resultCode . '^' . $this->escape($fact->label),
            '',
            $this->escape($value),
            $this->escape($fact->unit ?? ''),
            $this->escape($fact->referenceRange ?? ''),
            $abnormal,
            '',
            '',
            self::RESULT_STATUS,
            '',
            '',
            $observedDt,
        ]);
    }

    private function valueForObx(LabFact $fact): string
    {
        if ($fact->valueText !== null && $fact->valueText !== '') {
            return $fact->valueText;
        }
        if ($fact->valueNumeric !== null) {
            // Trim trailing zeros from a stringified float without losing precision below 1e-6.
            return rtrim(rtrim(number_format($fact->valueNumeric, 6, '.', ''), '0'), '.');
        }

        return '';
    }

    private function resolveObservationDateTime(LabIngestionRequest $request): string
    {
        foreach ($request->facts as $fact) {
            if ($fact->observedOn !== null) {
                return str_replace('-', '', $fact->observedOn) . '000000';
            }
        }

        return date('YmdHis');
    }

    private function mapSexToHl7(string $sex): string
    {
        return match (strtolower(trim($sex))) {
            'male', 'm' => 'M',
            'female', 'f' => 'F',
            'transgender', 't' => 'T',
            default => 'U',
        };
    }

    private function mapAbnormalToHl7(?string $flag): string
    {
        if ($flag === null) {
            return '';
        }
        $normalized = strtoupper(trim($flag));

        return match ($normalized) {
            '', 'N', 'NORMAL' => 'N',
            'H', 'HIGH' => 'H',
            'L', 'LOW' => 'L',
            'HH', 'VHIGH', 'CRITICAL HIGH', 'PANIC HIGH' => 'HH',
            'LL', 'VLOW', 'CRITICAL LOW', 'PANIC LOW' => 'LL',
            'A', 'ABNORMAL' => 'A',
            default => 'A',
        };
    }

    private function slugify(string $label): string
    {
        $normalized = strtoupper(preg_replace('/[^A-Za-z0-9]+/', '_', $label) ?? '');
        $trimmed = trim($normalized, '_');

        return $trimmed === '' ? 'AI_RESULT' : 'AI_' . $trimmed;
    }

    private function escape(string $value): string
    {
        // HL7 v2.3 escape sequences for our delimiters: | ^ ~ \ &
        return str_replace(
            ['\\', '|', '^', '~', '&'],
            ['\\E\\', '\\F\\', '\\S\\', '\\R\\', '\\T\\'],
            $value,
        );
    }

    /**
     * @param list<string> $fields
     */
    private function joinSegment(array $fields): string
    {
        return implode('|', $fields);
    }

    /**
     * @return array<string, mixed>
     */
    private function invokeReceiver(string $hl7, int $providerId): array
    {
        require_once self::RECEIVE_HL7_PATH;

        $matchreq = [];
        $matchresp = null;
        // The receive function captures errors into a global by reference;
        // pass-by-ref is required for $hl7 and $matchreq.
        receive_hl7_results($hl7, $matchreq, $providerId, 'R', false, $matchresp);

        global $rhl7_return;

        return is_array($rhl7_return) ? $rhl7_return : [];
    }

    /**
     * @return array{0:int,1:int,2:list<int>}
     */
    private function locateInsertedRows(int $providerId, string $controlId): array
    {
        $orderRow = QueryUtils::fetchRecords(
            'SELECT `procedure_order_id` FROM `procedure_order` '
            . 'WHERE `lab_id` = ? AND `control_id` = ? '
            . 'ORDER BY `procedure_order_id` DESC LIMIT 1',
            [$providerId, $controlId],
        );
        if ($orderRow === []) {
            return [0, 0, []];
        }
        $orderId = (int) $orderRow[0]['procedure_order_id'];

        $reportRow = QueryUtils::fetchRecords(
            'SELECT `procedure_report_id` FROM `procedure_report` '
            . 'WHERE `procedure_order_id` = ? '
            . 'ORDER BY `procedure_report_id` DESC LIMIT 1',
            [$orderId],
        );
        if ($reportRow === []) {
            return [$orderId, 0, []];
        }
        $reportId = (int) $reportRow[0]['procedure_report_id'];

        $resultRows = QueryUtils::fetchRecords(
            'SELECT `procedure_result_id` FROM `procedure_result` '
            . 'WHERE `procedure_report_id` = ? '
            . 'ORDER BY `procedure_result_id` ASC',
            [$reportId],
        );
        $resultIds = array_map(static fn (array $r): int => (int) $r['procedure_result_id'], $resultRows);

        return [$orderId, $reportId, $resultIds];
    }

    private function attributeReportSource(int $reportId, int $userId): void
    {
        QueryUtils::sqlStatementThrowException(
            'UPDATE `procedure_report` SET `source` = ? WHERE `procedure_report_id` = ?',
            [$userId, $reportId],
        );
    }

    /**
     * @param list<int> $resultIds
     */
    private function linkDocumentToResults(array $resultIds, int $documentId): void
    {
        if ($resultIds === []) {
            return;
        }
        $placeholders = implode(',', array_fill(0, count($resultIds), '?'));
        QueryUtils::sqlStatementThrowException(
            "UPDATE `procedure_result` SET `document_id` = ? "
            . "WHERE `procedure_result_id` IN ($placeholders)",
            array_merge([$documentId], $resultIds),
        );
    }

    /**
     * Writes one ai_result_provenance row per inserted procedure_result row.
     *
     * Pairs facts to results positionally: receive_hl7_results() inserts
     * procedure_result rows in the order it encounters OBX segments, and we
     * emit OBX in fact-array order. The lookup ORDER BY procedure_result_id
     * preserves that.
     *
     * @param list<int> $resultIds
     */
    private function writeProvenance(LabIngestionRequest $request, array $resultIds): void
    {
        $count = min(count($resultIds), count($request->facts));
        for ($i = 0; $i < $count; $i++) {
            $fact = $request->facts[$i];
            $primarySnippet = $fact->sourceSnippets[0] ?? null;

            QueryUtils::sqlInsert(
                'INSERT INTO `ai_result_provenance` '
                . '(`procedure_result_id`, `document_id`, `extraction_job_id`, '
                . '`page_number`, `bbox_json`, `snippet_text`, '
                . '`extraction_confidence`, `extraction_model`) '
                . 'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                [
                    $resultIds[$i],
                    $request->documentId,
                    $request->jobId,
                    $primarySnippet?->pageNumber,
                    $primarySnippet !== null && $primarySnippet->bbox !== null
                        ? json_encode($primarySnippet->bbox, JSON_THROW_ON_ERROR)
                        : null,
                    $primarySnippet?->text,
                    $request->extractionConfidence,
                    $request->modelId,
                ],
            );
        }
    }
}
