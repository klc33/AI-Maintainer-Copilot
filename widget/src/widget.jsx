// widget.jsx — embedded chat widget (Preact + vanilla CSS).
//
// Boot sequence:
//   1. The embed page (served by api /widget/{id}/embed) injects widget_id,
//      pre-fetched config, and a session JWT onto window, then dispatches
//      the 'mc:ready' event.
//   2. mountWidget() reads window.__MC_CONFIG__, applies theme variables to
//      #mc-widget-root, and renders <Widget />.
//   3. On every open/close + on first mount the widget posts an mc:layout
//      message to the parent loader iframe so it can resize/move itself.
import { render } from 'preact';
import { useState, useRef, useEffect } from 'preact/hooks';
// ?inline tells Vite to give us the compiled CSS as a string at build time
// (instead of emitting a separate .css file). We inject it into the document
// at mount, so the entire widget ships as one widget.js file.
import cssText from './widget.css?inline';

// Iframe dimensions for collapsed (bubble only) and expanded (panel) states.
const COLLAPSED = { width: 96,  height: 96  };  // bubble 56 + 20 margin x2
const EXPANDED  = { width: 400, height: 600 };  // panel 360x560 + 20 margin x2
const CHAT_PATH = '/widget/chat';

// ── SVG icons (no external assets so the bundle stays single-file) ──
const IconChat = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>
  </svg>
);
const IconClose = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
    <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
  </svg>
);
const IconSend = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>
  </svg>
);
const IconBot = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <rect x="3" y="11" width="18" height="10" rx="2"/>
    <circle cx="12" cy="5" r="2"/><path d="M12 7v4"/>
    <line x1="8" y1="16" x2="8" y2="16"/><line x1="16" y1="16" x2="16" y2="16"/>
  </svg>
);

function postLayout({ width, height, position }) {
  try {
    window.parent.postMessage({ type: 'mc:layout', width, height, position }, '*');
  } catch (e) { /* no parent in standalone dev mode */ }
}

function injectStyles() {
  if (document.getElementById('mc-widget-styles')) return;
  const style = document.createElement('style');
  style.id = 'mc-widget-styles';
  style.textContent = cssText;
  document.head.appendChild(style);
}

function applyTheme(theme = {}) {
  const root = document.getElementById('mc-widget-root');
  if (!root) return;
  if (theme.color) root.style.setProperty('--mc-color', theme.color);
}

function positionClass(theme = {}) {
  return theme.position === 'bottom-left' ? 'mc-pos-bottom-left' : 'mc-pos-bottom-right';
}

