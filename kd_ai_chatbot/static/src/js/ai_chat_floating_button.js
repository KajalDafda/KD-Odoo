/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, useState, onMounted, onWillUnmount } from "@odoo/owl";
import AiChatWidget from "./ai_chat_widget";

class AiChatFloatingButton extends Component {
    static template = "ai_chatbot.FloatingButton";
    static components = { AiChatWidget };

    setup() {
        this.action = useService("action");
        // "mounted": the AiChatWidget subcomponent is created the first time
        // the panel is opened, and then kept alive (hidden via CSS, not
        // destroyed via t-if) so minimizing never loses the conversation.
        this.state = useState({ open: false, mounted: false, menuOpen: false });

        this._onWindowClick = () => {
            this.state.menuOpen = false;
        };
        onMounted(() => window.addEventListener("click", this._onWindowClick));
        onWillUnmount(() => window.removeEventListener("click", this._onWindowClick));
    }

    toggle() {
        // Fab button: open if closed/minimized, minimize if currently open.
        if (this.state.open) {
            this.minimize();
        } else {
            this.state.open = true;
            this.state.mounted = true;
            this.state.menuOpen = false;
        }
    }

    minimize() {
        // Hides the panel but keeps AiChatWidget mounted - conversation
        // (messages, conversation id) is preserved for next time.
        this.state.open = false;
        this.state.menuOpen = false;
    }

    close() {
        // Fully unmounts AiChatWidget, so the next open starts a fresh
        // conversation - distinct from minimize on purpose.
        this.state.open = false;
        this.state.mounted = false;
        this.state.menuOpen = false;
    }

    toggleMenu(ev) {
        ev.stopPropagation();
        this.state.menuOpen = !this.state.menuOpen;
    }

    openSettings() {
        this.state.menuOpen = false;
        this.action.doAction('ai_chatbot.action_kd_bot_settings');
    }

    openHistory() {
        this.state.menuOpen = false;
        this.action.doAction('ai_chatbot.action_ai_chat_conversation');
    }
}

// Registering in "main_components" makes this render once, globally, inside
// the web client shell - it stays mounted across every screen/menu change,
// unlike a client action which only exists while its own menu is open.
registry.category("main_components").add("ai_chatbot.FloatingButton", {
    Component: AiChatFloatingButton,
});

export default AiChatFloatingButton;
