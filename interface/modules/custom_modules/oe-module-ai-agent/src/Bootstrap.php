<?php

/**
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Ryan Jagger <jagger@fastmail.com>
 * @copyright Copyright (c) 2026 Ryan Jagger <jagger@fastmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AiAgent;

use OpenEMR\Common\Csrf\CsrfUtils;
use OpenEMR\Common\Http\HttpRestRequest;
use OpenEMR\Common\Session\SessionWrapperFactory;
use OpenEMR\Events\PatientDemographics\RenderEvent as DemographicsRenderEvent;
use OpenEMR\Events\RestApiExtend\RestApiCreateEvent;
use OpenEMR\Menu\MenuEvent;
use OpenEMR\Modules\AiAgent\Controller\BriefController;
use OpenEMR\Modules\AiAgent\Controller\ChatController;
use OpenEMR\Modules\AiAgent\Controller\DocumentIngestionController;
use stdClass;
use Symfony\Component\EventDispatcher\EventDispatcherInterface;
use Symfony\Component\HttpFoundation\JsonResponse;

final class Bootstrap
{
    private const TEMPLATE_DIR = __DIR__ . '/../templates';
    private const PUBLIC_PATH = '/interface/modules/custom_modules/oe-module-ai-agent/public';
    private const ADMIN_RECENT_PATH = '/interface/modules/custom_modules/oe-module-ai-agent/admin/recent.php';

    public function __construct(
        private readonly EventDispatcherInterface $eventDispatcher,
    ) {
    }

    public function subscribeToEvents(): void
    {
        $this->eventDispatcher->addListener(
            RestApiCreateEvent::EVENT_HANDLE,
            $this->registerRestRoutes(...),
        );
        $this->eventDispatcher->addListener(
            DemographicsRenderEvent::EVENT_SECTION_LIST_RENDER_BEFORE,
            $this->renderPanels(...),
        );
        $this->eventDispatcher->addListener(
            MenuEvent::MENU_UPDATE,
            $this->addAdminMenuItem(...),
        );
    }

    public function registerRestRoutes(RestApiCreateEvent $event): RestApiCreateEvent
    {
        $event->addToRouteMap(
            'POST /api/ai/brief/:pid',
            function (string $pid, HttpRestRequest $request): array {
                return BriefController::default()->generate($pid, $request);
            },
        );
        $event->addToRouteMap(
            'POST /api/ai/chat/:pid',
            function (string $pid, HttpRestRequest $request): array {
                return ChatController::default()->turn($pid, $request);
            },
        );
        $event->addToRouteMap(
            'GET /api/ai/documents/recent/:pid',
            function (string $pid, HttpRestRequest $request): JsonResponse {
                return DocumentIngestionController::default()->recent($pid, $request);
            },
        );
        $event->addToRouteMap(
            'POST /api/ai/documents/ingest/:pid',
            function (string $pid, HttpRestRequest $request): JsonResponse {
                return DocumentIngestionController::default()->ingest($pid, $request);
            },
        );
        $event->addToRouteMap(
            'GET /api/ai/documents/jobs/:pid/:jobId',
            function (string $pid, string $jobId, HttpRestRequest $request): JsonResponse {
                return DocumentIngestionController::default()->job($pid, $jobId, $request);
            },
        );
        $event->addToRouteMap(
            'GET /api/ai/documents/indexed/:pid/document',
            function (string $pid, HttpRestRequest $request): JsonResponse {
                return DocumentIngestionController::default()->indexed($pid, $request);
            },
        );
        $event->addToRouteMap(
            'GET /api/ai/documents/indexed-facts/:pid/document',
            function (string $pid, HttpRestRequest $request): JsonResponse {
                return DocumentIngestionController::default()->indexedFacts($pid, $request);
            },
        );

        return $event;
    }

    public function addAdminMenuItem(MenuEvent $event): MenuEvent
    {
        $menu = $event->getMenu();

        foreach ($menu as $menuItem) {
            $isAdminMenu = ($menuItem->menu_id ?? null) === 'admimg'
                || ($menuItem->label ?? '') === 'Admin';
            if (! $isAdminMenu) {
                continue;
            }

            $item = new stdClass();
            $item->requirement = 0;
            $item->target = 'adm';
            $item->menu_id = 'aiagt0';
            $item->label = xlt('AI Observability');
            $item->url = self::ADMIN_RECENT_PATH;
            $item->children = [];
            $item->acl_req = ['admin', 'super'];
            $item->global_req = [];

            $menuItem->children[] = $item;
            break;
        }

        $event->setMenu($menu);

        return $event;
    }

    public function renderPanels(DemographicsRenderEvent $event): void
    {
        $session = SessionWrapperFactory::getInstance()->getActiveSession();
        $apiCsrfToken = CsrfUtils::collectCsrfToken($session, 'api');
        $pid = (string) $event->getPid();
        $publicPath = self::PUBLIC_PATH;

        $templatePath = self::TEMPLATE_DIR . '/patient_summary_panel.php';
        if (is_file($templatePath)) {
            include $templatePath;
        }
    }
}
