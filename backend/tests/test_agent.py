"""AI agent: the tool loop is driven by a scripted fake LLM, so no network or API key is needed."""
import json

import pytest

from app import agent
from app.config import settings
from tests.conftest import register
from tests.test_simulation_api import solve


class FakeLLM:
    """Plays back a list of assistant turns; each turn is a list of (tool, input) calls or a final text."""

    def __init__(self, turns):
        self.turns, self.requests = list(turns), []

    def __call__(self, body):
        self.requests.append(body)
        turn = self.turns.pop(0)
        if isinstance(turn, str):
            return {"stop_reason": "end_turn", "content": [{"type": "text", "text": turn}]}
        return {"stop_reason": "tool_use", "content": [
            {"type": "tool_use", "id": f"tu{len(self.requests)}_{i}", "name": n, "input": a} for i, (n, a) in enumerate(turn)]}


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    monkeypatch.setattr(settings, "agent_provider_setting", "")
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")


def use(monkeypatch, turns):
    llm = FakeLLM(turns)
    monkeypatch.setattr(agent, "_post_messages", llm)
    return llm


def chat(c, pid, text="go"):
    return c.post(f"/api/projects/{pid}/agent/chat", json={"messages": [{"role": "user", "content": text}]})


def new_project(c):
    return c.post("/api/projects", json={"name": "Agent"}).json()["id"]


CANTILEVER = [
    [("create_solid_mesh", {"shape": "box", "dims": {"length": 1.0, "width": 0.1, "height": 0.1}, "divisions": 3,
                            "element_type": "C3D10"})],
    [("set_material", {"preset": "steel"}), ("set_analysis", {"analysis_type": "static"})],
    [("add_support", {"name": "Wall", "region": {"type": "face", "side": "x_min"}, "dofs": [0, 1, 2]}),
     ("add_load", {"load_type": "pressure", "region": {"type": "face", "side": "x_max"}, "magnitude": 1e6})],
    [("run_simulation", {})],
    "Cantilever beam modelled in steel, fixed at x=0 with 1 MPa at the free end. Simulation started.",
]


def test_status_and_unconfigured(client, monkeypatch):
    register(client)
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    assert client.get("/api/agent/status").json() == {"available": False, "model": None, "provider": None}
    assert chat(client, new_project(client)).status_code == 503


def test_agent_builds_a_model_and_requests_the_run(client, configured, monkeypatch):
    register(client)
    pid = new_project(client)
    llm = use(monkeypatch, CANTILEVER)
    r = chat(client, pid, "A 1 m steel beam, 100 mm square, clamped at one end, 1 MPa on the other end.")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["run_simulation"] is True
    assert [s["tool"] for s in out["steps"]] == ["create_solid_mesh", "set_material", "set_analysis", "add_support",
                                                 "add_load", "run_simulation"]
    assert all(s["ok"] for s in out["steps"]), out["steps"]
    p = out["project"]
    assert p["element_type"] == "C3D10" and p["material"]["E"] == 210e9
    assert len(p["boundary_conditions"]) == 1 and len(p["loads"]) == 1
    assert p["loads"][0]["faces"] and all(  # only whole faces on the x_max plane
        abs(p["nodes"][n][0] - 0.5) < 1e-9 for t in p["loads"][0]["faces"] for n in t)
    assert "tools" in llm.requests[0] and "claude" in llm.requests[0]["model"]
    # edits are ordinary undoable edits
    assert client.get(f"/api/projects/{pid}").json()["history_labels"]
    # and the manual solve path works on what the agent built
    job = solve(client, pid)
    assert job["status"] == "completed", job["log"]
    # axial shortening of the bar under 1 MPa end pressure (pushes into the surface): -P*L/E
    ux = min(job["results"]["fields"]["ux"])
    assert ux == pytest.approx(-1e6 * 1.0 / 210e9, rel=0.02)