function Widget({ config }) {
  const theme = config?.theme || {};
  const greeting = theme.greeting || '';
  const name = config?.name || 'Chat';

  const [open, setOpen] = useState(false);
  const [msg, setMsg] = useState('');
  const [messages, setMessages] = useState(
    greeting ? [{ role: 'assistant', content: greeting }] : []
  );
  const [loading, setLoading] = useState(false);
  // True while we're waiting for the first token from the server (nothing
  // streamed yet) — drives the typing-dots indicator.
  const [awaiting, setAwaiting] = useState(false);

  const messagesRef = useRef(null);
  const inputRef = useRef(null);

  // Tell the parent iframe to resize + pin to the configured corner.
  useEffect(() => {
    const dims = open ? EXPANDED : COLLAPSED;
    postLayout({
      width: dims.width,
      height: dims.height,
      position: theme.position === 'bottom-left' ? 'bottom-left' : 'bottom-right',
    });
  }, [open, theme.position]);

  // Auto-focus the input whenever the panel opens.
  useEffect(() => {
    if (open && inputRef.current) inputRef.current.focus();
  }, [open]);

  // Auto-scroll to bottom on new messages.
  useEffect(() => {
    const el = messagesRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, awaiting]);

  const sendMessage = async () => {
    const text = msg.trim();
    if (!text || loading) return;
    setMsg('');
    setMessages(prev => [...prev, { role: 'user', content: text }]);
    setLoading(true);
    setAwaiting(true);

    let assistantMsg = '';
    const appendAssistant = (chunk) => {
      assistantMsg += chunk;
      setAwaiting(false); // first token arrived
      setMessages(prev => {
        const last = prev[prev.length - 1];
        if (last && last.role === 'assistant' && last.streaming) {
          return [...prev.slice(0, -1), { ...last, content: assistantMsg }];
        }
        return [...prev, { role: 'assistant', content: assistantMsg, streaming: true }];
      });
    };

    try {
      const resp = await fetch(CHAT_PATH, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${window.__MC_SESSION_TOKEN__}`,
        },
        body: JSON.stringify({
          message: text,
          conversation_id: 'widget_session',
        }),
      });
      if (!resp.ok || !resp.body) {
        appendAssistant(`(error: ${resp.status})`);
      } else {
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buf = '';
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          let nl;
          while ((nl = buf.indexOf('\n\n')) !== -1) {
            const frame = buf.slice(0, nl);
            buf = buf.slice(nl + 2);
            if (!frame.startsWith('data: ')) continue;
            try {
              const evt = JSON.parse(frame.slice(6));
              if (evt.type === 'token') appendAssistant(evt.content);
              else if (evt.type === 'tool_call_start') {
                setMessages(prev => [...prev, { role: 'tool', content: `Using ${evt.name}…` }]);
              }
            } catch (_) { /* malformed frame */ }
          }
        }
      }
    } catch (e) {
      appendAssistant(`(network error: ${e.message})`);
    } finally {
      setMessages(prev => prev.map(m => m.streaming ? { ...m, streaming: false } : m));
      setLoading(false);
      setAwaiting(false);
    }
  };

  // ── Collapsed bubble ─────────────────────────────────
  if (!open) {
    return (
      <div class={`mc-root ${positionClass(theme)}`}>
        <button class="mc-bubble" onClick={() => setOpen(true)} aria-label="Open chat">
          <IconChat />
        </button>
      </div>
    );
  }

  // ── Expanded panel ───────────────────────────────────
  return (
    <div class={`mc-root ${positionClass(theme)}`}>
      <div class="mc-panel" role="dialog" aria-label={name}>
        <div class="mc-header">
          <div class="mc-avatar"><IconBot /></div>
          <div class="mc-header-text">
            <div class="mc-title">{name}</div>
            <div class="mc-status">Online · typically replies instantly</div>
          </div>
          <button class="mc-close" onClick={() => setOpen(false)} aria-label="Close chat">
            <IconClose />
          </button>
        </div>

        <div class="mc-messages" ref={messagesRef}>
          {messages.map((m, i) => {
            if (m.role === 'tool') {
              return <div key={i} class="mc-tool-note">{m.content}</div>;
            }
            return (
              <div key={i} class={`mc-msg-row ${m.role}`}>
                <div class={`mc-msg ${m.role}`}>{m.content}</div>
              </div>
            );
          })}
          {awaiting && (
            <div class="mc-msg-row assistant">
              <div class="mc-typing" aria-label="Assistant is typing">
                <span></span><span></span><span></span>
              </div>
            </div>
          )}
        </div>

        <div class="mc-inputbar">
          <input
            class="mc-input"
            ref={inputRef}
            type="text"
            value={msg}
            onInput={e => setMsg(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && sendMessage()}
            placeholder="Ask a question…"
            disabled={loading}
            aria-label="Message"
          />
          <button
            class="mc-send"
            onClick={sendMessage}
            disabled={loading || !msg.trim()}
            aria-label="Send message"
          >
            <IconSend />
          </button>
        </div>

        <div class="mc-footer">Powered by Maintainer's Copilot</div>
      </div>
    </div>
  );
}

function mountWidget() {
  const root = document.getElementById('mc-widget-root');
  if (!root) return;
  injectStyles();
  const config = window.__MC_CONFIG__ || { name: 'Chat', theme: {} };
  applyTheme(config.theme || {});
  render(<Widget config={config} />, root);
}

// The embed page dispatches mc:ready once config + session are loaded.
// If we somehow miss the event (script raced), poll briefly and mount once
// __MC_CONFIG__ is available; mount blind after 2s as a last resort.
if (window.__MC_CONFIG__) {
  mountWidget();
} else {
  let mounted = false;
  const mountOnce = () => { if (!mounted) { mounted = true; mountWidget(); } };
  window.addEventListener('mc:ready', mountOnce);
  let waited = 0;
  const tick = setInterval(() => {
    waited += 100;
    if (window.__MC_CONFIG__) { clearInterval(tick); mountOnce(); }
    else if (waited >= 2000) { clearInterval(tick); mountOnce(); }
  }, 100);
}
