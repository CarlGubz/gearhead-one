import React, { useEffect, useRef, useState } from "react";
import * as api from "../api.js";

const EXAMPLES = [
  "A 1 m long steel cantilever beam with a 50 mm × 50 mm square section, fixed at one end and loaded with 2 kN downward at the free end.",
  "Aluminium plate 200 mm × 100 mm × 5 mm with a 20 mm hole, clamped on the left edge and pulled with 10 MPa on the right edge.",
  "Steel cylinder 100 mm diameter, 300 mm tall, standing on the ground and carrying a 50 kN compressive load on top.",
];

const storageKey = (id) => `webfea-agent-${id}`;

function loadChat(id) {
  try {
    return JSON.parse(sessionStorage.getItem(storageKey(id))) || [];
  } catch (e) {
    return [];
  }
}

/**
 * Chat with the engineering agent. The agent edits the project on the server; this panel shows what it did,
 * hands the updated project to the workspace and, when the agent says the model is complete, starts the
 * simulation through the same code path as the Solve button.
 */
export default function AgentPanel({ projectId, onClose, onProject, onRun, solving }) {
  const [status, setStatus] = useState(null);
  const [chat, setChat] = useState(() => loadChat(projectId));
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const logRef = useRef(null);

  useEffect(() => {
    api.agentStatus().then(setStatus).catch(() => setStatus({ available: false }));
  }, []);
  useEffect(() => {
    setChat(loadChat(projectId));
  }, [projectId]);
  useEffect(() => {
    try {
      sessionStorage.setItem(storageKey(projectId), JSON.stringify(chat.slice(-40)));
    } catch (e) { /* storage unavailable */ }
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [chat, projectId, busy]);

  const send = async (content) => {
    const msg = (content ?? text).trim();
    if (!msg || busy || !projectId) return;
    const next = [...chat, { role: "user", content: msg }];
    setChat(next);
    setText("");
    setBusy(true);
    try {
      const history = next.filter((m) => m.role !== "error").map((m) => ({ role: m.role, content: m.content }));
      const out = await api.agentChat(projectId, history);
      setChat((c) => [...c, { role: "assistant", content: out.reply, steps: out.steps }]);
      if (out.steps?.length) onProject(out.project);
      if (out.run_simulation) onRun(out.project);
    } catch (err) {
      setChat((c) => [...c, { role: "error", content: api.errorMessage(err, "The assistant could not answer.") }]);
    } finally {
      setBusy(false);
    }
  };

  const off = status && !status.available;
  return (
    <div className="agent-drawer" role="dialog" aria-label="AI engineering assistant">
      <div className="agent-head">
        <strong>✦ Gearhead AI</strong>
        <span className="spacer" />
        {chat.length > 0 && <button title="Start a new conversation" onClick={() => setChat([])} disabled={busy}>Clear</button>}
        <button title="Close" onClick={onClose}>✕</button>
      </div>
      <div className="agent-log" ref={logRef}>
        {chat.length === 0 && (
          <p className="agent-hint" style={{ padding: 0 }}>
            Describe the problem in plain language: geometry, material, how it is held and what loads it.
            I build the model in this project, then run the simulation for you. You can edit anything afterwards
            (Ctrl+Z undoes my steps).
          </p>
        )}
        {chat.map((m, i) => (
          <div key={i} className={`agent-msg ${m.role}`}>
            {m.content}
            {m.steps?.length > 0 && (
              <ul className="agent-steps">
                {m.steps.map((s, k) => (
                  <li key={k}>
                    <span className={s.ok ? "ok" : "bad"}>{s.ok ? "✓" : "✕"}</span>
                    <span>{s.label}{s.detail ? ` — ${s.detail}` : ""}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
        {busy && <div className="agent-working">Working on your model</div>}
      </div>
      {chat.length === 0 && !off && (
        <div className="agent-examples">
          {EXAMPLES.map((ex) => (
            <button key={ex} disabled={busy || !projectId} onClick={() => send(ex)}>{ex}</button>
          ))}
        </div>
      )}
      {off && (
        <p className="agent-hint">
          The assistant is switched off on this server. The administrator must set <code>GEMINI_API_KEY</code> in the
          backend <code>.env</code> and restart (see the README).
        </p>
      )}
      {!projectId && !off && <p className="agent-hint">Select or create a project first.</p>}
      <form className="agent-form" onSubmit={(e) => { e.preventDefault(); send(); }}>
        <textarea value={text} placeholder="Describe your problem…" rows={2} disabled={off || !projectId}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
        <button type="submit" disabled={off || busy || solving || !text.trim() || !projectId}>Send</button>
      </form>
    </div>
  );
}
