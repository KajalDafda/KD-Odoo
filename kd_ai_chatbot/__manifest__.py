{
    'name': 'KD AI Chatbot',
    'version': '19.0.14.0.0',
    'category': 'Productivity',
    'summary': 'AI-powered chatbot integrated into Odoo, with MCP integration',
    'description': """
AI Chatbot for Odoo
====================
Lets users chat with an AI assistant (Claude, OpenAI, or any LLM API)
directly inside Odoo. Conversations are stored per user and can be
reviewed later.

Admins can connect remote MCP servers (Streamable HTTP transport) to give
the AI new dynamically-discovered tools - e.g. GitHub's official remote
MCP server, Slack, or any other MCP-compatible integration.

Configure your API key and endpoint in Settings > Technical > AI Chatbot Config.
""",
    'author': 'KD',
    'depends': ['base', 'mail', 'web', 'sale'],
    'data': [
        'security/ir.model.access.csv',
        'views/ai_chat_views.xml',
        'views/ai_chat_mcp_views.xml',
        'views/res_config_settings_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'ai_chatbot/static/src/js/ai_chat_widget.js',
            'ai_chatbot/static/src/xml/ai_chat_widget.xml',
            'ai_chatbot/static/src/css/ai_chat_widget.css',
            'ai_chatbot/static/src/js/ai_chat_floating_button.js',
            'ai_chatbot/static/src/xml/ai_chat_floating_button.xml',
            'ai_chatbot/static/src/css/ai_chat_floating_button.css',
        ],
    },
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
