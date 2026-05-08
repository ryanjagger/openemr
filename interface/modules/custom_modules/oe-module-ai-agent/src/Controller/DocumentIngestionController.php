<?php

/**
 * API endpoints for manual document ingestion into co-pilot chat context.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Ryan Jagger <jagger@fastmail.com>
 * @copyright Copyright (c) 2026 Ryan Jagger <jagger@fastmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AiAgent\Controller;

use OpenEMR\Common\Database\QueryUtils;
use OpenEMR\Common\Http\HttpRestRequest;
use OpenEMR\Common\Session\SessionWrapperFactory;
use OpenEMR\Common\Uuid\UuidRegistry;
use OpenEMR\Modules\AiAgent\Service\DocumentIngestionLauncher;
use OpenEMR\Modules\AiAgent\Service\DocumentIngestionRepository;
use OpenEMR\Modules\AiAgent\Service\PatientAccessValidator;
use OpenEMR\Modules\AiAgent\Service\SidecarClient;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Response;
use Throwable;

final class DocumentIngestionController
{
    public function __construct(
        private readonly DocumentIngestionRepository $repository,
        private readonly PatientAccessValidator $patientAccessValidator,
        private readonly DocumentIngestionLauncher $launcher,
    ) {
    }

    public static function default(): self
    {
        return new self(
            new DocumentIngestionRepository(),
            new PatientAccessValidator(),
            new DocumentIngestionLauncher(),
        );
    }

    /**
     * @return JsonResponse
     */
    public function recent(string $pid, HttpRestRequest $request): JsonResponse
    {
        $patientId = $this->patientId($pid);
        if ($patientId === 0) {
            return $this->error('patient_not_found', 404);
        }
        // Pass the resolved integer pid: PatientAccessValidator::canRead expects
        // a numeric string (ctype_digit), but $pid here can be either a numeric
        // pid (browser flow) or a UUID (agent bearer-token flow).
        if (!$this->patientAccessValidator->canRead((string) $patientId)) {
            return $this->error('forbidden', 403);
        }

        $username = $this->username($request);
        if ($username === '') {
            return $this->error('no_authenticated_user', 401);
        }

        return $this->json([
            'documents' => $this->repository->recentEligibleDocuments(
                patientId: $patientId,
                username: $username,
            ),
        ]);
    }

    /**
     * @return JsonResponse
     */
    public function ingest(string $pid, HttpRestRequest $request): JsonResponse
    {
        $patientId = $this->patientId($pid);
        if ($patientId === 0) {
            return $this->error('patient_not_found', 404);
        }
        if (!$this->patientAccessValidator->canRead((string) $patientId)) {
            return $this->error('forbidden', 403);
        }

        $userId = $this->userId($request);
        $username = $this->username($request);
        if ($userId === 0 || $username === '') {
            return $this->error('no_authenticated_user', 401);
        }

        try {
            $job = $this->repository->createJob(
                patientId: $patientId,
                userId: $userId,
                username: $username,
                selectedDocuments: $this->selectedDocuments($request),
            );
            $this->launcher->launch();
        } catch (\InvalidArgumentException $e) {
            return $this->error('no_eligible_documents', 400, $e->getMessage());
        } catch (Throwable $e) {
            error_log('oe-module-ai-agent: document ingestion enqueue failed: ' . $e->getMessage());
            return $this->error('document_ingestion_enqueue_failed', 500);
        }

        return $this->json($job);
    }

    /**
     * @return JsonResponse
     */
    public function job(string $pid, string $jobId, HttpRestRequest $request): JsonResponse
    {
        $patientId = $this->patientId($pid);
        if ($patientId === 0) {
            return $this->error('patient_not_found', 404);
        }
        if (!$this->patientAccessValidator->canRead((string) $patientId)) {
            return $this->error('forbidden', 403);
        }

        try {
            $job = $this->repository->jobStatus($patientId, $jobId);
            if (($job['status'] ?? null) === 'pending') {
                $this->launcher->launch();
            }

            return $this->json($job);
        } catch (Throwable) {
            return $this->error('job_not_found', 404);
        }
    }

    public function sourcePreview(string $pid, HttpRestRequest $request): Response
    {
        $patientId = $this->patientId($pid);
        if ($patientId === 0) {
            return new Response('', Response::HTTP_NOT_FOUND);
        }
        if (!$this->patientAccessValidator->canRead((string) $patientId)) {
            return new Response('', Response::HTTP_FORBIDDEN);
        }

        $username = $this->username($request);
        if ($username === '') {
            return new Response('', Response::HTTP_UNAUTHORIZED);
        }

        $documentId = $this->queryInt($request, 'document_id', 0);
        if (!$this->repository->canAccessDocument($patientId, $username, $documentId)) {
            return new Response('', Response::HTTP_NOT_FOUND);
        }

        try {
            $document = new \Document($documentId);
            if ((string) $document->get_mimetype() !== 'application/pdf') {
                return new Response('', Response::HTTP_UNSUPPORTED_MEDIA_TYPE);
            }

            $data = $document->get_data();
            if (!is_string($data) || $data === '') {
                return new Response('', Response::HTTP_NOT_FOUND);
            }

            $page = max(1, $this->queryInt($request, 'page', 1));
            $bbox = $this->bboxPayload($request);
            $bboxUnit = $this->bboxUnit($request, $bbox);
            try {
                $content = SidecarClient::fromEnvironment()->renderPdfPagePreview(
                    $data,
                    $page,
                    $bbox,
                    $bboxUnit,
                );
                return $this->pngResponse($content);
            } catch (Throwable $e) {
                error_log('oe-module-ai-agent: sidecar source PDF preview failed: ' . $e->getMessage());
            }

            return $this->renderPdfPagePreview(
                pdfData: $data,
                page: $page,
                request: $request,
            );
        } catch (Throwable $e) {
            error_log('oe-module-ai-agent: source PDF preview failed: ' . $e->getMessage());
            return new Response('', Response::HTTP_UNPROCESSABLE_ENTITY);
        }
    }

    /**
     * @return list<array{document_id: int, document_type: string}>
     */
    private function selectedDocuments(HttpRestRequest $request): array
    {
        $body = $this->decodeBody($request);
        $documents = $body['documents'] ?? [];
        if (!is_array($documents)) {
            return [];
        }

        $selected = [];
        foreach ($documents as $document) {
            if (!is_array($document)) {
                continue;
            }
            $id = $document['id'] ?? $document['document_id'] ?? null;
            $type = $document['document_type'] ?? null;
            if (!is_int($id) && !(is_string($id) && ctype_digit($id))) {
                continue;
            }
            if (!is_string($type)) {
                continue;
            }
            $selected[] = [
                'document_id' => (int) $id,
                'document_type' => $type,
            ];
        }

        return $selected;
    }

    /**
     * @return array<string, mixed>
     */
    private function decodeBody(HttpRestRequest $request): array
    {
        $raw = (string) $request->getContent();
        if ($raw === '') {
            return [];
        }
        try {
            $decoded = json_decode($raw, true, flags: JSON_THROW_ON_ERROR);
        } catch (Throwable) {
            return [];
        }

        return is_array($decoded) ? $decoded : [];
    }

    private function patientId(string $pid): int
    {
        if (ctype_digit($pid)) {
            return (int) $pid;
        }
        if (!UuidRegistry::isValidStringUUID($pid)) {
            return 0;
        }
        $rows = QueryUtils::fetchRecords(
            'SELECT `pid` FROM `patient_data` WHERE `uuid` = ? LIMIT 1',
            [UuidRegistry::uuidToBytes($pid)],
            true,
        );

        return $rows === [] ? 0 : (int) $rows[0]['pid'];
    }

    private function patientUuid(int $patientId): ?string
    {
        $rows = QueryUtils::fetchRecords(
            'SELECT `uuid` FROM `patient_data` WHERE `pid` = ? LIMIT 1',
            [$patientId],
            true,
        );
        if ($rows === [] || empty($rows[0]['uuid'])) {
            return null;
        }

        return UuidRegistry::uuidToString($rows[0]['uuid']);
    }

    private function queryString(HttpRestRequest $request, string $key): ?string
    {
        $value = $request->query->get($key);
        if (!is_string($value) && !is_numeric($value)) {
            return null;
        }
        $text = trim((string) $value);

        return $text === '' ? null : $text;
    }

    private function queryInt(HttpRestRequest $request, string $key, int $default): int
    {
        $value = $request->query->get($key);
        if (is_int($value)) {
            return $value;
        }
        if (is_string($value) && ctype_digit($value)) {
            return (int) $value;
        }

        return $default;
    }

    private function queryFloat(HttpRestRequest $request, string $key): ?float
    {
        $value = $request->query->get($key);
        if (is_int($value) || is_float($value)) {
            return (float) $value;
        }
        if (is_string($value) && is_numeric($value)) {
            return (float) $value;
        }

        return null;
    }

    /**
     * @return array{x: float, y: float, width: float, height: float}|null
     */
    private function bboxPayload(HttpRestRequest $request): ?array
    {
        $x = $this->queryFloat($request, 'x');
        $y = $this->queryFloat($request, 'y');
        $width = $this->queryFloat($request, 'width');
        $height = $this->queryFloat($request, 'height');
        if ($x === null || $y === null || $width === null || $height === null) {
            return null;
        }
        if ($x < 0 || $y < 0 || $width <= 0 || $height <= 0) {
            return null;
        }

        return [
            'x' => $x,
            'y' => $y,
            'width' => $width,
            'height' => $height,
        ];
    }

    /**
     * @param array{x: float, y: float, width: float, height: float}|null $bbox
     */
    private function bboxUnit(HttpRestRequest $request, ?array $bbox): string
    {
        $unit = $this->queryString($request, 'bbox_unit');
        if (in_array($unit, ['normalized', 'percent', 'pixels'], true)) {
            return (string) $unit;
        }
        if ($bbox === null) {
            return 'normalized';
        }

        return $this->inferBboxUnit($bbox['x'], $bbox['y'], $bbox['width'], $bbox['height']);
    }

    private function renderPdfPagePreview(string $pdfData, int $page, HttpRestRequest $request): Response
    {
        $pdf = new \Imagick();
        $pdf->setResolution(144, 144);
        $pdf->readImageBlob($pdfData);

        $pageIndex = $page - 1;
        if ($pageIndex < 0 || $pageIndex >= $pdf->getNumberImages()) {
            $pdf->clear();
            $pdf->destroy();
            return new Response('', Response::HTTP_NOT_FOUND);
        }

        $pdf->setIteratorIndex($pageIndex);
        $pageImage = $pdf->getImage();
        $pageImage->setImageBackgroundColor(new \ImagickPixel('white'));
        $image = $pageImage->mergeImageLayers(\Imagick::LAYERMETHOD_FLATTEN);
        $image->setImageFormat('png');

        $bbox = $this->bboxPixels($request, $image->getImageWidth(), $image->getImageHeight());
        if ($bbox !== null) {
            $this->drawBbox($image, $bbox);
        }

        $content = $image->getImagesBlob();

        $image->clear();
        $image->destroy();
        $pageImage->clear();
        $pageImage->destroy();
        $pdf->clear();
        $pdf->destroy();

        return new Response($content, Response::HTTP_OK, [
            'Content-Type' => 'image/png',
            'Cache-Control' => 'private, max-age=300',
        ]);
    }

    /**
     * @return array{left: float, top: float, right: float, bottom: float}|null
     */
    private function bboxPixels(HttpRestRequest $request, int $imageWidth, int $imageHeight): ?array
    {
        $x = $this->queryFloat($request, 'x');
        $y = $this->queryFloat($request, 'y');
        $width = $this->queryFloat($request, 'width');
        $height = $this->queryFloat($request, 'height');
        if ($x === null || $y === null || $width === null || $height === null) {
            return null;
        }
        if ($x < 0 || $y < 0 || $width <= 0 || $height <= 0) {
            return null;
        }

        $unit = $this->bboxUnit($request, [
            'x' => $x,
            'y' => $y,
            'width' => $width,
            'height' => $height,
        ]);
        [$scaleX, $scaleY] = match ($unit) {
            'normalized' => [(float) $imageWidth, (float) $imageHeight],
            'percent' => [$imageWidth / 100.0, $imageHeight / 100.0],
            default => [1.0, 1.0],
        };

        $left = $this->clamp($x * $scaleX, 0.0, (float) $imageWidth);
        $top = $this->clamp($y * $scaleY, 0.0, (float) $imageHeight);
        $right = $this->clamp(($x + $width) * $scaleX, 0.0, (float) $imageWidth);
        $bottom = $this->clamp(($y + $height) * $scaleY, 0.0, (float) $imageHeight);

        if ($right - $left < 2.0 || $bottom - $top < 2.0) {
            return null;
        }

        return [
            'left' => $left,
            'top' => $top,
            'right' => $right,
            'bottom' => $bottom,
        ];
    }

    private function inferBboxUnit(float $x, float $y, float $width, float $height): string
    {
        if ($x <= 1.0 && $y <= 1.0 && $width <= 1.0 && $height <= 1.0) {
            return 'normalized';
        }
        if ($x <= 100.0 && $y <= 100.0 && $width <= 100.0 && $height <= 100.0) {
            return 'percent';
        }

        return 'pixels';
    }

    /**
     * @param array{left: float, top: float, right: float, bottom: float} $bbox
     */
    private function drawBbox(\Imagick $image, array $bbox): void
    {
        $draw = new \ImagickDraw();
        $strokeWidth = max(3.0, min($image->getImageWidth(), $image->getImageHeight()) * 0.004);
        $draw->setStrokeColor(new \ImagickPixel('#dc3545'));
        $draw->setStrokeWidth($strokeWidth);
        $draw->setFillColor(new \ImagickPixel('#dc3545'));
        $draw->setFillOpacity(0.18);
        $draw->rectangle($bbox['left'], $bbox['top'], $bbox['right'], $bbox['bottom']);
        $image->drawImage($draw);
        $draw->clear();
        $draw->destroy();
    }

    private function pngResponse(string $content): Response
    {
        return new Response($content, Response::HTTP_OK, [
            'Content-Type' => 'image/png',
            'Cache-Control' => 'private, max-age=300',
        ]);
    }

    private function clamp(float $value, float $min, float $max): float
    {
        return min($max, max($min, $value));
    }

    /**
     * Resolve the authenticated user's integer id.
     *
     * Source-of-truth precedence:
     *   1. ``HttpRestRequest::getRequestUser()['id']`` — explicitly populated by
     *      every REST authorization strategy (bearer-token, skip-auth, browser
     *      session-cookie). This is the contract REST controllers should rely on.
     *   2. ``$_SESSION['authUserID']`` fallback — preserves the historical
     *      session-cookie path for any non-REST entrypoint that might call this
     *      controller directly without going through the dispatch pipeline.
     */
    private function userId(HttpRestRequest $request): int
    {
        $requestUser = $request->getRequestUser();
        $requestUserId = $requestUser['id'] ?? null;
        if (is_int($requestUserId)) {
            return $requestUserId;
        }
        if (is_string($requestUserId) && ctype_digit($requestUserId)) {
            return (int) $requestUserId;
        }

        $sessionValue = SessionWrapperFactory::getInstance()->getActiveSession()->get('authUserID');
        if (is_int($sessionValue)) {
            return $sessionValue;
        }
        if (is_string($sessionValue) && ctype_digit($sessionValue)) {
            return (int) $sessionValue;
        }

        return 0;
    }

    private function username(HttpRestRequest $request): string
    {
        $requestUser = $request->getRequestUser();
        $requestUsername = $requestUser['username'] ?? null;
        if (is_string($requestUsername) && $requestUsername !== '') {
            return $requestUsername;
        }

        $sessionValue = SessionWrapperFactory::getInstance()->getActiveSession()->get('authUser');

        return is_string($sessionValue) ? $sessionValue : '';
    }

    /**
     * @param array<string, mixed> $payload
     */
    private function json(array $payload, int $httpStatus = 200): JsonResponse
    {
        return new JsonResponse($payload, $httpStatus);
    }

    private function error(string $code, int $httpStatus, ?string $detail = null): JsonResponse
    {
        $payload = ['error' => $code];
        if ($detail !== null) {
            $payload['detail'] = $detail;
        }

        return $this->json($payload, $httpStatus);
    }
}
