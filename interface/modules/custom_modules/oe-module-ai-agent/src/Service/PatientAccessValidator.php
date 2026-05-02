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

use OpenEMR\Common\Acl\AclMain;
use OpenEMR\Common\Database\QueryUtils;

/**
 * Closes the audit's HIGH `pid` ACL finding (interface/globals.php:757-761)
 * for any agent-initiated read by performing the ownership check that the
 * core code skips when storing $_GET['pid'] into session.
 *
 * Applies the same checks demographics_full.php uses to gate visibility:
 *  1. The user has the `patients`/`demo`/`view` ACL.
 *  2. The patient's squad (if any) is in the user's allowed `squads`.
 */
final class PatientAccessValidator
{
    public function canRead(string $pid): bool
    {
        if (!ctype_digit($pid) || $pid === '0') {
            return false;
        }

        // Mirror demographics.php's chart-view gate (interface/patient_file/summary/demographics.php:1053):
        // call without a return_value so any allow-row on patients/demo (view, write, addonly, ...) grants
        // read. Requiring the literal `view` row excluded groups like Physicians whose ACL row carries
        // `write` instead, blocking every non-admin user.
        if (!AclMain::aclCheckCore('patients', 'demo')) {
            return false;
        }

        $row = QueryUtils::fetchRecords(
            'SELECT squad FROM patient_data WHERE pid = ? LIMIT 1',
            [$pid],
            true,
        );
        if ($row === []) {
            return false;
        }

        $squad = (string) ($row[0]['squad'] ?? '');
        if ($squad !== '' && !AclMain::aclCheckCore('squads', $squad)) {
            return false;
        }

        return true;
    }
}