def test_tool_errors_are_returned_to_the_model_not_raised(client, configured, monkeypatch):
    register(client)
    pid = new_project(client)
    llm = use(monkeypatch, [
        [("add_support", {"region": {"type": "face", "side": "x_min"}, "dofs": [0]})],       # no mesh yet
        [("create_solid_mesh", {"shape": "box", "dims": {"length": 1}, "divisions": 2, "element_type": "C3D4"})],
        [("add_support", {"region": {"type": "box", "min": [5, 5, 5], "max": [6, 6, 6]}, "dofs": [0]})],  # empty
        [("run_simulation", {})],                                                             # nothing to run
        "I could not finish.",
    ])
    out = chat(client, pid).json()
    assert [s["ok"] for s in out["steps"]] == [False, True, False, False]
    assert "no mesh" in out["steps"][0]["detail"].lower()
    assert "contains no nodes" in out["steps"][2]["detail"]
    assert out["run_simulation"] is False
    sent = llm.requests[-1]["messages"][-1]["content"][0]
    assert sent["is_error"] is True


def test_pro_only_analysis_is_refused_for_free_users(client, configured, monkeypatch):
    register(client)
    pid = new_project(client)
    use(monkeypatch, [[("create_solid_mesh", {"shape": "box", "dims": {}, "divisions": 2, "element_type": "C3D4"}),
                       ("set_analysis", {"analysis_type": "modal"})], "Modal needs Pro."])
    steps = chat(client, pid).json()["steps"]
    assert steps[0]["ok"] and not steps[1]["ok"] and "Pro" in steps[1]["detail"]


def test_structural_model_and_rate_limit_and_ownership(client, make_client, configured, monkeypatch):
    register(client)
    pid = new_project(client)
    use(monkeypatch, [
        [("create_structural_model", {"kind": "line", "element_type": "B31", "divisions": 4,
                                      "dims": {"x2": 2.0}, "section": {"type": "rect", "b": 0.05, "h": 0.1}}),
         ("set_material", {"preset": "aluminium"}),
         ("add_support", {"region": {"type": "face", "side": "x_min"}, "dofs": [0, 1, 2, 3, 4, 5]}),
         ("add_load", {"load_type": "force", "region": {"type": "face", "side": "x_max"}, "vector": [0, 0, -500]}),
         ("run_simulation", {})],
        "Done."])
    out = chat(client, pid).json()
    assert out["run_simulation"] is True and all(s["ok"] for s in out["steps"]), out["steps"]
    job = solve(client, pid)
    assert job["status"] == "completed", job["log"]

    other = make_client()
    register(other, email="bo@example.com")
    assert chat(other, pid).status_code == 404

    bad = client.post(f"/api/projects/{pid}/agent/chat", json={"messages": [{"role": "assistant", "content": "hi"}]})
    assert bad.status_code == 422


def test_loop_is_bounded(client, configured, monkeypatch):
    register(client)
    pid = new_project(client)
    monkeypatch.setattr(settings, "agent_max_turns", 3)
    use(monkeypatch, [[("inspect_model", {})]] * 5)
    out = chat(client, pid).json()
    assert len(out["steps"]) == 3 and "ran out of steps" in out["reply"]


# ------------------------------------------------------------------ Gemini provider
class FakeGemini:
    """Stands in for httpx.post: plays back Gemini generateContent replies and records the requests."""

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.calls.append((url, json, headers))
        reply = self.replies.pop(0)
        status, data = reply if isinstance(reply, tuple) else (200, reply)
        return type("R", (), {"status_code": status, "text": str(data), "json": lambda self: data})()


def fc(name, args, sig=None):
    part = {"functionCall": {"name": name, "args": args}}
    if sig:
        part["thoughtSignature"] = sig
    return part


def gem(*parts, finish="STOP"):
    return {"candidates": [{"content": {"role": "model", "parts": list(parts)}, "finishReason": finish}]}


@pytest.fixture
def gemini(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "g-key")
    monkeypatch.setattr(settings, "gemini_model", "gemini-2.5-pro")
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    monkeypatch.setattr(settings, "agent_provider_setting", "")


def test_gemini_is_the_default_provider_when_its_key_is_set(client, gemini, monkeypatch):
    register(client)
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    assert client.get("/api/agent/status").json() == {"available": True, "model": "gemini-2.5-pro", "provider": "gemini"}
    monkeypatch.setattr(settings, "agent_provider_setting", "anthropic")
    assert client.get("/api/agent/status").json()["available"] is False  # forced to a provider with no key


