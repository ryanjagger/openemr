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
use OpenEMR\Modules\AiAgent\Controller\BriefController;
use OpenEMR\Modules\AiAgent\Controller\ChatController;
use Symfony\Component\EventDispatcher\EventDispatcherInterface;

final class Bootstrap
{
    private const TEMPLATE_DIR = __DIR__ . '/../templates';
    private const PUBLIC_PATH = '/interface/modules/custom_modules/oe-module-ai-agent/public';

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

        return $event;
    }

    public function renderPanels(DemographicsRenderEvent $event): void
    {
        $session = SessionWrapperFactory::getInstance()->getActiveSession();
        $apiCsrfToken = CsrfUtils::collectCsrfToken($session, 'api');
        $pid = (string) $event->getPid();
        $publicPath = self::PUBLIC_PATH;

        foreach (['patient_summary_panel.php', 'chat_panel.php'] as $template) {
            $templatePath = self::TEMPLATE_DIR . '/' . $template;
            if (!is_file($templatePath)) {
                continue;
            }
            include $templatePath;
        }
    }
}
