import logging
import secrets
import urllib.parse

import requests
import werkzeug

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

PROVIDER_CONFIG = {
    'github': {
        'authorize_url': 'https://github.com/login/oauth/authorize',
        'token_url': 'https://github.com/login/oauth/access_token',
        'user_url': 'https://api.github.com/user',
        'scope': 'repo read:user',
    },
    'gitlab': {
        # authorize/token URLs are built dynamically from the configured
        # instance base URL (defaults to gitlab.com) since GitLab is
        # commonly self-hosted.
        'scope': 'read_api read_user',
    },
    'bitbucket': {
        'authorize_url': 'https://bitbucket.org/site/oauth2/authorize',
        'token_url': 'https://bitbucket.org/site/oauth2/access_token',
        'user_url': 'https://api.bitbucket.org/2.0/user',
        'scope': 'repository account',
    },
}


class AiChatGitOAuthController(http.Controller):

    def _icp(self):
        """Return the system configuration parameter model."""
        return request.env['ir.config_parameter'].sudo()

    def _gitlab_base(self):
        """Return the configured GitLab base URL."""
        base = self._icp().get_param('ai_chatbot.gitlab_base_url') or 'https://gitlab.com'
        return base.rstrip('/')

    def _get_client_creds(self, provider):
        """Return the OAuth client ID and secret for the given provider."""
        icp = self._icp()
        client_id = icp.get_param(f'ai_chatbot.{provider}_client_id')
        client_secret = icp.get_param(f'ai_chatbot.{provider}_client_secret')
        return client_id, client_secret

    def _redirect_uri(self):
        """Return the OAuth callback URL."""
        base_url = self._icp().get_param('web.base.url', '')
        return f'{base_url}/ai_chatbot/git/callback'

    @http.route('/ai_chatbot/git/connect', type='http', auth='user')
    def connect(self, provider=None, **kwargs):
        """Start the OAuth authorization flow for the selected Git provider."""
        if provider not in PROVIDER_CONFIG:
            return werkzeug.wrappers.Response('Unknown Git provider.', status=400)

        client_id, client_secret = self._get_client_creds(provider)
        if not client_id or not client_secret:
            return werkzeug.wrappers.Response(
                f'{provider.capitalize()} OAuth is not configured yet. An administrator must '
                f'set the Client ID and Client Secret in Settings > AI Chatbot > Git Integration.',
                status=400,
            )

        state = secrets.token_urlsafe(24)
        # Tie the CSRF state token to this user's session so the callback can
        # verify it, without needing a separate DB table.
        request.session[f'ai_chatbot_git_oauth_state_{provider}'] = state

        redirect_uri = self._redirect_uri()

        if provider == 'github':
            authorize_url = PROVIDER_CONFIG['github']['authorize_url']
            scope = PROVIDER_CONFIG['github']['scope']
        elif provider == 'gitlab':
            authorize_url = f'{self._gitlab_base()}/oauth/authorize'
            scope = PROVIDER_CONFIG['gitlab']['scope']
        else:  # bitbucket
            authorize_url = PROVIDER_CONFIG['bitbucket']['authorize_url']
            scope = PROVIDER_CONFIG['bitbucket']['scope']

        params = {
            'client_id': client_id,
            'redirect_uri': redirect_uri,
            'scope': scope,
            'state': state,
            'response_type': 'code',
        }
        url = f'{authorize_url}?{urllib.parse.urlencode(params)}'
        return werkzeug.utils.redirect(url)

    @http.route('/ai_chatbot/git/callback', type='http', auth='user')
    def callback(self, provider=None, code=None, state=None, error=None, **kwargs):
        """Process the OAuth callback and store the user's access token."""
        if error:
            return werkzeug.wrappers.Response(f'Git connection was not completed: {error}', status=400)

        # Provider isn't always echoed back in the query string by every
        # provider, so also try to infer it from which state key matches.
        if provider not in PROVIDER_CONFIG:
            for p in PROVIDER_CONFIG:
                if request.session.get(f'ai_chatbot_git_oauth_state_{p}') == state:
                    provider = p
                    break

        if provider not in PROVIDER_CONFIG:
            return werkzeug.wrappers.Response('Unknown or missing Git provider in callback.', status=400)

        expected_state = request.session.get(f'ai_chatbot_git_oauth_state_{provider}')
        if not state or not expected_state or state != expected_state:
            return werkzeug.wrappers.Response('Invalid OAuth state - possible CSRF attempt. Please try connecting again.', status=400)
        request.session.pop(f'ai_chatbot_git_oauth_state_{provider}', None)

        if not code:
            return werkzeug.wrappers.Response('No authorization code received.', status=400)

        client_id, client_secret = self._get_client_creds(provider)
        redirect_uri = self._redirect_uri()

        try:
            token_data = self._exchange_code(provider, client_id, client_secret, code, redirect_uri)
            access_token = token_data.get('access_token')
            if not access_token:
                raise ValueError(f'No access_token in response: {token_data}')
            user_info = self._fetch_user_info(provider, access_token)
        except Exception as e:  # noqa: BLE001
            _logger.exception('Git OAuth callback failed for provider %s', provider)
            return werkzeug.wrappers.Response(f'Failed to complete Git connection: {e}', status=400)

        Account = request.env['ai.chat.git.account']
        existing = Account.search([
            ('user_id', '=', request.env.user.id), ('provider', '=', provider),
        ], limit=1)
        vals = {
            'user_id': request.env.user.id,
            'provider': provider,
            'access_token': access_token,
            'refresh_token': token_data.get('refresh_token'),
            'external_username': user_info.get('username'),
            'external_user_id': str(user_info.get('id')) if user_info.get('id') is not None else False,
            'scope': token_data.get('scope', ''),
        }
        if existing:
            existing.write(vals)
        else:
            Account.create(vals)

        return werkzeug.utils.redirect('/odoo/action-ai_chatbot.action_ai_chat_git_account')

    def _exchange_code(self, provider, client_id, client_secret, code, redirect_uri):
        """Exchange an authorization code for an OAuth access token."""
        headers = {'Accept': 'application/json'}
        data = {
            'client_id': client_id,
            'client_secret': client_secret,
            'code': code,
            'redirect_uri': redirect_uri,
            'grant_type': 'authorization_code',
        }

        if provider == 'github':
            resp = requests.post(PROVIDER_CONFIG['github']['token_url'], data=data, headers=headers, timeout=30)
        elif provider == 'gitlab':
            resp = requests.post(f'{self._gitlab_base()}/oauth/token', data=data, headers=headers, timeout=30)
        else:  # bitbucket - uses HTTP Basic auth with client_id/secret instead of body params
            resp = requests.post(
                PROVIDER_CONFIG['bitbucket']['token_url'],
                data={'grant_type': 'authorization_code', 'code': code},
                auth=(client_id, client_secret),
                headers=headers,
                timeout=30,
            )

        resp.raise_for_status()
        return resp.json()

    def _fetch_user_info(self, provider, access_token):
        """Fetch the authenticated user's profile from the Git provider."""
        headers = {'Authorization': f'Bearer {access_token}', 'Accept': 'application/json'}

        if provider == 'github':
            resp = requests.get(PROVIDER_CONFIG['github']['user_url'], headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            return {'username': data.get('login'), 'id': data.get('id')}

        if provider == 'gitlab':
            resp = requests.get(f'{self._gitlab_base()}/api/v4/user', headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            return {'username': data.get('username'), 'id': data.get('id')}

        # bitbucket
        resp = requests.get(PROVIDER_CONFIG['bitbucket']['user_url'], headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return {'username': data.get('username'), 'id': data.get('account_id')}
