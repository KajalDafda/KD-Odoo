import json
import logging
import re

import requests

from odoo import models, fields, api
from odoo.exceptions import UserError, AccessError

_logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are an AI assistant embedded inside an Odoo ERP system. "
    "You can help the user with general questions, and you can also take real "
    "actions in Odoo using the tools provided to you.\n\n"
    "You have generic tools that work on ANY Odoo model (not just sale orders): "
    "search_records to look up data, create_record to make new records, "
    "write_records to update existing ones, delete_records to remove them, "
    "call_method to trigger button actions or workflow transitions, "
    "create_field and add_field_to_views to add new custom fields and surface "
    "them in views, and open_view to navigate the user's screen to a menu, a "
    "filtered list, or a specific record.\n\n"
    "Rules you must follow:\n"
    "- Always search_records first to find the correct model name and record "
    "id(s) before calling write_records, delete_records, or call_method - never "
    "guess an id.\n"
    "- NEVER assume a field exists on a model based on general knowledge, even "
    "if it sounds standard. Before using any field name you are not certain "
    "about (in a domain, in values, or when discussing it with the user), verify "
    "it first with search_records on model 'ir.model.fields', e.g. domain "
    "[['model_id.model','=','account.move'],['name','ilike','delivery']], fields "
    "['name','field_description','ttype']. On ir.model.fields: 'name' is the "
    "technical field name (e.g. 'date_order'), 'field_description' is its human "
    "label (e.g. 'Order Date'), 'model_id.model' is the technical model name to "
    "filter by - there is no field called 'field_label' or 'model'. If the field "
    "doesn't exist, say so plainly and offer to create it with create_field "
    "rather than guessing or pretending it exists. If the user gives you a "
    "human label like 'order date' instead of a technical name, search by "
    "field_description to find the technical name before using it anywhere.\n"
    "- To reposition a field that was already added to a view, call "
    "add_field_to_views again with the new anchor_field - it automatically "
    "replaces the field's previous placement in that view rather than "
    "duplicating it.\n"
    "- Use exact Odoo technical model names (e.g. 'res.partner', 'sale.order', "
    "'product.product'), not display labels.\n"
    "- delete_records is irreversible. Before calling it, explicitly ask the "
    "user to confirm which record(s) will be deleted and wait for their "
    "confirmation in a prior message.\n"
    "- create_field and add_field_to_views change the database schema and UI "
    "for all users. Before calling create_field, briefly confirm the field name, "
    "type, and target model with the user.\n"
    "- Before calling write_records or call_method on more than one record at "
    "once, briefly summarize what will change and get the user's confirmation "
    "first.\n"
    "- For requests like 'open Sales Orders', 'show draft purchase orders', or "
    "'open Sale Order S00025', use the open_view tool rather than just "
    "describing the records in text. Use intent='menu' for a known menu name, "
    "intent='list' with a domain for filtered lists (translate natural language "
    "like 'today', 'draft', 'overdue' into a proper Odoo domain), and "
    "intent='record' once you have confirmed the exact record id via "
    "search_records. If search_records finds multiple matching records, list "
    "them for the user to choose from instead of opening one arbitrarily.\n"
    "- If a tool call fails or a search finds no results, tell the user clearly "
    "instead of guessing or making up data.\n"
    "- For Git/GitHub/GitLab/Bitbucket-related requests (browsing repos, "
    "reading code, reviewing pull requests, explaining code, writing commit "
    "messages, or drafting documentation from a repo), look for tools named "
    "mcp__<server>__<tool> from an admin-connected MCP server (e.g. "
    "mcp__github__... from a connected GitHub MCP server) and use those to "
    "fetch the real content first, then reason over it yourself - there is no "
    "separate 'explain code' or 'write commit message' tool, that's just you "
    "writing text once you've read the actual file/diff. If no matching "
    "mcp__ tool is available, tell the user clearly that no Git integration "
    "is connected and that an administrator can add one via Configuration > "
    "KD Bot > MCP Servers, rather than guessing at repo contents.\n"
    "- After a tool runs, summarize the outcome for the user in plain language, "
    "including any record reference/name if one was created or changed.\n"
    "- Tools named mcp__servername__toolname come from external MCP servers "
    "an administrator has connected and work exactly like any other tool. "
    "Read each one's description to know what it does; don't assume based on "
    "the name alone.\n"
    "- If the user asks something like 'what MCP servers/tools/integrations "
    "are connected', 'what can you do', or 'what integrations do you have', "
    "answer directly from the tool list you already have access to - do NOT "
    "say you don't know or that you can't check. Group the mcp__<server>__ "
    "prefixed tools by server name (the part between the two double "
    "underscores) and list each connected server with a short summary of "
    "what its tools let you do, based on their descriptions. No tool call is "
    "needed for this - you already have this information in your own tool "
    "list for this turn.\n"
    "- GitHub's official MCP server tools (mcp__github__...) do NOT include a "
    "plain 'list my repos' tool. To list or find the current user's own "
    "repositories: first call mcp__github__get_me to learn their username "
    "(unless it's already given to you as known context below), then call "
    "the repository search tool using GitHub search syntax in the query, e.g. "
    "'user:<username>' for repos they own or contributed to, or "
    "'org:<orgname>' for an organization's repos."
)

