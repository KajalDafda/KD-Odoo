from odoo import http
from odoo.http import request


class AiChatController(http.Controller):

    @http.route('/ai_chatbot/send', type='jsonrpc', auth='user')
    def send_message(self, conversation_id=None, message=''):
        """JSON-RPC endpoint called by the chat widget on every message.
        Creates a new conversation if conversation_id is empty, otherwise
        continues an existing one. Returns the AI's reply text plus an
        optional navigation action for the widget to execute via
        actionService.doAction()."""
        Conversation = request.env['ai.chat.conversation']
        if conversation_id:
            conv = Conversation.browse(conversation_id)
        else:
            conv = Conversation.create({
                'name': message[:50] or 'New Conversation',
                'user_id': request.env.user.id,
            })
        result = conv.action_send_message(message)
        return {
            'conversation_id': conv.id,
            'reply': result.get('text'),
            'action': result.get('action'),
        }
