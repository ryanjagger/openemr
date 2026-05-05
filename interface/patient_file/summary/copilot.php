<?php

/**
 * Patient Co-pilot — full-page AI chat for the current chart.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Ryan Jagger <jagger@fastmail.com>
 * @copyright Copyright (c) 2026 Ryan Jagger <jagger@fastmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

require_once("../../globals.php");

use OpenEMR\Common\Acl\AclMain;
use OpenEMR\Common\Csrf\CsrfUtils;
use OpenEMR\Common\Session\SessionWrapperFactory;
use OpenEMR\Core\Header;
use OpenEMR\Menu\PatientMenuRole;
use OpenEMR\OeUI\OemrUI;

$session = SessionWrapperFactory::getInstance()->getActiveSession();
$apiCsrfToken = CsrfUtils::collectCsrfToken($session, 'api');
$publicPath = $GLOBALS['webroot'] . '/interface/modules/custom_modules/oe-module-ai-agent/public';

$arrOeUiSettings = [
    'heading_title' => xl('Co-pilot'),
    'include_patient_name' => true,
    'expandable' => false,
    'expandable_files' => [],
    'action' => "",
    'action_title' => "",
    'action_href' => "",
    'show_help_icon' => false,
    'help_file_name' => "",
];
$oemr_ui = new OemrUI($arrOeUiSettings);
?>
<html>
<head>
    <title><?php echo xlt("Co-pilot"); ?></title>
    <?php Header::setupHeader('common'); ?>

    <style>
        #oe-ai-agent-chat-log {
            height: 65vh;
            min-height: 320px;
            overflow-y: auto;
            padding: 1rem;
            background: var(--white, #fff);
            border: 1px solid var(--gray-300, #dee2e6);
            border-radius: 0.25rem;
        }
    </style>
</head>
<body class="body_top">

<div id="container_div" class="<?php echo $oemr_ui->oeContainer(); ?> mt-3">
    <div class="row">
        <div class="col-sm-12">
            <?php
            if (! AclMain::aclCheckCore('patients', 'med')) {
                echo "<p>(" . xlt('Co-pilot not authorized') . ")</p>\n";
                echo "</body>\n</html>\n";
                exit();
            }
            ?>
            <?php require_once("$include_root/patient_file/summary/dashboard_header.php"); ?>
        </div>
    </div>
    <?php
    $list_id = "copilot";
    $menuPatient = new PatientMenuRole();
    $menuPatient->displayHorizNavBarMenu();
    ?>
    <div class="row">
        <div class="col-sm-12">
            <section id="oe-ai-agent-chat-panel"
                data-pid="<?php echo attr((string) $pid); ?>"
                data-csrf="<?php echo attr($apiCsrfToken); ?>">
                <div class="border rounded p-3 mb-3 bg-light" id="oe-ai-agent-doc-ingestion">
                    <div class="mb-2">
                        <strong><?php echo xlt('Recent Documents'); ?></strong>
                    </div>
                    <div id="oe-ai-agent-doc-status" class="small text-muted mb-2">
                        <?php echo xlt('Loading recent PDF/PNG documents...'); ?>
                    </div>
                    <div id="oe-ai-agent-doc-list" class="mb-2"></div>
                    <button type="button" class="btn btn-sm btn-primary d-none" id="oe-ai-agent-doc-ingest">
                        <?php echo xlt('Ingest selected documents'); ?>
                    </button>
                </div>
                <div id="oe-ai-agent-chat-log" class="mb-3">
                    <div class="text-muted small">
                        <?php echo xlt("Ask a question grounded in this patient's chart and indexed documents. The agent summarizes evidence, not treatment orders."); ?>
                    </div>
                </div>
                <form id="oe-ai-agent-chat-form" class="d-flex" autocomplete="off">
                    <input type="text" id="oe-ai-agent-chat-input"
                           class="form-control mr-2"
                           placeholder="<?php echo xla("Ask about this patient's chart..."); ?>"
                           maxlength="500" required>
                    <button type="submit" class="btn btn-primary"
                            id="oe-ai-agent-chat-send">
                        <?php echo xlt('Send'); ?>
                    </button>
                </form>
                <div class="small text-muted mt-2">
                    <?php echo xlt('Conversation is cleared when you reload or leave the chart.'); ?>
                </div>
            </section>
        </div>
    </div>
    <?php $oemr_ui->oeBelowContainerDiv(); ?>
</div>

<script src="<?php echo attr($publicPath); ?>/js/chat_panel.js?v=0.6.3"></script>

</body>
</html>