# Canonical tool definitions, written in Anthropic's shape. Converted to
# OpenAI's shape at call time by _tools_for_provider(). Add more tools here.
TOOLS = [
    {
        "name": "search_records",
        "description": (
            "Search and read records from any Odoo model the current user has "
            "access to. Use this first to find record ids before updating, "
            "deleting, or calling a method on them."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Technical model name, e.g. 'res.partner', 'sale.order'.",
                },
                "domain": {
                    "type": "array",
                    "description": (
                        "Odoo domain as a list of [field, operator, value] triples, "
                        "e.g. [[\"name\", \"ilike\", \"Azure\"]]. Use [] for no filter."
                    ),
                    "items": {"type": "array"},
                },
                "fields": {
                    "type": "array",
                    "description": "Field names to return. Defaults to ['display_name'] if omitted.",
                    "items": {"type": "string"},
                },
                "limit": {
                    "type": "integer",
                    "description": "Max records to return, default 20, hard cap 100.",
                },
            },
            "required": ["model"],
        },
    },
    {
        "name": "create_record",
        "description": "Create a new record on any Odoo model the current user has permission to create.",
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Technical model name."},
                "values": {
                    "type": "object",
                    "description": "Field values to set, matching Odoo's create() vals dict.",
                },
            },
            "required": ["model", "values"],
        },
    },
    {
        "name": "write_records",
        "description": "Update one or more existing records on any Odoo model the current user has permission to write.",
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Technical model name."},
                "record_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "IDs of the records to update.",
                },
                "values": {
                    "type": "object",
                    "description": "Field values to set on all given records.",
                },
            },
            "required": ["model", "record_ids", "values"],
        },
    },
    {
        "name": "delete_records",
        "description": (
            "Permanently delete one or more records from any Odoo model. "
            "This is irreversible - only call this after the user has explicitly "
            "confirmed which record(s) to delete."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Technical model name."},
                "record_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "IDs of the records to delete.",
                },
            },
            "required": ["model", "record_ids"],
        },
    },
    {
        "name": "call_method",
        "description": (
            "Call a public method on one or more records of an Odoo model. Use this "
            "for button actions and workflow transitions (e.g. 'action_confirm' on "
            "sale.order, 'action_cancel', 'method_direct_trigger' on ir.cron to run "
            "a cron job now). Do NOT use this for create, write, or delete - use the "
            "dedicated tools for those instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Technical model name."},
                "record_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "IDs of the records to call the method on.",
                },
                "method": {
                    "type": "string",
                    "description": "Public method name, e.g. 'action_confirm'.",
                },
                "args": {
                    "type": "array",
                    "description": "Optional positional arguments to pass to the method.",
                    "items": {},
                },
            },
            "required": ["model", "record_ids", "method"],
        },
    },
    {
        "name": "create_sale_order",
        "description": (
            "Convenience shortcut to create a draft sale order (quotation) for a "
            "customer by name with product-name order lines, without needing to "
            "look up partner/product ids yourself first. Prefer this over "
            "create_record for sale orders."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_name": {
                    "type": "string",
                    "description": "Name of the customer/contact as it appears in Odoo.",
                },
                "order_lines": {
                    "type": "array",
                    "description": "Products to add to the order.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "product_name": {
                                "type": "string",
                                "description": "Name of the product as it appears in Odoo.",
                            },
                            "quantity": {
                                "type": "number",
                                "description": "Quantity to order.",
                            },
                        },
                        "required": ["product_name", "quantity"],
                    },
                },
            },
            "required": ["customer_name", "order_lines"],
        },
    },
    {
        "name": "create_field",
        "description": (
            "Create a new custom field on any Odoo model. The field name will be "
            "auto-prefixed with 'x_' if not already, following Odoo's convention "
            "for safely-removable custom fields. This changes the database schema."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Technical model name, e.g. 'account.move'."},
                "field_name": {"type": "string", "description": "Technical field name, e.g. 'delivery_date'."},
                "field_label": {"type": "string", "description": "Human-readable label shown in the UI."},
                "field_type": {
                    "type": "string",
                    "enum": ["char", "text", "html", "integer", "float", "boolean",
                             "date", "datetime", "many2one", "many2many", "selection"],
                    "description": "Field data type.",
                },
                "relation": {
                    "type": "string",
                    "description": "Target model technical name. Required for many2one/many2many.",
                },
                "selection_options": {
                    "type": "array",
                    "description": "Required for field_type 'selection'. List of {value, label} pairs.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "value": {"type": "string"},
                            "label": {"type": "string"},
                        },
                        "required": ["value", "label"],
                    },
                },
                "required_field": {"type": "boolean", "description": "Whether the field is mandatory. Default false."},
                "help": {"type": "string", "description": "Optional tooltip/help text."},
            },
            "required": ["model", "field_name", "field_label", "field_type"],
        },
    },
    {
        "name": "add_field_to_views",
        "description": (
            "Make an existing field visible in one or more view types for a model "
            "(form, list, kanban, calendar, pivot, graph, activity). Creates a new "
            "inherited view for each type rather than modifying the original view. "
            "Note: form and list placement is reliable; kanban/calendar/pivot/graph/"
            "activity only get the field's data made available at the view root - "
            "exact visual placement in those (e.g. inside a kanban card) may still "
            "need manual template adjustment."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Technical model name."},
                "field_name": {"type": "string", "description": "Technical field name. Must already exist."},
                "view_types": {
                    "type": "array",
                    "description": "Which view types to add the field to. Defaults to all 7 if omitted.",
                    "items": {
                        "type": "string",
                        "enum": ["form", "list", "kanban", "calendar", "pivot", "graph", "activity"],
                    },
                },
                "anchor_field": {
                    "type": "string",
                    "description": "For the form view, an existing field name to place the new field after. Optional.",
                },
            },
            "required": ["model", "field_name"],
        },
    },
    {
        "name": "open_view",
        "description": (
            "Navigate the user's Odoo screen to a menu, a filtered list of records, "
            "or one specific record's form view. Use this whenever the user asks to "
            "open, show, or navigate to something, rather than just describing it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "enum": ["menu", "list", "record"],
                    "description": (
                        "'menu' to open a known Odoo menu by name (e.g. 'Sales Orders', "
                        "'Invoices', 'Customers'). 'list' to open a filtered list view "
                        "of a model. 'record' to open one specific record's form view."
                    ),
                },
                "menu_name": {"type": "string", "description": "Menu label. Required when intent is 'menu'."},
                "model": {"type": "string", "description": "Technical model name. Required for 'list' and 'record'."},
                "domain": {
                    "type": "array",
                    "description": "Odoo domain filter for intent 'list'. Use [] for no filter.",
                    "items": {"type": "array"},
                },
                "record_id": {
                    "type": "integer",
                    "description": (
                        "ID of the exact record to open, required for intent 'record'. "
                        "Find it via search_records first - never guess it."
                    ),
                },
            },
            "required": ["intent"],
        },
    },
]

