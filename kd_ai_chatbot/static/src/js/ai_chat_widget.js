/** @odoo-module **/

import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";
import { useService } from "@web/core/utils/hooks";
import { Component, useState, useRef, onWillUnmount } from "@odoo/owl";

const SpeechRecognitionCtor = window.SpeechRecognition || window.webkitSpeechRecognition;
const speechRecognitionSupported = !!SpeechRecognitionCtor;
const speechSynthesisSupported = !!window.speechSynthesis;

class AiChatWidget extends Component {
    static template = "ai_chatbot.ChatWidget";

    setup() {
        this.action = useService("action");
        this.state = useState({
            messages: [], // { role: 'user'|'assistant', content: str }
            input: "",
            conversationId: null,
            loading: false,
            isRecording: false,
            ttsEnabled: false,
            voiceSupported: speechRecognitionSupported,
        });
        this.inputRef = useRef("input");
        this.recognition = null;

        onWillUnmount(() => {
            if (this.recognition) {
                this.recognition.stop();
            }
            if (speechSynthesisSupported) {
                window.speechSynthesis.cancel();
            }
        });
    }

    async sendMessage() {
        const text = this.state.input.trim();
        if (!text || this.state.loading) return;

        this.state.messages.push({ role: "user", content: text });
        this.state.input = "";
        this.state.loading = true;

        try {
            const result = await rpc("/ai_chatbot/send", {
                conversation_id: this.state.conversationId,
                message: text,
            });
            this.state.conversationId = result.conversation_id;
            this.state.messages.push({ role: "assistant", content: result.reply });
            if (this.state.ttsEnabled) {
                this.speak(result.reply);
            }
            if (result.action) {
                // Fire-and-forget: navigate the user's screen per the AI's
                // open_view tool call. Don't block the chat UI on it.
                this.action.doAction(result.action);
            }
        } catch (e) {
            const detail =
                e?.data?.message ||
                e?.message?.data?.message ||
                e?.message ||
                "Unknown error";
            this.state.messages.push({
                role: "assistant",
                content: `⚠️ ${detail}`,
            });
        } finally {
            this.state.loading = false;
        }
    }

    onKeydown(ev) {
        if (ev.key === "Enter" && !ev.shiftKey) {
            ev.preventDefault();
            this.sendMessage();
        }
    }

    // -- Voice input (speech-to-text) ------------------------------------

    toggleRecording() {
        if (!this.state.voiceSupported) {
            this.state.messages.push({
                role: "assistant",
                content:
                    "⚠️ Voice input isn't supported in this browser. Try Chrome or Edge.",
            });
            return;
        }
        if (this.state.isRecording) {
            this.stopRecording();
        } else {
            this.startRecording();
        }
    }

    startRecording() {
        this.recognition = new SpeechRecognitionCtor();
        this.recognition.lang = navigator.language || "en-US";
        this.recognition.interimResults = false;
        this.recognition.maxAlternatives = 1;

        this.recognition.onresult = (event) => {
            const transcript = event.results[0][0].transcript;
            this.state.input = transcript;
            this.state.isRecording = false;
            // Auto-send the transcribed voice command, same as a typed message.
            this.sendMessage();
        };
        this.recognition.onerror = () => {
            this.state.isRecording = false;
        };
        this.recognition.onend = () => {
            this.state.isRecording = false;
        };

        this.state.isRecording = true;
        this.recognition.start();
    }

    stopRecording() {
        if (this.recognition) {
            this.recognition.stop();
        }
        this.state.isRecording = false;
    }

    // -- Voice output (text-to-speech) ------------------------------------

    toggleTts() {
        if (!speechSynthesisSupported) {
            this.state.messages.push({
                role: "assistant",
                content: "⚠️ Text-to-speech isn't supported in this browser.",
            });
            return;
        }
        this.state.ttsEnabled = !this.state.ttsEnabled;
        if (!this.state.ttsEnabled) {
            window.speechSynthesis.cancel();
        }
    }

    speak(text) {
        if (!speechSynthesisSupported || !text) return;
        window.speechSynthesis.cancel();
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.lang = navigator.language || "en-US";
        window.speechSynthesis.speak(utterance);
    }
}

registry.category("actions").add("ai_chatbot.chat_widget", AiChatWidget);

export default AiChatWidget;