def test_gemini_builds_the_model_and_requests_the_run(client, gemini, monkeypatch):
    register(client)
    pid = new_project(client)
    fake = FakeGemini([
        gem(fc("create_solid_mesh", {"shape": "box", "dims": {"length": 1.0, "width": 0.1, "height": 0.1},
                                     "divisions": 3, "element_type": "C3D10"}, sig="sigA")),
        gem({"text": "thinking...", "thought": True},
            fc("set_material", {"preset": "steel"}), fc("set_analysis", {"analysis_type": "static"})),
        gem(fc("add_support", {"name": "Wall", "region": {"type": "face", "side": "x_min"}, "dofs": [0, 1, 2]}),
            fc("add_load", {"load_type": "pressure", "region": {"type": "face", "side": "x_max"}, "magnitude": 1e6})),
        gem(fc("run_simulation", {})),
        gem({"text": "Steel cantilever, fixed at x=0, 1 MPa at the free end. Simulation started."}),
    ])
    monkeypatch.setattr(agent.httpx, "post", fake)
    out = chat(client, pid, "1 m steel bar 100 mm square, clamped at one end, 1 MPa on the other.").json()
    assert out["run_simulation"] is True and all(s["ok"] for s in out["steps"]), out["steps"]
    assert [s["tool"] for s in out["steps"]] == ["create_solid_mesh", "set_material", "set_analysis", "add_support",
                                                 "add_load", "run_simulation"]
    assert "thinking" not in out["reply"] and "Simulation started" in out["reply"]
    job = solve(client, pid)
    assert job["status"] == "completed", job["log"]
    assert min(job["results"]["fields"]["ux"]) == pytest.approx(-1e6 / 210e9, rel=0.02)

    url, first, headers = fake.calls[0]
    assert url.endswith("/models/gemini-2.5-pro:generateContent") and headers["x-goog-api-key"] == "g-key"
    assert first["systemInstruction"]["parts"][0]["text"].startswith("You are the Gearhead")
    assert first["contents"] == [{"role": "user", "parts": [{"text": "1 m steel bar 100 mm square, clamped at one end, 1 MPa on the other."}]}]
    decls = {d["name"]: d for d in first["tools"][0]["functionDeclarations"]}
    assert "run_simulation" in decls and "parametersJsonSchema" not in decls["run_simulation"]  # no-arg tool
    assert decls["add_load"]["parametersJsonSchema"]["required"] == ["load_type"]
    # the second request replays the model's call (with its thought signature) and our result as functionResponse
    second = fake.calls[1][1]["contents"]
    assert second[1]["role"] == "model" and second[1]["parts"][0]["functionCall"]["name"] == "create_solid_mesh"
    assert second[1]["parts"][0]["thoughtSignature"] == "sigA"
    resp = second[2]["parts"][0]["functionResponse"]
    assert second[2]["role"] == "user" and resp["name"] == "create_solid_mesh" and resp["response"]["ok"] is True
    assert fake.calls[0][1]["generationConfig"]["maxOutputTokens"] >= 8192


def test_gemini_tool_errors_are_returned_to_the_model(client, gemini, monkeypatch):
    register(client)
    pid = new_project(client)
    fake = FakeGemini([gem(fc("add_support", {"region": {"type": "face", "side": "x_min"}, "dofs": [0]})),
                       gem({"text": "I need a mesh first."})])
    monkeypatch.setattr(agent.httpx, "post", fake)
    out = chat(client, pid).json()
    assert out["steps"][0]["ok"] is False
    sent = fake.calls[1][1]["contents"][-1]["parts"][0]["functionResponse"]["response"]
    assert sent["ok"] is False and "no mesh" in sent["error"].lower()


@pytest.mark.parametrize("status,expect", [(403, 503), (429, 429), (500, 502)])
def test_gemini_http_errors_are_mapped(client, gemini, monkeypatch, status, expect):
    register(client)
    pid = new_project(client)
    monkeypatch.setattr(agent.httpx, "post", FakeGemini([(status, {"error": {"message": "x"}})]))
    assert chat(client, pid).status_code == expect


def test_gemini_blocked_prompt_gives_a_readable_reply(client, gemini, monkeypatch):
    register(client)
    pid = new_project(client)
    monkeypatch.setattr(agent.httpx, "post", FakeGemini([{"promptFeedback": {"blockReason": "SAFETY"}}]))
    out = chat(client, pid).json()
    assert "declined" in out["reply"] and out["steps"] == [] and out["run_simulation"] is False


