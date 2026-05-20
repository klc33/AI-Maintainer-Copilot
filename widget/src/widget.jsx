import { render } from 'preact';
import { useState, useRef, useEffect } from 'preact/hooks';

function Widget() {
  const [open, setOpen] = useState(false);
  const [msg, setMsg] = useState('');
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const chatRef = useRef(null);

  const sendMessage = async () => {
    if (!msg.trim()) return;
    const userMsg = msg;
    setMsg('');
    setMessages(prev => [...prev, { role: 'user', content: userMsg }]);
    setLoading(true);

    try {
      const response = await fetch('/api/chat/message', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${window.__MC_SESSION_TOKEN__}`,
        },
        body: JSON.stringify({
          message: userMsg,
          conversation_id: 'widget_session',
        }),
      });

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let assistantMsg = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, { stream: true });
        const lines = chunk.split('\n');
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6));
              if (data.type === 'token') {
                assistantMsg += data.content;
                setMessages(prev => {
                  const last = prev[prev.length - 1];
                  if (last?.role === 'assistant') {
                    last.content = assistantMsg;
                    return [...prev];
                  }
                  return [...prev, { role: 'assistant', content: assistantMsg }];
                });
              }
            } catch (e) {}
          }
        }
      }
    } catch (e) {
      console.error(e);
    }
    setLoading(false);
  };

  const style = {
    position: 'fixed',
    bottom: '20px',
    right: '20px',
    zIndex: 9999,
    fontFamily: 'sans-serif',
  };

  if (!open) {
    return (
      <div style={style}>
        <button
          onClick={() => setOpen(true)}
          style={{
            width: '56px',
            height: '56px',
            borderRadius: '50%',
            backgroundColor: '#4f46e5',
            color: 'white',
            border: 'none',
            cursor: 'pointer',
            fontSize: '24px',
            boxShadow: '0 4px 12px rgba(0,0,0,0.2)',
          }}
        >
          💬
        </button>
      </div>
    );
  }

  return (
    <div
      style={{
        ...style,
        width: '360px',
        height: '500px',
        borderRadius: '12px',
        boxShadow: '0 8px 24px rgba(0,0,0,0.3)',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
        backgroundColor: 'white',
      }}
    >
      <div
        style={{
          backgroundColor: '#4f46e5',
          color: 'white',
          padding: '12px',
          display: 'flex',
          justifyContent: 'space-between',
        }}
      >
        <strong>Copilot</strong>
        <button onClick={() => setOpen(false)} style={{ background: 'none', border: 'none', color: 'white', cursor: 'pointer' }}>✕</button>
      </div>
      <div ref={chatRef} style={{ flex: 1, overflowY: 'auto', padding: '12px' }}>
        {messages.map((m, i) => (
          <div key={i} style={{ marginBottom: '8px', textAlign: m.role === 'user' ? 'right' : 'left' }}>
            <div
              style={{
                display: 'inline-block',
                padding: '8px 12px',
                borderRadius: '12px',
                maxWidth: '80%',
                backgroundColor: m.role === 'user' ? '#e0e7ff' : '#f1f5f9',
                color: 'black',
                fontSize: '14px',
              }}
            >
              {m.content}
            </div>
          </div>
        ))}
      </div>
      <div style={{ padding: '12px', borderTop: '1px solid #eee', display: 'flex' }}>
        <input
          type="text"
          value={msg}
          onInput={e => setMsg(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && sendMessage()}
          placeholder="Ask a question..."
          disabled={loading}
          style={{
            flex: 1,
            padding: '8px',
            borderRadius: '8px',
            border: '1px solid #ddd',
            fontSize: '14px',
          }}
        />
        <button
          onClick={sendMessage}
          disabled={loading}
          style={{
            marginLeft: '8px',
            padding: '8px 16px',
            backgroundColor: '#4f46e5',
            color: 'white',
            border: 'none',
            borderRadius: '8px',
            cursor: 'pointer',
          }}
        >
          Send
        </button>
      </div>
    </div>
  );
}

const root = document.getElementById('mc-widget-root');
if (root) {
  render(<Widget />, root);
}