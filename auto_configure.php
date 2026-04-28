<?php

declare(strict_types=1);

/**
 * Automatic install script invoked by the openemr/openemr Docker image
 * entrypoint. Reads MYSQL_* and OE_* env vars and runs the installer
 * if the site has not yet been configured.
 *
 * Replaces the version baked into older Docker images, which calls
 * Installer with the pre-8.x single-argument signature.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

require_once __DIR__ . '/vendor/autoload.php';

use OpenEMR\BC\ServiceContainer;

$siteSqlConf = __DIR__ . '/sites/default/sqlconf.php';
if (file_exists($siteSqlConf) && filesize($siteSqlConf) > 0) {
    fwrite(STDOUT, "OpenEMR already configured; skipping auto_configure.\n");
    exit(0);
}

$installSettings = [
    'iuser'     => getenv('OE_USER') ?: 'admin',
    'iuserpass' => getenv('OE_PASS') ?: 'pass',
    'iuname'    => 'Administrator',
    'iufname'   => 'OpenEMR',
    'igroup'    => 'Default',
    'server'    => getenv('MYSQL_HOST') ?: 'localhost',
    'loginhost' => '%',
    'port'      => getenv('MYSQL_PORT') ?: '3306',
    'root'      => getenv('MYSQL_ROOT_USER') ?: 'root',
    'rootpass'  => getenv('MYSQL_ROOT_PASS') ?: '',
    'login'     => getenv('MYSQL_USER') ?: 'openemr',
    'pass'      => getenv('MYSQL_PASS') ?: 'openemr',
    'dbname'    => getenv('MYSQL_DATABASE') ?: 'openemr',
    'collate'   => 'utf8mb4_general_ci',
    'site'      => 'default',
];

$installer = new Installer($installSettings, ServiceContainer::getLogger());

if (!$installer->quick_install()) {
    fwrite(STDERR, "auto_configure failed: " . $installer->error_message . "\n");
    exit(1);
}

fwrite(STDOUT, $installer->debug_message . "\n");
exit(0);
