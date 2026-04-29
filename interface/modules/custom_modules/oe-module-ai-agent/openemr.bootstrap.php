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

/**
 * @global \OpenEMR\Core\ModulesClassLoader $classLoader
 */

$classLoader->registerNamespaceIfNotExists(
    'OpenEMR\\Modules\\AiAgent\\',
    __DIR__ . DIRECTORY_SEPARATOR . 'src'
);

/**
 * @global \Symfony\Component\EventDispatcher\EventDispatcherInterface $eventDispatcher Injected by the OpenEMR module loader
 */

$bootstrap = new Bootstrap($eventDispatcher);
$bootstrap->subscribeToEvents();
