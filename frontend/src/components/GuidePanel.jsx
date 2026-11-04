import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { STEPS, TOPICS, advice, blocker, guideState, matchTopic, nextStepIndex } from "./guideContent.js";

const HIGHLIGHT = "tour-highlight";
const storageKey = "webfea-guide-step";

/**
 * Spotlight the workspace element tagged data-tour="<id>", opening it first if it is a collapsed section. Ribbon commands
 * share their id with the tour id: the workspace opens the command's dialog when it hears gh:open-command.
 */
function spotlight(id) {
  document.querySelectorAll(`.${HIGHLIGHT}`).forEach((el) => el.classList.remove(HIGHLIGHT));
  if (!id) return;
  window.dispatchEvent(new CustomEvent("gh:open-command", { detail: id }));
  // a collapsed column opens on that event; let it render before looking for the section
  setTimeout(() => {
    const el = document.querySelector(`[data-tour="${id}"]`);
    if (!el) return;
    const header = el.querySelector(".tree-section-header");
    if (header && !el.querySelector(".tree-section-body")) header.click();
    el.classList.add(HIGHLIGHT);
    el.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, 60);
}

/**
 * Walkthrough agent. It follows the live project state, explains each step of the workflow, highlights the
 * matching panel in the workspace and answers common questions. Everything runs in the browser.
 */
export default function GuidePanel({ project, job, solving, isPro, onClose }) {
  const state = useMemo(() => guideState({ project, job, solving, isPro }), [project, job, solving, isPro]);
  const next = nextStepIndex(state);
  const [mode, setMode] = useState("home"); // home | tour | answer
  const [step, setStep] = useState(0);
  const [answer, setAnswer] = useState(null);
  const [text, setText] = useState("");
  const bodyRef = useRef(null);

  useEffect(() => {
    try {
      const saved = Number(sessionStorage.getItem(storageKey));
      if (Number.isInteger(saved) && saved > 0 && saved < STEPS.length) setStep(saved);
    } catch (e) { /* storage unavailable */ }
    return () => spotlight(null);
  }, []);

  const goTo = useCallback((i) => {
    const k = Math.max(0, Math.min(STEPS.length - 1, i));
    setStep(k);
    setMode("tour");
    spotlight(STEPS[k].tour);
    try { sessionStorage.setItem(storageKey, String(k)); } catch (e) { /* storage unavailable */ }
  }, []);

  const home = () => { setMode("home"); setAnswer(null); spotlight(null); };

  const ask = (topic) => {
    setAnswer(topic);
    setMode("answer");
    spotlight(topic.tour);
  };
  const submit = (e) => {
    e.preventDefault();
    const q = text.trim();
    if (!q) return;
    setText("");
    ask(matchTopic(q) || { id: "none", label: q, answer: "I could not match that to a topic. Try one of the questions below, or use the tour for a step-by-step walkthrough. For anything you want built for you, open the AI Assistant.", tour: null });
  };

  useEffect(() => { if (bodyRef.current) bodyRef.current.scrollTop = 0; }, [mode, step, answer]);

  const cur = STEPS[step];
  const doneCount = STEPS.filter((s) => !s.optional && s.done(state)).length;
  const required = STEPS.filter((s) => !s.optional).length;

  return (
    <div className="agent-drawer guide-drawer" role="dialog" aria-label="Workspace guide">
      <div className="agent-head">
        <strong>? Gearhead Guide</strong>
        <span className="spacer" />
        {mode !== "home" && <button title="Back to the guide menu" onClick={home}>Menu</button>}
        <button title="Close" onClick={onClose}>✕</button>
      </div>

      <div className="guide-progress" title={`${doneCount} of ${required} required steps done`}>
        <div className="guide-progress-fill" style={{ width: `${(doneCount / required) * 100}%` }} />
      </div>

      <div className="agent-log guide-body" ref={bodyRef}>
        {mode === "home" && (
          <>
            <div className="agent-msg assistant guide-advice">{advice(state)}</div>
            <div className="guide-actions">
              <button className="primary" onClick={() => goTo(0)}>▶ Start the tour</button>
              <button onClick={() => goTo(next)}>Go to my next step</button>
            </div>
            <div className="guide-label">Workflow ({doneCount}/{required})</div>
            <ol className="guide-steps">
              {STEPS.map((s, i) => (
                <li key={s.id} className={`${s.done(state) ? "done" : ""} ${i === next ? "next" : ""}`}>
                  <button onClick={() => goTo(i)}>
                    <span className="guide-check">{s.done(state) ? "✓" : i + 1}</span>
                    <span>{s.title}{s.optional ? " (optional)" : ""}</span>
                  </button>
                </li>
              ))}
            </ol>
          </>
        )}

        {mode === "tour" && (
          <>
            <div className="guide-label">Step {step + 1} of {STEPS.length}{cur.optional ? " · optional" : ""}</div>
            <h4 className="guide-title">{cur.title}{cur.done(state) && <span className="guide-badge">done</span>}</h4>
            <div className="agent-msg assistant">{cur.text}</div>
            {cur.tip && <div className="guide-tip"><strong>Tip.</strong> {cur.tip}</div>}
            {blocker(cur, state) && <div className="guide-block">{blocker(cur, state)}</div>}
            {step === next && !blocker(cur, state) && !cur.done(state) && <div className="guide-now">This is your next step.</div>}
            <div className="guide-actions">
              <button onClick={() => goTo(step - 1)} disabled={step === 0}>← Back</button>
              <button onClick={() => spotlight(cur.tour)} title="Highlight this panel in the workspace">Show me</button>
              <button className="primary" onClick={() => (step === STEPS.length - 1 ? home() : goTo(step + 1))}>
                {step === STEPS.length - 1 ? "Finish" : "Next →"}
              </button>
            </div>
          </>
        )}

        {mode === "answer" && answer && (
          <>
            <div className="guide-label">You asked</div>
            <h4 className="guide-title">{answer.label}</h4>
            <div className="agent-msg assistant">{answer.answer}</div>
            <div className="guide-actions">
              {answer.tour && <button onClick={() => spotlight(answer.tour)}>Show me</button>}
              <button className="primary" onClick={() => goTo(next)}>Go to my next step</button>
            </div>
          </>
        )}
      </div>

      <div className="agent-examples guide-topics">
        {TOPICS.slice(0, 6).map((t) => (
          <button key={t.id} onClick={() => ask(t)}>{t.label}</button>
        ))}
      </div>
      <form className="agent-form" onSubmit={submit}>
        <input value={text} placeholder="Ask how to do something…" onChange={(e) => setText(e.target.value)} />
        <button type="submit" disabled={!text.trim()}>Ask</button>
      </form>
    </div>
  );
}
