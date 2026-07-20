import logging
import secrets
import urllib.parse

import requests
import werkzeug

from odoo import http, fields
from odoo.http import request

_logger = logging.getLogger(__name__)

SESSION_KEY = 'ai_kdchatbot_mcp_oauth_states'


class AiChatMcpOAuthController(http.Controller):

    def _redirect_uri(self):
        """The callback URL registered on the provider's side - must exactly
        match what's shown in the server's read-only oauth_redirect_uri field."""
        base_url = request.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        return f'{base_url}/ai_kdchatbot/mcp/oauth/callback'

    @http.route('/ai_kdchatbot/mcp/oauth/connect', type='http', auth='user')
    def connect(self, server_id=None, **kwargs):
        """Step 1 of the OAuth flow. Generates a CSRF state token (tied to
        this user's session and the target server id), then redirects the
        browser to the provider's own authorize URL."""
        server = request.env['ai.chat.mcp.server'].sudo().browse(int(server_id or 0))
        if not server.exists():
            return werkzeug.wrappers.Response('Unknown MCP server.', status=400)
        if not server.oauth_authorize_url or not server.oauth_client_id:
            return werkzeug.wrappers.Response(
                'This server is missing OAuth Authorize URL or Client ID. '
                'Fill those in on the server record first.', status=400,
            )

        state = secrets.token_urlsafe(24)
        pending = request.session.get(SESSION_KEY) or {}
        pending[state] = server.id
        request.session[SESSION_KEY] = pending

        params = {
            'client_id': server.oauth_client_id,
            'redirect_uri': self._redirect_uri(),
            'response_type': 'code',
            'state': state,
        }
        if server.oauth_scope:
            params['scope'] = server.oauth_scope

        url = f'{server.oauth_authorize_url}?{urllib.parse.urlencode(params)}'
        return werkzeug.utils.redirect(url)

    @http.route('/ai_kdchatbot/mcp/oauth/callback', type='http', auth='user')
    def callback(self, code=None, state=None, error=None, **kwargs):
        """Step 2 of the OAuth flow. Validates the CSRF state, exchanges the
        authorization code for an access token, and writes it straight into
        the server's auth_token field - the same field a manually-pasted
        PAT would use, so nothing downstream needs to know which path was
        used to obtain it."""
        if error:
            return werkzeug.wrappers.Response(f'OAuth connection was not completed: {error}', status=400)

        pending = request.session.get(SESSION_KEY) or {}
        server_id = pending.pop(state, None) if state else None
        request.session[SESSION_KEY] = pending

        if not server_id:
            return werkzeug.wrappers.Response(
                'Invalid or expired OAuth state - possible CSRF attempt, or you took too '
                'long. Please try connecting again.', status=400,
            )

        server = request.env['ai.chat.mcp.server'].sudo().browse(server_id)
        if not server.exists():
            return werkzeug.wrappers.Response('This MCP server no longer exists.', status=400)

        if not code:
            return werkzeug.wrappers.Response('No authorization code received.', status=400)

        try:
            data = {
                'client_id': server.oauth_client_id,
                'client_secret': server.oauth_client_secret,
                'code': code,
                'redirect_uri': self._redirect_uri(),
                'grant_type': 'authorization_code',
            }
            resp = requests.post(
                server.oauth_token_url, data=data,
                headers={'Accept': 'application/json'}, timeout=30,
            )
            resp.raise_for_status()
            token_data = resp.json()
            access_token = token_data.get('access_token')
            if not access_token:
                raise ValueError(f'No access_token in response: {token_data}')
        except Exception as e:  # noqa: BLE001
            _logger.exception('MCP OAuth callback failed for server %s', server.name)
            return werkzeug.wrappers.Response(f'Failed to complete OAuth connection: {e}', status=400)

        server.write({
            'auth_token': access_token,
            'oauth_connected_at': fields.Datetime.now(),
        })

        return werkzeug.utils.redirect('/odoo/action-ai_kdchatbot.action_ai_chat_mcp_server')
