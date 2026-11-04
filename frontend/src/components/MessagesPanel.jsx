import React, { useEffect, useRef } from "react";

/** PrePoMax-style message/output window: errors, warnings and the solver log. */
export default function MessagesPanel({ messages, open, setOpen, onClear }) {
  const endRef = useRef(null);
  useEffect(() => {
    if (open) endRef.current?.scrollIntoView({ block: "end" });
  }, [messages, open]);
  const last = messages[messages.length - 1];
  return (
    <div className={`messages ${open ? "open" : ""}`}>
      <div className="messages-bar" onClick={() => setOpen(!open)}>
        <span className={`tree-chevron ${open ? "open" : ""}`}>❯</span>
        <strong>Messages</strong>
        {!open && last && <span className={`msg-${last.level}`}>{last.text.split("\n")[0].slice(0, 140)}</span>}
        <span className="spacer" />
        {open && <button className="link" style={{ color: "var(--ws-text-2)" }} onClick={(e) => { e.stopPropagation(); onClear(); }}>clear</button>}
      </div>
      {open && (
        <div className="messages-body">
          {messages.length === 0 && <p className="hint">Nothing to report.</p>}
          {messages.map((m) => (
            <pre key={m.id} className={`msg-${m.level}`}>{`[${m.time}] ${m.text}`}</pre>
          ))}
          <div ref={endRef} />
        </div>
      )}
    </div>
  );
}
