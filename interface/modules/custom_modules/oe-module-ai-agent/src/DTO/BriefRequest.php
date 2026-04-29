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

final readonly class BriefRequest
{
    public function __construct(
        public string $patientUuid,
        public string $fhirBaseUrl,
        public string $bearerToken,
        public string $requestId,
    ) {
    }

    /**
     * @return array{patient_uuid: string, fhir_base_url: string, bearer_token: string, request_id: string}
     */
    public function toArray(): array
    {
        return [
            'patient_uuid' => $this->patientUuid,
            'fhir_base_url' => $this->fhirBaseUrl,
            'bearer_token' => $this->bearerToken,
            'request_id' => $this->requestId,
        ];
    }
}