MAX_TOOL_ITERATIONS = 8
MAX_SEARCH_LIMIT = 100
DEFAULT_SEARCH_LIMIT = 20

# Methods that must go through the dedicated create/write/delete tools instead
# of the generic call_method tool, plus private/internal methods.
CALL_METHOD_BLOCKLIST = {
    'create', 'write', 'unlink', 'copy', 'search', 'search_read', 'search_count',
    'read', 'fields_get', 'sudo', 'with_user', 'with_context', 'with_company',
    'check_access_rights', 'check_access_rule',
}

ALLOWED_FIELD_TYPES = {
    'char', 'text', 'html', 'integer', 'float', 'boolean',
    'date', 'datetime', 'many2one', 'many2many', 'selection',
}

ALL_VIEW_TYPES = ['form', 'list', 'kanban', 'calendar', 'pivot', 'graph', 'activity']

DEFAULT_ENDPOINTS = {
    'anthropic': 'https://api.anthropic.com/v1/messages',
    'openai': 'https://api.openai.com/v1/chat/completions',
    'gemini': 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions',
    'groq': 'https://api.groq.com/openai/v1/chat/completions',
    'cerebras': 'https://api.cerebras.ai/v1/chat/completions',
}

# Providers that speak the OpenAI Chat Completions format (vs. Anthropic's own format).
OPENAI_COMPATIBLE_PROVIDERS = {'openai', 'gemini', 'groq', 'cerebras'}


