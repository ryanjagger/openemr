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

use DateInterval;
use DateTimeImmutable;
use League\OAuth2\Server\CryptKey;
use OpenEMR\Common\Auth\OAuth2KeyConfig;
use OpenEMR\Common\Auth\OpenIDConnect\Entities\ScopeEntity;
use OpenEMR\Common\Auth\OpenIDConnect\Repositories\AccessTokenRepository;
use OpenEMR\Common\Auth\OpenIDConnect\Repositories\ClientRepository;
use OpenEMR\Common\Session\SessionWrapperFactory;
use OpenEMR\FHIR\Config\ServerConfig;
use OpenEMR\Services\TrustedUserService;
use RuntimeException;

/**
 * Mints short-lived user-scoped OAuth2 access tokens for the Python sidecar
 * to use against the OpenEMR FHIR API. The sidecar's reads inherit the
 * user's ACL automatically — no system-level scope expansion happens here.
 */
final class BearerTokenMinter
{
    public const CLIENT_ID = 'oe-module-ai-agent-internal';
    private const TOKEN_TTL = 'PT5M';

    /** @var list<string> */
    public const FHIR_READ_SCOPES = [
        'user/Patient.read',
        'user/Appointment.read',
        'user/CarePlan.read',
        'user/Condition.read',
        'user/MedicationRequest.read',
        'user/AllergyIntolerance.read',
        'user/Encounter.read',
        'user/Goal.read',
        'user/Observation.read',
        'user/DocumentReference.read',
        'user/ServiceRequest.read',
        'user/Procedure.read',
        'user/Immunization.read',
    ];

    public function __construct(
        private readonly ClientRepository $clientRepository,
        private readonly AccessTokenRepository $accessTokenRepository,
        private readonly OAuth2KeyConfig $keyConfig,
        private readonly ServerConfig $serverConfig,
        private readonly TrustedUserService $trustedUserService,
    ) {
    }

    public static function default(): self
    {
        $session = SessionWrapperFactory::getInstance()->getActiveSession();
        $serverConfig = new ServerConfig();
        $accessTokenRepository = new AccessTokenRepository($serverConfig, $session);
        $keyConfig = new OAuth2KeyConfig();
        $keyConfig->configKeyPairs();

        return new self(
            new ClientRepository(),
            $accessTokenRepository,
            $keyConfig,
            $serverConfig,
            new TrustedUserService(),
        );
    }

    /**
     * @param list<string> $scopes
     */
    public function mintForUser(string $userUuid, array $scopes): string
    {
        $client = $this->clientRepository->getClientEntity(self::CLIENT_ID);
        if ($client === false) {
            throw new RuntimeException(
                'AI Agent OAuth client missing — run sql/install.sql for oe-module-ai-agent.',
            );
        }

        $scopeEntities = array_map(
            static fn (string $scope): ScopeEntity => ScopeEntity::createFromString($scope),
            $scopes,
        );

        $accessToken = $this->accessTokenRepository->getNewToken($client, $scopeEntities, $userUuid);
        $accessToken->setExpiryDateTime(
            (new DateTimeImmutable())->add(new DateInterval(self::TOKEN_TTL)),
        );
        $tokenId = bin2hex(random_bytes(20));
        $accessToken->setIdentifier($tokenId);
        $accessToken->setIssuer($this->serverConfig->getOauthAuthorizationUrl());
        $accessToken->setPrivateKey(
            new CryptKey($this->keyConfig->getPrivateKeyLocation(), $this->keyConfig->getPassPhrase()),
        );

        $this->accessTokenRepository->persistNewAccessToken($accessToken);

        // BearerTokenAuthorizationStrategy::isTrustedUser requires a non-empty
        // session_cache row in oauth_trusted_user before the token is accepted
        // on FHIR routes. Stamp a marker so the per-mint trust row exists.
        $this->trustedUserService->saveTrustedUser(
            self::CLIENT_ID,
            $userUuid,
            $scopes,
            0,
            '',
            'oe-module-ai-agent:' . $tokenId,
            'client_credentials',
        );

        return (string) $accessToken;
    }
}