# ------------------------------------------------------------------ OpenRouter provider (last resort)
class FakeOpenRouter(FakeGemini):
    pass


def orr(content=None, calls=()):
    msg = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = [{"id": f"or{i}", "type": "function",
                              "function": {"name": n, "arguments": json.dumps(a)}} for i, (n, a) in enumerate(calls)]
    return {"choices": [{"message": msg, "finish_reason": "tool_calls" if calls else "stop"}]}


@pytest.fixture
def openrouter(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings, "agent_provider_setting", "")
    monkeypatch.setattr(settings, "openrouter_api_key", "or-key")
    monkeypatch.setattr(settings, "openrouter_model", "google/gemini-2.5-pro")


def test_openrouter_is_used_only_when_gemini_and_anthropic_keys_are_missing(client, openrouter, monkeypatch):
    register(client)
    assert client.get("/api/agent/status").json() == {"available": True, "model": "google/gemini-2.5-pro",
                                                      "provider": "openrouter"}
    monkeypatch.setattr(settings, "anthropic_api_key", "a")
    assert settings.agent_provider == "anthropic"
    monkeypatch.setattr(settings, "gemini_api_key", "g")
    assert settings.agent_provider == "gemini"
    monkeypatch.setattr(settings, "agent_provider_setting", "openrouter")  # explicit choice still wins
    assert settings.agent_provider == "openrouter"


def test_openrouter_builds_the_model_and_requests_the_run(client, openrouter, monkeypatch):
    register(client)
    pid = new_project(client)
    fake = FakeOpenRouter([
        orr(calls=[("create_solid_mesh", {"shape": "box", "dims": {"length": 1.0, "width": 0.1, "height": 0.1},
                                          "divisions": 3, "element_type": "C3D10"})]),
        orr("ok", calls=[("set_material", {"preset": "steel"}), ("set_analysis", {"analysis_type": "static"})]),
        orr(calls=[("add_support", {"name": "Wall", "region": {"type": "face", "side": "x_min"}, "dofs": [0, 1, 2]}),
                   ("add_load", {"load_type": "pressure", "region": {"type": "face", "side": "x_max"},
                                 "magnitude": 1e6})]),
        orr(calls=[("run_simulation", {})]),
        orr("Steel bar, fixed at x=0, 1 MPa at the free end. Simulation started."),
    ])
    monkeypatch.setattr(agent.httpx, "post", fake)
    out = chat(client, pid, "1 m steel bar 100 mm square, clamped at one end, 1 MPa on the other.").json()
    assert out["run_simulation"] is True and all(s["ok"] for s in out["steps"]), out["steps"]
    assert "Simulation started" in out["reply"]
    job = solve(client, pid)
    assert job["status"] == "completed", job["log"]
    assert min(job["results"]["fields"]["ux"]) == pytest.approx(-1e6 / 210e9, rel=0.02)

    url, first, headers = fake.calls[0]
    assert url == agent.OPENROUTER_URL and headers["Authorization"] == "Bearer or-key"
    assert first["model"] == "google/gemini-2.5-pro"
    assert first["messages"][0]["role"] == "system" and first["messages"][1]["role"] == "user"
    tool = next(t for t in first["tools"] if t["function"]["name"] == "add_load")
    assert tool["type"] == "function" and tool["function"]["parameters"]["required"] == ["load_type"]
    second = fake.calls[1][1]["messages"]
    call = second[-2]["tool_calls"][0]
    assert second[-2]["role"] == "assistant" and call["function"]["name"] == "create_solid_mesh"
    assert json.loads(call["function"]["arguments"])["shape"] == "box"
    assert second[-1]["role"] == "tool" and second[-1]["tool_call_id"] == call["id"]
    assert json.loads(second[-1]["content"])["ok"] is True


@pytest.mark.parametrize("status,expect", [(401, 503), (429, 429), (500, 502)])
def test_openrouter_http_errors_are_mapped(client, openrouter, monkeypatch, status, expect):
    register(client)
    pid = new_project(client)
    monkeypatch.setattr(agent.httpx, "post", FakeOpenRouter([(status, {"error": {"message": "x"}})]))
    assert chat(client, pid).status_code == expect
