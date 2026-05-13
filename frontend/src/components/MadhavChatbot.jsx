import React, { useEffect, useRef, useState } from 'react';
import { X, Send, Sparkles } from 'lucide-react';
import { api } from '../api';

/**
 * Madhav — floating help chatbot.
 * Bottom-right "ask_me" icon → expands into a chat panel.
 * Backend: POST /api/madhav/ask, GET /api/madhav/topics
 */
export default function MadhavChatbot() {
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [topics, setTopics] = useState([]);
  const [messages, setMessages] = useState([
    {
      role: 'bot',
      text: "Hi, I'm **Madhav** 👋 — your QuantFlux assistant.\n\nTap any suggestion below, or type your own question.",
      suggestions: [],
    },
  ]);
  const scrollRef = useRef(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, open]);

  // Pre-load FAQ chips the first time the panel is opened.
  useEffect(() => {
    if (!open || topics.length > 0) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await api.madhavTopics();
        if (cancelled) return;
        const s = r?.suggestions || [];
        setTopics(s);
        setMessages((m) => {
          if (!m.length) return m;
          const first = m[0];
          if (first.role !== 'bot' || (first.suggestions && first.suggestions.length)) return m;
          return [{ ...first, suggestions: s }, ...m.slice(1)];
        });
      } catch { /* ignore */ }
    })();
    return () => { cancelled = true; };
  }, [open, topics.length]);

  const ask = async (q) => {
    if (!q || busy) return;
    setMessages((m) => [...m, { role: 'user', text: q }]);
    setInput('');
    setBusy(true);
    try {
      const r = await api.madhavAsk(q);
      setMessages((m) => [...m, {
        role: 'bot',
        text: r.answer || "I couldn't find that.",
        sources: r.sources || [],
        suggestions: r.suggestions || [],
      }]);
    } catch (e) {
      setMessages((m) => [...m, {
        role: 'bot',
        text: `Error: ${e.message}`,
        suggestions: topics,
      }]);
    } finally {
      setBusy(false);
    }
  };

  const send = () => ask(input.trim());

  const onKey = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  const renderText = (text) => (
    <div dangerouslySetInnerHTML={{
      __html: (text || '')
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
        .replace(/`([^`]+)`/g, '<code class="bg-black/30 px-1 rounded">$1</code>')
        .replace(/\n/g, '<br/>'),
    }} />
  );

  return (
    <>
      {/* Floating ask-me button */}
      {!open && (
        <button
          onClick={() => setOpen(true)}
          aria-label="Ask Madhav"
          className="fixed bottom-5 right-5 z-40 w-14 h-14 rounded-full bg-gradient-to-br from-brand-500 to-brand-700 text-white shadow-lg shadow-brand-900/40 flex items-center justify-center hover:scale-105 transition"
        >
          <Sparkles className="w-6 h-6" />
          <span className="absolute -top-1 -right-1 bg-yellow-400 text-black text-[10px] font-bold px-1.5 py-0.5 rounded-full">
            ask
          </span>
        </button>
      )}

      {/* Chat panel */}
      {open && (
        <div className="fixed bottom-5 right-5 z-50 w-[380px] sm:w-[440px] max-w-[95vw] h-[600px] max-h-[85vh] bg-surface-1 border border-surface-3 rounded-2xl shadow-2xl flex flex-col overflow-hidden">
          {/* header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-surface-3 bg-gradient-to-r from-brand-600/20 to-transparent">
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 rounded-full bg-brand-500/30 flex items-center justify-center">
                <Sparkles className="w-4 h-4 text-brand-300" />
              </div>
              <div>
                <div className="text-sm font-semibold text-white">Madhav</div>
                <div className="text-[10px] text-gray-500">QuantFlux assistant</div>
              </div>
            </div>
            <button onClick={() => setOpen(false)} className="text-gray-400 hover:text-white">
              <X className="w-4 h-4" />
            </button>
          </div>

          {/* messages */}
          <div ref={scrollRef} className="flex-1 overflow-y-auto p-3 space-y-2 text-sm">
            {messages.map((m, i) => (
              <div key={i}>
                <div className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                  <div className={`max-w-[88%] px-3 py-2 rounded-xl whitespace-pre-wrap leading-snug ${
                    m.role === 'user'
                      ? 'bg-brand-600 text-white'
                      : 'bg-surface-2 border border-surface-3 text-gray-200'
                  }`}>
                    {renderText(m.text)}
                    {m.sources && m.sources.length ? (
                      <div className="mt-2 pt-2 border-t border-surface-3 text-[10px] text-gray-500">
                        <span>Source:&nbsp;</span>
                        {m.sources.slice(0, 3).map((s, k) => (
                          <span key={k} className="mr-2">• {s.label}</span>
                        ))}
                      </div>
                    ) : null}
                  </div>
                </div>

                {/* Suggestion chips — clickable */}
                {m.role === 'bot' && m.suggestions && m.suggestions.length ? (
                  <div className="mt-2 flex flex-wrap gap-1.5 justify-start">
                    {m.suggestions.map((s, k) => (
                      <button
                        key={k}
                        onClick={() => ask(s.q)}
                        disabled={busy}
                        className="text-[11px] px-2.5 py-1 rounded-full bg-brand-500/15 border border-brand-500/40 text-brand-300 hover:bg-brand-500/25 hover:text-white transition disabled:opacity-50"
                      >
                        {s.label}
                      </button>
                    ))}
                  </div>
                ) : null}
              </div>
            ))}
            {busy ? (
              <div className="flex justify-start">
                <div className="bg-surface-2 border border-surface-3 text-gray-400 px-3 py-2 rounded-xl text-xs italic">
                  Madhav is thinking…
                </div>
              </div>
            ) : null}
          </div>

          {/* input */}
          <div className="p-2 border-t border-surface-3 flex gap-2 bg-surface-1">
            <textarea
              rows={1}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKey}
              placeholder="Ask anything about QuantFlux…"
              className="flex-1 input-field text-xs resize-none py-2 text-white placeholder-gray-500"
              style={{ minHeight: '38px', maxHeight: '120px' }}
            />
            <button onClick={send} disabled={busy || !input.trim()}
                    className="px-3 rounded-lg bg-brand-600 hover:bg-brand-500 text-white disabled:opacity-50">
              <Send className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}
    </>
  );
}
