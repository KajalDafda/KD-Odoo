from odoo import models, fields, api

# Curated, known-working models per provider. Kept intentionally short - just
# enough to get someone running without guessing. For anything newer, use
# the "Custom Model Override" field instead of waiting for this list to be
# updated.
MODEL_CHOICES = {
    'anthropic': [
        ('claude-sonnet-5', 'Claude Sonnet 5 (recommended)'),
        ('claude-opus-4-8', 'Claude Opus 4.8 (highest capability)'),
        ('claude-haiku-4-5-20251001', 'Claude Haiku 4.5 (fastest/cheapest)'),
    ],
    'openai': [
        ('gpt-5.5', 'GPT-5.5 (recommended)'),
        ('gpt-5.5-pro', 'GPT-5.5 Pro (highest capability)'),
        ('gpt-5.4-mini', 'GPT-5.4 Mini (fastest/cheapest)'),
    ],
    'gemini': [
        ('gemini-3.5-flash', 'Gemini 3.5 Flash (recommended, free tier)'),
        ('gemini-3.1-flash-lite', 'Gemini 3.1 Flash Lite (fastest/cheapest, free tier)'),
        ('gemini-2.5-flash-lite', 'Gemini 2.5 Flash Lite (may be restricted on new accounts)'),
    ],
    'groq': [
        ('llama-3.3-70b-versatile', 'Llama 3.3 70B Versatile (recommended, free tier)'),
    ],
    'cerebras': [
        ('llama-3.3-70b', 'Llama 3.3 70B (recommended, free tier)'),
    ],
}


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ai_chatbot_provider = fields.Selection([
        ('anthropic', 'Anthropic (Claude) — paid, requires billing'),
        ('openai', 'OpenAI (ChatGPT) — paid, requires billing'),
        ('gemini', 'Google Gemini — free tier, no card required'),
        ('groq', 'Groq (Llama models) — free tier, no card required'),
        ('cerebras', 'Cerebras (Llama models) — free tier, no card required'),
    ], string='AI Provider',
        config_parameter='ai_chatbot.provider',
        default='gemini',
        help='Gemini, Groq, and Cerebras have permanent free tiers with no billing setup. '
             'Anthropic and OpenAI require a funded account.')

    def _selection_ai_chatbot_model(self):
        """Dynamic Selection options for the Model dropdown, filtered to the
        currently chosen Provider. Falls back to the union of every
        provider's choices when there's no record context yet, so a
        previously-saved value still displays correctly before the user
        touches the Provider field again."""
        provider = self.ai_chatbot_provider if self else False
        if provider and provider in MODEL_CHOICES:
            return MODEL_CHOICES[provider]
        seen = {}
        for choices in MODEL_CHOICES.values():
            for value, label in choices:
                seen[value] = label
        return list(seen.items())

    @api.onchange('ai_chatbot_provider')
    def _onchange_ai_chatbot_provider_reset_model(self):
        """Clear (or reset to the new provider's first option) the Model
        field whenever Provider changes, so a stale model string from a
        different provider can never be silently left selected."""
        valid_values = {v for v, _ in MODEL_CHOICES.get(self.ai_chatbot_provider, [])}
        if self.ai_chatbot_model not in valid_values:
            self.ai_chatbot_model = MODEL_CHOICES.get(self.ai_chatbot_provider, [(False, '')])[0][0]

    ai_chatbot_api_key = fields.Char(
        string='AI API Key',
        config_parameter='ai_chatbot.api_key',
        help='API key for your chosen provider. Get a free Gemini key with no card at '
             'ai.google.dev, a free Groq key at console.groq.com, or a free Cerebras key '
             'at cloud.cerebras.ai.'
    )
    ai_chatbot_api_url = fields.Char(
        string='AI API Endpoint',
        config_parameter='ai_chatbot.api_url',
        help='Leave blank to use the default endpoint for the selected provider:\n'
             'Anthropic: https://api.anthropic.com/v1/messages\n'
             'OpenAI: https://api.openai.com/v1/chat/completions\n'
             'Gemini: https://generativelanguage.googleapis.com/v1beta/openai/chat/completions\n'
             'Groq: https://api.groq.com/openai/v1/chat/completions\n'
             'Cerebras: https://api.cerebras.ai/v1/chat/completions'
    )
    ai_chatbot_model = fields.Selection(
        selection='_selection_ai_chatbot_model',
        string='Model',
        config_parameter='ai_chatbot.model',
        help='Recommended models for the selected provider. Changing Provider refreshes '
             'this list. If you need a newer model not listed yet, use "Custom Model '
             'Override" below instead.'
    )
    ai_chatbot_model_custom = fields.Char(
        string='Custom Model Override (optional)',
        config_parameter='ai_chatbot.model_custom',
        help='If set, this exact string is sent to the API instead of the Model dropdown '
             'above - use this for a newly released model that isn\'t in the list yet. '
             'Leave blank to use the Model dropdown.'
    )
    ai_chatbot_allowed_models = fields.Char(
        string='Allowed Odoo Models (optional)',
        config_parameter='ai_chatbot.allowed_models',
        help='Comma-separated technical model names the AI is allowed to read/create/'
             'write/delete, e.g. "res.partner,sale.order,product.product". '
             'Leave blank to allow any model the logged-in user already has access to. '
             'Odoo\'s normal access rights and record rules always apply either way.'
    )