class AiChatConversation(models.Model):
    _name = 'ai.chat.conversation'
    _description = 'AI Chat Conversation'
    _order = 'write_date desc'

    name = fields.Char(default='New Conversation')
    user_id = fields.Many2one('res.users', default=lambda self: self.env.user, required=True)
    message_ids = fields.One2many('ai.chat.message', 'conversation_id', string='Messages')

    def action_send_message(self, body):
        """Add a user message, run the AI (with tool-calling loop), store and
        return the reply text plus an optional client-side navigation action
        (set when the AI called open_view)."""
        self.ensure_one()
        self.env['ai.chat.message'].create({
            'conversation_id': self.id,
            'role': 'user',
            'content': body,
        })
        reply_text, nav_action = self._run_ai_turn()
        reply = self.env['ai.chat.message'].create({
            'conversation_id': self.id,
            'role': 'assistant',
            'content': reply_text,
        })
        return {'text': reply.content, 'action': nav_action}

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    def _get_config(self):
        """Read the active AI provider, API key, endpoint, and model from
        system parameters. Raises UserError if the key or model is missing."""
        icp = self.env['ir.config_parameter'].sudo()
        provider = icp.get_param('ai_kdchatbot.provider', 'anthropic')
        api_key = icp.get_param('ai_kdchatbot.api_key')
        api_url = icp.get_param('ai_kdchatbot.api_url') or DEFAULT_ENDPOINTS.get(provider)
        # Custom Model Override always wins if set, so a newer model not yet
        # in the dropdown can still be used without a module update.
        model = icp.get_param('ai_kdchatbot.model_custom') or icp.get_param('ai_kdchatbot.model')

        if not api_key:
            raise UserError('Please configure the AI API key in Settings > AI Chatbot.')
        if not model:
            raise UserError('Please configure a Model in Settings > AI Chatbot.')

        return {'provider': provider, 'api_key': api_key, 'api_url': api_url, 'model': model}

    def _check_model_allowed(self, model_name):
        """Optional admin-configured allowlist restricting which models the AI
        may touch at all, on top of Odoo's normal access rights. If the
        ai_kdchatbot.allowed_models system parameter is empty, all models the
        current user can access are permitted.
        """
        if model_name not in self.env:
            raise UserError(f'Unknown model: {model_name}')

        icp = self.env['ir.config_parameter'].sudo()
        allowlist_raw = icp.get_param('ai_kdchatbot.allowed_models', '')
        allowlist = {m.strip() for m in allowlist_raw.split(',') if m.strip()}
        if allowlist and model_name not in allowlist:
            raise UserError(
                f'The model "{model_name}" is not in the AI Chatbot allowed models list. '
                f'An administrator can adjust this in Settings > AI Chatbot.'
            )

    def _tools_for_provider(self, provider, tool_defs):
        """Convert a list of tool defs from Anthropic's native shape into
        whichever wire format the target provider expects. OpenAI-compatible
        providers (OpenAI, Gemini, Groq, Cerebras) need each tool wrapped in
        a {'type': 'function', 'function': {...}} envelope; Anthropic takes
        the tool defs as-is."""
        if provider in OPENAI_COMPATIBLE_PROVIDERS:
            return [
                {
                    'type': 'function',
                    'function': {
                        'name': t['name'],
                        'description': t['description'],
                        'parameters': t['input_schema'],
                    },
                }
                for t in tool_defs
            ]
        return tool_defs

    # ------------------------------------------------------------------
    # AI turn / tool-calling loop
    # ------------------------------------------------------------------

    def _run_ai_turn(self):
        """Run one user turn to completion, handling any number of tool calls
        the model makes along the way. Returns (text, nav_action) - nav_action
        is set only if a tool (currently open_view) produced one, and is None
        otherwise.
        """
        self.ensure_one()
        config = self._get_config()
        provider = config['provider']

        history = [
            {'role': m.role, 'content': m.content}
            for m in self.message_ids.sorted('id')
        ]

        mcp_tool_defs, mcp_index, mcp_context = self._get_mcp_tool_defs()
        all_tools = TOOLS + mcp_tool_defs
        system_prompt = SYSTEM_PROMPT + mcp_context

        if provider in OPENAI_COMPATIBLE_PROVIDERS:
            return self._run_turn_openai(config, history, all_tools, mcp_index, system_prompt)
        return self._run_turn_anthropic(config, history, all_tools, mcp_index, system_prompt)

    # -- Anthropic ------------------------------------------------------

    def _run_turn_anthropic(self, config, history, all_tools, mcp_index, system_prompt):
        """Run the Anthropic-flavored tool-calling loop: call the API, and
        while it keeps returning tool_use blocks, execute each tool and feed
        the results back, up to MAX_TOOL_ITERATIONS rounds. Returns
        (final_text, nav_action)."""
        messages = list(history)
        tools = self._tools_for_provider('anthropic', all_tools)
        nav_action = None

        for _iteration in range(MAX_TOOL_ITERATIONS):
            data = self._call_anthropic(config, messages, tools, system_prompt)
            content_blocks = data.get('content', [])
            stop_reason = data.get('stop_reason')

            text_parts = [b.get('text', '') for b in content_blocks if b.get('type') == 'text']
            tool_use_blocks = [b for b in content_blocks if b.get('type') == 'tool_use']

            if stop_reason != 'tool_use' or not tool_use_blocks:
                text = '\n'.join(t for t in text_parts if t) or 'No response received.'
                return text, nav_action

            messages.append({'role': 'assistant', 'content': content_blocks})

            tool_result_blocks = []
            for tool_use in tool_use_blocks:
                result = self._execute_tool(tool_use.get('name'), tool_use.get('input') or {}, mcp_index)
                if result.get('action'):
                    nav_action = result['action']
                tool_result_blocks.append({
                    'type': 'tool_result',
                    'tool_use_id': tool_use.get('id'),
                    'content': json.dumps(result),
                })
            messages.append({'role': 'user', 'content': tool_result_blocks})

        return "I wasn't able to finish that after several tool attempts. Could you rephrase your request?", nav_action

    def _call_anthropic(self, config, messages, tools, system_prompt):
        """Single request to Anthropic's /v1/messages endpoint."""
        headers = {
            'Content-Type': 'application/json',
            'x-api-key': config['api_key'],
            'anthropic-version': '2023-06-01',
        }
        payload = {
            'model': config['model'],
            'max_tokens': 1024,
            'system': system_prompt,
            'tools': tools,
            'messages': messages,
        }
        return self._post(config['api_url'], headers, payload)

    # -- OpenAI -----------------------------------------------------------

    def _run_turn_openai(self, config, history, all_tools, mcp_index, system_prompt):
        """Run the OpenAI-compatible tool-calling loop (used for OpenAI,
        Gemini, Groq, Cerebras - they all speak the Chat Completions format).
        While the model keeps returning tool_calls, execute each and feed
        results back, up to MAX_TOOL_ITERATIONS rounds. Returns
        (final_text, nav_action)."""
        messages = [{'role': 'system', 'content': system_prompt}] + list(history)
        tools = self._tools_for_provider('openai', all_tools)
        nav_action = None

        for _iteration in range(MAX_TOOL_ITERATIONS):
            data = self._call_openai(config, messages, tools)
            choice = (data.get('choices') or [{}])[0]
            message = choice.get('message', {})
            finish_reason = choice.get('finish_reason')
            tool_calls = message.get('tool_calls') or []

            if finish_reason != 'tool_calls' or not tool_calls:
                text = message.get('content') or 'No response received.'
                return text, nav_action

            # Replay the assistant's tool-call message back verbatim so the
            # API can match tool results to calls by id.
            messages.append({
                'role': 'assistant',
                'content': message.get('content'),
                'tool_calls': tool_calls,
            })

            for call in tool_calls:
                fn = call.get('function', {})
                try:
                    args = json.loads(fn.get('arguments') or '{}')
                except json.JSONDecodeError:
                    args = {}
                result = self._execute_tool(fn.get('name'), args, mcp_index)
                if result.get('action'):
                    nav_action = result['action']
                messages.append({
                    'role': 'tool',
                    'tool_call_id': call.get('id'),
                    'content': json.dumps(result),
                })

        return "I wasn't able to finish that after several tool attempts. Could you rephrase your request?", nav_action

    def _call_openai(self, config, messages, tools):
        """Single request to an OpenAI-compatible Chat Completions endpoint
        (OpenAI, Gemini, Groq, or Cerebras, depending on config['api_url'])."""
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f"Bearer {config['api_key']}",
        }
        payload = {
            'model': config['model'],
            'messages': messages,
            'tools': tools,
        }
        return self._post(config['api_url'], headers, payload)

    # -- Shared HTTP ------------------------------------------------------

    def _post(self, url, headers, payload):
        """Shared JSON POST helper for both provider families. Raises
        UserError with a readable message (including the response body when
        available) on any HTTP/network failure, since this bubbles straight
        up to the chat UI."""
        try:
            response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=60)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            _logger.error('AI Chatbot API call failed: %s', e)
            detail = ''
            if getattr(e, 'response', None) is not None:
                try:
                    detail = f" - {e.response.text}"
                except Exception:
                    pass
            raise UserError(f'Failed to reach the AI service: {e}{detail}')

    # ------------------------------------------------------------------
    # Tool execution (provider-agnostic)
    # ------------------------------------------------------------------

    def _execute_tool(self, name, tool_input, mcp_index=None):
        """Dispatch a tool call by name. Always returns a JSON-serializable
        dict; never raises, so the model can see and react to failures.
        Runs under the current user's own permissions (no sudo), so Odoo's
        normal access rights and record rules are respected. MCP tools
        (name starting with 'mcp__') are dispatched via mcp_index, a
        {composite_name: (server_id, original_tool_name)} map built fresh
        each turn by _get_mcp_tool_defs() - never cached on self.
        """
        self.ensure_one()

        if mcp_index and name in mcp_index:
            server_id, original_name = mcp_index[name]
            try:
                return self._tool_call_mcp(server_id, original_name, tool_input)
            except Exception as e:  # noqa: BLE001
                _logger.exception('MCP tool %s failed', name)
                return {'success': False, 'error': str(e)}

        handlers = {
            'search_records': self._tool_search_records,
            'create_record': self._tool_create_record,
            'write_records': self._tool_write_records,
            'delete_records': self._tool_delete_records,
            'call_method': self._tool_call_method,
            'create_sale_order': self._tool_create_sale_order,
            'create_field': self._tool_create_field,
            'add_field_to_views': self._tool_add_field_to_views,
            'open_view': self._tool_open_view,
        }
        handler = handlers.get(name)
        if not handler:
            return {'success': False, 'error': f'Unknown tool: {name}'}
        try:
            return handler(tool_input)
        except AccessError as e:
            return {'success': False, 'error': f'Permission denied: {e}'}
        except Exception as e:  # noqa: BLE001 - report to the model, don't crash the turn
            _logger.exception('Tool %s failed', name)
            return {'success': False, 'error': str(e)}

    def _tool_search_records(self, tool_input):
        """AI tool: search_records. Reads records from any allowed Odoo model
        via search_read, respecting the caller's own access rights."""
        model_name = (tool_input.get('model') or '').strip()
        domain = tool_input.get('domain') or []
        fields_list = tool_input.get('fields') or ['display_name']
        limit = tool_input.get('limit') or DEFAULT_SEARCH_LIMIT
        limit = min(int(limit), MAX_SEARCH_LIMIT)

        self._check_model_allowed(model_name)

        Model = self.env[model_name]
        records = Model.search_read(domain, fields_list, limit=limit)
        return {
            'success': True,
            'model': model_name,
            'count': len(records),
            'records': records,
        }

    def _tool_create_record(self, tool_input):
        """AI tool: create_record. Creates a record on any allowed Odoo
        model, respecting the caller's own access rights."""
        model_name = (tool_input.get('model') or '').strip()
        values = tool_input.get('values') or {}

        if not values:
            return {'success': False, 'error': 'No values provided.'}

        self._check_model_allowed(model_name)

        record = self.env[model_name].create(values)
        return {
            'success': True,
            'model': model_name,
            'id': record.id,
            'display_name': record.display_name,
        }

    def _tool_write_records(self, tool_input):
        """AI tool: write_records. Updates one or more existing records on
        any allowed Odoo model, respecting the caller's own access rights."""
        model_name = (tool_input.get('model') or '').strip()
        record_ids = tool_input.get('record_ids') or []
        values = tool_input.get('values') or {}

        if not record_ids:
            return {'success': False, 'error': 'No record_ids provided.'}
        if not values:
            return {'success': False, 'error': 'No values provided.'}

        self._check_model_allowed(model_name)

        records = self.env[model_name].browse(record_ids).exists()
        if not records:
            return {'success': False, 'error': f'None of the given ids exist on {model_name}.'}

        records.write(values)
        return {
            'success': True,
            'model': model_name,
            'updated_ids': records.ids,
            'count': len(records),
        }

    def _tool_delete_records(self, tool_input):
        """AI tool: delete_records. Permanently deletes one or more records
        on any allowed Odoo model. Irreversible - the system prompt instructs
        the AI to confirm with the user before calling this."""
        model_name = (tool_input.get('model') or '').strip()
        record_ids = tool_input.get('record_ids') or []

        if not record_ids:
            return {'success': False, 'error': 'No record_ids provided.'}

        self._check_model_allowed(model_name)

        records = self.env[model_name].browse(record_ids).exists()
        if not records:
            return {'success': False, 'error': f'None of the given ids exist on {model_name}.'}

        deleted_ids = records.ids
        records.unlink()
        return {
            'success': True,
            'model': model_name,
            'deleted_ids': deleted_ids,
            'count': len(deleted_ids),
        }

    def _tool_call_method(self, tool_input):
        """AI tool: call_method. Invokes a public, non-CRUD method on a
        model or recordset (button actions, workflow transitions, etc.).
        Blocks private methods (leading underscore) and CRUD-equivalent
        methods that have their own dedicated tools (see
        CALL_METHOD_BLOCKLIST)."""
        model_name = (tool_input.get('model') or '').strip()
        record_ids = tool_input.get('record_ids') or []
        method_name = (tool_input.get('method') or '').strip()
        args = tool_input.get('args') or []

        if not method_name:
            return {'success': False, 'error': 'No method provided.'}
        if method_name.startswith('_'):
            return {'success': False, 'error': f'Method "{method_name}" is private and cannot be called.'}
        if method_name in CALL_METHOD_BLOCKLIST:
            return {
                'success': False,
                'error': f'Method "{method_name}" must be called via the dedicated '
                         f'create_record/write_records/delete_records/search_records tools instead.',
            }

        self._check_model_allowed(model_name)

        Model = self.env[model_name]
        if record_ids:
            recordset = Model.browse(record_ids).exists()
            if not recordset:
                return {'success': False, 'error': f'None of the given ids exist on {model_name}.'}
        else:
            recordset = Model

        method = getattr(recordset, method_name, None)
        if method is None or not callable(method):
            return {'success': False, 'error': f'Method "{method_name}" does not exist on {model_name}.'}

        result = method(*args)
        # Result may be an action dict, a recordset, True/False, etc. Make it JSON-safe.
        return {
            'success': True,
            'model': model_name,
            'method': method_name,
            'record_ids': recordset.ids,
            'result': self._jsonable(result),
        }

    def _jsonable(self, value):
        """Best-effort conversion of arbitrary Odoo method return values into
        something JSON-serializable, so it can be sent back to the AI."""
        if isinstance(value, models.BaseModel):
            return {'model': value._name, 'ids': value.ids}
        if isinstance(value, (dict, list, str, int, float, bool)) or value is None:
            try:
                json.dumps(value)
                return value
            except TypeError:
                return str(value)
        return str(value)

    def _tool_create_sale_order(self, tool_input):
        """AI tool: create_sale_order. Convenience shortcut that resolves a
        customer name and product names to their Odoo records and creates a
        draft sale.order, without the AI needing to look up ids itself first."""
        customer_name = (tool_input.get('customer_name') or '').strip()
        order_lines_input = tool_input.get('order_lines') or []

        if not customer_name:
            return {'success': False, 'error': 'No customer_name provided.'}
        if not order_lines_input:
            return {'success': False, 'error': 'No order_lines provided.'}

        Partner = self.env['res.partner']
        partner = Partner.search([('name', 'ilike', customer_name)], limit=1)
        if not partner:
            return {
                'success': False,
                'error': f'No contact found matching "{customer_name}". '
                         f'Ask the user to confirm the exact customer name.',
            }

        Product = self.env['product.product']
        order_line_commands = []
        missing_products = []
        for line in order_lines_input:
            product_name = (line.get('product_name') or '').strip()
            quantity = line.get('quantity') or 0
            product = Product.search([('name', 'ilike', product_name)], limit=1)
            if not product:
                missing_products.append(product_name)
                continue
            order_line_commands.append((0, 0, {
                'product_id': product.id,
                'product_uom_qty': quantity,
            }))

        if missing_products:
            return {
                'success': False,
                'error': f'Could not find these product(s) in Odoo: {", ".join(missing_products)}. '
                         f'Ask the user to confirm exact product names.',
            }

        SaleOrder = self.env['sale.order']
        order = SaleOrder.create({
            'partner_id': partner.id,
            'order_line': order_line_commands,
        })

        return {
            'success': True,
            'order_id': order.id,
            'order_reference': order.name,
            'customer': partner.name,
            'amount_total': order.amount_total,
            'line_count': len(order_line_commands),
        }

    # ------------------------------------------------------------------
    # Field / view management
    # ------------------------------------------------------------------

    def _tool_create_field(self, tool_input):
        """AI tool: create_field. Adds a new custom field (ir.model.fields)
        to any allowed Odoo model, auto-prefixed 'x_' per Odoo convention.
        Changes the database schema."""
        model_name = (tool_input.get('model') or '').strip()
        field_name = (tool_input.get('field_name') or '').strip()
        field_label = (tool_input.get('field_label') or field_name).strip()
        field_type = (tool_input.get('field_type') or '').strip()
        relation = (tool_input.get('relation') or '').strip()
        selection_options = tool_input.get('selection_options') or []
        required = bool(tool_input.get('required_field') or False)
        help_text = tool_input.get('help') or ''

        if not model_name or not field_name or not field_type:
            return {'success': False, 'error': 'model, field_name, and field_type are required.'}
        if field_type not in ALLOWED_FIELD_TYPES:
            return {
                'success': False,
                'error': f'Unsupported field_type "{field_type}". '
                         f'Allowed: {", ".join(sorted(ALLOWED_FIELD_TYPES))}.',
            }

        self._check_model_allowed(model_name)

        # Odoo convention: custom fields added outside a module are prefixed
        # x_ so they're clearly identifiable and safely removable.
        if not field_name.startswith('x_'):
            field_name = 'x_' + field_name

        target_model = self.env['ir.model'].search([('model', '=', model_name)], limit=1)
        if not target_model:
            return {'success': False, 'error': f'Model "{model_name}" not found in ir.model.'}

        existing = self.env['ir.model.fields'].search([
            ('model_id', '=', target_model.id), ('name', '=', field_name),
        ], limit=1)
        if existing:
            return {'success': False, 'error': f'Field "{field_name}" already exists on {model_name}.'}

        vals = {
            'name': field_name,
            'model_id': target_model.id,
            'field_description': field_label,
            'ttype': field_type,
            'required': required,
            'help': help_text,
            'state': 'manual',
        }

        if field_type in ('many2one', 'many2many'):
            if not relation:
                return {'success': False, 'error': f'relation is required for field_type "{field_type}".'}
            if relation not in self.env:
                return {'success': False, 'error': f'Unknown relation model: {relation}'}
            vals['relation'] = relation

        if field_type == 'selection':
            if not selection_options:
                return {'success': False, 'error': 'selection_options is required for field_type "selection".'}
            pairs = [(str(o.get('value')), str(o.get('label'))) for o in selection_options]
            vals['selection'] = str(pairs)

        new_field = self.env['ir.model.fields'].create(vals)
        return {
            'success': True,
            'model': model_name,
            'field_name': field_name,
            'field_label': field_label,
            'field_type': field_type,
            'field_id': new_field.id,
        }

    def _tool_add_field_to_views(self, tool_input):
        """AI tool: add_field_to_views. Surfaces an existing field in one or
        more view types by creating a new inherited ir.ui.view per type
        (never modifies the original view). Loops _add_field_to_single_view
        per requested view type, collecting per-type results so one failure
        doesn't abort the rest."""
        model_name = (tool_input.get('model') or '').strip()
        field_name = (tool_input.get('field_name') or '').strip()
        view_types = tool_input.get('view_types') or ALL_VIEW_TYPES
        anchor_field = (tool_input.get('anchor_field') or '').strip()

        if not model_name or not field_name:
            return {'success': False, 'error': 'model and field_name are required.'}

        self._check_model_allowed(model_name)

        field_exists = self.env['ir.model.fields'].search_count([
            ('model_id.model', '=', model_name), ('name', '=', field_name),
        ])
        if not field_exists:
            return {
                'success': False,
                'error': f'Field "{field_name}" does not exist on {model_name}. '
                         f'Create it first with create_field.',
            }

        results = {}
        for vtype in view_types:
            try:
                results[vtype] = self._add_field_to_single_view(model_name, field_name, vtype, anchor_field)
            except Exception as e:  # noqa: BLE001 - report per-view failure, don't abort the batch
                _logger.exception('add_field_to_views failed for %s/%s', model_name, vtype)
                results[vtype] = {'success': False, 'error': str(e)}

        return {'success': True, 'model': model_name, 'field_name': field_name, 'results': results}

    def _add_field_to_single_view(self, model_name, field_name, view_type, anchor_field):
        """Create one inherited view adding field_name into the given
        view_type's primary view. If a prior AI-created placement for this
        exact (model, field, view_type) exists, it's replaced rather than
        duplicated - this is what makes 'move this field after X' work as a
        clean reposition instead of stacking two copies of the field."""
        odoo_view_type = 'list' if view_type == 'tree' else view_type

        base_view = self.env['ir.ui.view'].search([
            ('model', '=', model_name),
            ('type', '=', odoo_view_type),
            ('mode', '=', 'primary'),
        ], limit=1, order='priority')
        if not base_view:
            return {'success': False, 'error': f'No primary {view_type} view found for {model_name}.'}

        if view_type == 'form':
            if anchor_field:
                xpath_expr = f"//field[@name='{anchor_field}']"
                position = 'after'
                inner = f'<field name="{field_name}"/>'
            else:
                xpath_expr = "//sheet"
                position = 'inside'
                inner = f'<group><field name="{field_name}"/></group>'
        elif view_type in ('list', 'tree'):
            root_tag = 'list' if '<list' in (base_view.arch or '') else 'tree'
            xpath_expr = f"//{root_tag}"
            position = 'inside'
            inner = f'<field name="{field_name}"/>'
        else:
            # kanban, calendar, pivot, graph, activity: this makes the field's
            # data available at the view root. It does NOT guarantee visual
            # placement (e.g. inside a kanban card body, or marked as a pivot
            # measure) - that requires template-specific knowledge this tool
            # doesn't have.
            xpath_expr = f"//{odoo_view_type}"
            position = 'inside'
            inner = f'<field name="{field_name}"/>'

        arch = f'<data><xpath expr="{xpath_expr}" position="{position}">{inner}</xpath></data>'

        view_name = f'{model_name}.{view_type}.ai.{field_name}'

        # If we already placed this field in this view type before (e.g. the
        # user is repositioning it), remove that previous placement first so
        # the field doesn't end up appearing twice.
        previous_view = self.env['ir.ui.view'].search([('name', '=', view_name)], limit=1)
        replaced_previous = bool(previous_view)
        if previous_view:
            previous_view.unlink()

        new_view = self.env['ir.ui.view'].create({
            'name': view_name,
            'model': model_name,
            'inherit_id': base_view.id,
            'type': odoo_view_type,
            'arch': arch,
        })
        return {
            'success': True,
            'view_id': new_view.id,
            'inherited_from': base_view.id,
            'repositioned': replaced_previous,
        }

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _tool_open_view(self, tool_input):
        """AI tool: open_view. Resolves a navigation request (a named menu,
        a filtered list of a model, or one specific record) into a client
        action dict, returned inside the result under 'action' so the
        calling loop can pick it up and hand it to the frontend for a real
        screen navigation via doAction()."""
        intent = tool_input.get('intent')

        if intent == 'menu':
            menu_name = (tool_input.get('menu_name') or '').strip()
            if not menu_name:
                return {'success': False, 'error': 'menu_name is required for intent "menu".'}

            menus = self.env['ir.ui.menu'].search([
                ('name', 'ilike', menu_name), ('action', '!=', False),
            ], limit=5)
            if not menus:
                return {'success': False, 'error': f'No menu found matching "{menu_name}".'}
            if len(menus) > 1:
                return {
                    'success': False,
                    'error': 'multiple_matches',
                    'matches': [{'id': m.id, 'name': m.complete_name} for m in menus],
                }

            menu = menus[0]
            action_ref = menu.action
            if not action_ref:
                return {'success': False, 'error': f'Menu "{menu.name}" has no action to open.'}

            action = {'type': action_ref._name, 'id': action_ref.id}
            return {'success': True, 'opened': 'menu', 'menu': menu.complete_name, 'action': action}

        if intent == 'list':
            model_name = (tool_input.get('model') or '').strip()
            domain = tool_input.get('domain') or []
            if not model_name:
                return {'success': False, 'error': 'model is required for intent "list".'}

            self._check_model_allowed(model_name)

            count = self.env[model_name].search_count(domain)
            action = {
                'type': 'ir.actions.act_window',
                'name': f'AI: {model_name}',
                'res_model': model_name,
                'view_mode': 'list,form',
                'views': [[False, 'list'], [False, 'form']],
                'domain': domain,
                'target': 'current',
            }
            return {
                'success': True, 'opened': 'list', 'model': model_name,
                'match_count': count, 'action': action,
            }

        if intent == 'record':
            model_name = (tool_input.get('model') or '').strip()
            record_id = tool_input.get('record_id')
            if not model_name or not record_id:
                return {'success': False, 'error': 'model and record_id are required for intent "record".'}

            self._check_model_allowed(model_name)

            record = self.env[model_name].browse(record_id).exists()
            if not record:
                return {'success': False, 'error': f'No record {record_id} found on {model_name}.'}

            action = {
                'type': 'ir.actions.act_window',
                'name': record.display_name,
                'res_model': model_name,
                'view_mode': 'form',
                'views': [[False, 'form']],
                'res_id': record.id,
                'target': 'current',
            }
            return {
                'success': True,
                'opened': 'record',
                'model': model_name,
                'record_id': record.id,
                'display_name': record.display_name,
                'action': action,
            }

        return {'success': False, 'error': f'Unknown intent: {intent}'}

    # ------------------------------------------------------------------
    # MCP (Model Context Protocol) client
    #
    # Supports remote MCP servers over the Streamable HTTP transport only.
    # stdio-based local MCP servers are NOT supported - spawning subprocesses
    # from inside an Odoo web request isn't practical or safe. Session
    # negotiation is re-done on every call (no session reuse across HTTP
    # requests) for simplicity; this is less efficient than a persistent
    # connection but avoids holding server-side session state across
    # Odoo's stateless request/response cycle.
    # ------------------------------------------------------------------

    def _get_mcp_tool_defs(self):
        """Discover tools from all enabled MCP servers. Returns
        (tool_defs, index, context_block) where tool_defs is a list in the
        same shape as TOOLS (ready to merge and send to the AI provider),
        index maps each composite tool name back to (server_id,
        original_tool_name) for dispatch, and context_block is a string of
        admin-provided facts (e.g. "GitHub username: myuser") to append to
        the system prompt so the AI doesn't need a lookup tool call to learn
        them every turn. Never cached on self - rebuilt fresh each turn and
        passed through return values only, since Odoo recordsets don't
        reliably support scratch instance attributes.
        """
        servers = self.env['ai.chat.mcp.server'].sudo().search([('enabled', '=', True)])
        tool_defs = []
        index = {}
        context_lines = []

        for server in servers:
            tools, error = self._mcp_list_tools(server)
            if error:
                _logger.warning('MCP server "%s" tool discovery failed: %s', server.name, error)
                continue

            slug = re.sub(r'[^a-zA-Z0-9_]', '_', server.name.lower()).strip('_')[:24] or f'srv{server.id}'
            for t in tools:
                original_name = t.get('name')
                if not original_name:
                    continue
                composite_name = f'mcp__{slug}__{original_name}'[:64]
                tool_defs.append({
                    'name': composite_name,
                    'description': f'[{server.name}] {t.get("description") or ""}'.strip(),
                    'input_schema': t.get('inputSchema') or {'type': 'object', 'properties': {}},
                })
                index[composite_name] = (server.id, original_name)

            if server.context_note and server.context_note.strip():
                context_lines.append(f'{server.name}: {server.context_note.strip()}')

        context_block = ''
        if context_lines:
            context_block = (
                '\n\nKnown context about connected MCP servers (use this directly instead '
                'of calling a lookup tool to rediscover it):\n' + '\n'.join(f'- {line}' for line in context_lines)
            )

        return tool_defs, index, context_block


    def _mcp_headers(self, server, session_id=None):
        """Build the HTTP headers for an MCP JSON-RPC request: content
        negotiation, optional Bearer auth, and the session id header once
        one has been issued by the server."""
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json, text/event-stream',
        }
        if server.auth_token:
            headers['Authorization'] = f'Bearer {server.auth_token}'
        if session_id:
            headers['Mcp-Session-Id'] = session_id
        return headers

    def _mcp_parse_response(self, resp):
        """Parse a Streamable HTTP MCP response - either a plain JSON body
        or a text/event-stream carrying one or more 'data: {json}' lines."""
        content_type = resp.headers.get('Content-Type', '')
        if 'text/event-stream' in content_type:
            last_data = None
            for line in resp.text.splitlines():
                line = line.strip()
                if line.startswith('data:'):
                    last_data = line[len('data:'):].strip()
            if not last_data:
                raise ValueError('No data received in MCP server SSE response.')
            return json.loads(last_data)
        return resp.json()

    def _mcp_call(self, server, method, params=None, session_id=None, is_notification=False):
        """Single JSON-RPC 2.0 request to an MCP server. Returns
        (parsed_response_or_None, session_id)."""
        headers = self._mcp_headers(server, session_id)
        payload = {'jsonrpc': '2.0', 'method': method}
        if params is not None:
            payload['params'] = params
        if not is_notification:
            payload['id'] = 1

        resp = requests.post(server.url, headers=headers, data=json.dumps(payload), timeout=30)
        resp.raise_for_status()
        new_session_id = resp.headers.get('Mcp-Session-Id', session_id)

        if is_notification or not resp.content:
            return None, new_session_id
        return self._mcp_parse_response(resp), new_session_id

    def _mcp_initialize(self, server):
        """MCP initialize handshake. Returns a session_id (may be None if
        the server doesn't use one)."""
        init_params = {
            'protocolVersion': '2025-06-18',
            'capabilities': {},
            'clientInfo': {'name': 'kd-bot-odoo', 'version': '1.0'},
        }
        data, session_id = self._mcp_call(server, 'initialize', init_params)
        if data and data.get('error'):
            raise ValueError(data['error'].get('message', 'MCP initialize failed'))
        # Required by the MCP spec before any further requests are valid.
        self._mcp_call(server, 'notifications/initialized', {}, session_id=session_id, is_notification=True)
        return session_id

    def _mcp_list_tools(self, server):
        """Returns (tools_list, error_string_or_None)."""
        try:
            session_id = self._mcp_initialize(server)
            data, _ = self._mcp_call(server, 'tools/list', {}, session_id=session_id)
            if data.get('error'):
                return [], data['error'].get('message', 'tools/list failed')
            tools = (data.get('result') or {}).get('tools') or []
            return tools, None
        except Exception as e:  # noqa: BLE001
            return [], str(e)

    def _tool_call_mcp(self, server_id, tool_name, tool_input):
        """Dispatch target for any mcp__<server>__<tool> composite tool name
        (see _get_mcp_tool_defs / _execute_tool). Runs the MCP initialize
        handshake fresh, then tools/call, and normalizes the response's
        content blocks into the same {'success', ...} shape as every other
        tool result."""
        server = self.env['ai.chat.mcp.server'].sudo().browse(server_id)
        if not server.exists() or not server.enabled:
            return {'success': False, 'error': 'This MCP server is no longer available or was disabled.'}

        try:
            session_id = self._mcp_initialize(server)
            data, _ = self._mcp_call(
                server, 'tools/call', {'name': tool_name, 'arguments': tool_input}, session_id=session_id,
            )
            if data.get('error'):
                return {'success': False, 'error': data['error'].get('message', 'MCP tool call failed')}

            result = data.get('result') or {}
            content_blocks = result.get('content') or []
            text_parts = [b.get('text', '') for b in content_blocks if b.get('type') == 'text']

            return {
                'success': not result.get('isError', False),
                'server': server.name,
                'tool': tool_name,
                'output': '\n'.join(text_parts) if text_parts else result,
            }
        except requests.exceptions.RequestException as e:
            return {'success': False, 'error': f'MCP request to {server.name} failed: {e}'}


class AiChatMessage(models.Model):
    _name = 'ai.chat.message'
    _description = 'AI Chat Message'
    _order = 'id asc'

    conversation_id = fields.Many2one('ai.chat.conversation', required=True, ondelete='cascade')
    role = fields.Selection([
        ('user', 'User'),
        ('assistant', 'Assistant'),
    ], required=True, default='user')
    content = fields.Text(required=True)
    create_date = fields.Datetime(readonly=True)
