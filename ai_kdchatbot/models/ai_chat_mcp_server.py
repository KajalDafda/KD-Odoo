import logging

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AiChatMcpServer(models.Model):
    _name = 'ai.chat.mcp.server'
    _description = 'AI Chatbot MCP Server Connection'
    _order = 'sequence, id'

    name = fields.Char(required=True, help='Display name, e.g. "GitHub Remote MCP".')
    sequence = fields.Integer(default=10)
    url = fields.Char(
        required=True,
        help='HTTP(S) endpoint of the remote MCP server (Streamable HTTP transport). '
             'stdio-based local MCP servers are not supported.'
    )
    # Deliberately excluded from all views (see views/ai_chat_mcp_views.xml) so
    # the token is never rendered in the UI.
    auth_token = fields.Char(
        help='Bearer token sent as the Authorization header. Either paste one '
             'manually (e.g. a GitHub PAT), or fill in the OAuth fields below and '
             'use "Connect via OAuth" to obtain one automatically.'
    )
    enabled = fields.Boolean(default=True)

    context_note = fields.Text(
        string='Context for the AI',
        help='Optional facts the AI should already know about this server, so it doesn\'t '
             'need to call a lookup tool every time. Example for GitHub: '
             '"GitHub username: myusername". One fact per line if you have more than one.'
    )

    last_tested = fields.Datetime(readonly=True)
    last_test_result = fields.Text(readonly=True)
    last_tool_count = fields.Integer(readonly=True)

    # -- Generic OAuth 2.0 (authorization code flow) ----------------------
    # Optional. Fill these in for providers that require real OAuth instead
    # of a static token (Google, Slack, Notion, Linear, etc.). Get the
    # authorize/token URLs and register the redirect URI from this server's
    # own developer console - the exact steps differ per provider.
    oauth_authorize_url = fields.Char(
        string='OAuth Authorize URL',
        help='The provider\'s OAuth authorization endpoint, e.g. '
             'https://accounts.google.com/o/oauth2/v2/auth'
    )
    oauth_token_url = fields.Char(
        string='OAuth Token URL',
        help='The provider\'s OAuth token exchange endpoint, e.g. '
             'https://oauth2.googleapis.com/token'
    )
    oauth_client_id = fields.Char(string='OAuth Client ID')
    oauth_client_secret = fields.Char(string='OAuth Client Secret')
    oauth_scope = fields.Char(
        string='OAuth Scope',
        help='Space-separated scopes to request, e.g. '
             '"https://www.googleapis.com/auth/drive.readonly"'
    )
    oauth_redirect_uri = fields.Char(
        string='OAuth Redirect URI (read-only)',
        compute='_compute_oauth_redirect_uri',
        help='Register this exact URL as the app\'s "Authorized redirect URI" on the '
             'provider\'s developer console before connecting.'
    )
    oauth_connected_at = fields.Datetime(readonly=True)

    @api.depends_context('uid')
    def _compute_oauth_redirect_uri(self):
        """Show the exact callback URL to register on the provider's
        developer console as the app's authorized redirect URI."""
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        for rec in self:
            rec.oauth_redirect_uri = f'{base_url}/ai_kdchatbot/mcp/oauth/callback'

    def action_connect_oauth(self):
        """Header button: kick off the generic OAuth 2.0 authorization-code
        flow for this server by redirecting the browser to our connect
        controller, which in turn redirects to the provider's own
        authorize URL."""
        self.ensure_one()
        if not self.oauth_authorize_url or not self.oauth_client_id:
            raise UserError(
                'Fill in at least OAuth Authorize URL and OAuth Client ID before connecting.'
            )
        return {
            'type': 'ir.actions.act_url',
            'url': f'/ai_kdchatbot/mcp/oauth/connect?server_id={self.id}',
            'target': 'self',
        }

    def action_test_connection(self):
        """Header button: run the real MCP handshake (initialize +
        tools/list) against this server and record the outcome - either the
        full discovered tool list with descriptions, or the error - so an
        admin can verify the connection without leaving the form."""
        self.ensure_one()
        Conversation = self.env['ai.chat.conversation']
        tools, error = Conversation._mcp_list_tools(self)
        self.last_tested = fields.Datetime.now()
        if error:
            self.last_test_result = f'Failed: {error}'
            self.last_tool_count = 0
        else:
            lines = [f'OK - found {len(tools)} tool(s):', '']
            for t in tools:
                desc = (t.get('description') or '').strip().split('\n')[0][:120]
                lines.append(f"- {t.get('name', '?')}: {desc}")
            self.last_test_result = '\n'.join(lines)
            self.last_tool_count = len(tools)
        return {'type': 'ir.actions.act_window_close'}
