-- @package   OpenEMR
-- @link      https://www.open-emr.org
-- @author    Ryan Jagger <jagger@fastmail.com>
-- @copyright Copyright (c) 2026 Ryan Jagger <jagger@fastmail.com>
-- @license   GNU General Public License 3
--
-- Module install script for oe-module-ai-agent.
-- Idempotent — safe to run multiple times.

-- ---------------------------------------------------------------------
-- Internal OAuth2 client used by BearerTokenMinter to issue user-scoped
-- short-lived access tokens that the Python sidecar uses to call the
-- OpenEMR FHIR API on the user's behalf. Public client (no secret) —
-- the trust boundary is enforced by INTERNAL_AUTH_SECRET on the sidecar
-- and by the user's session having already authenticated.
-- ---------------------------------------------------------------------
INSERT INTO `oauth_clients` (
    `client_id`,
    `client_role`,
    `client_name`,
    `client_secret`,
    `redirect_uri`,
    `grant_types`,
    `scope`,
    `is_confidential`,
    `is_enabled`,
    `register_date`
) VALUES (
    'oe-module-ai-agent-internal',
    'users',
    'OpenEMR AI Agent (Internal)',
    NULL,
    '',
    '',
    'openid api:fhir user/Patient.read user/Condition.read user/MedicationRequest.read user/AllergyIntolerance.read user/Encounter.read user/Observation.read user/DocumentReference.read',
    0,
    1,
    NOW()
)
ON DUPLICATE KEY UPDATE
    `is_enabled` = 1,
    `scope` = VALUES(`scope`);
