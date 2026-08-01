"""0.8 flagship — replay the whole run + report card."""

import time

import httpx

from tests.conftest import anthropic_response, openai_response

UPSTREAM_URL = "https://mock-upstream.test"


def _wire_target(client, provider):
    client.app.state.replay_clients[provider] = httpx.AsyncClient(
        transport=httpx.MockTransport(client.upstream), base_url=UPSTREAM_URL,
    )


def test_score_replay_grades_honestly():
    from agenticledger.proxy.replay import score_replay

    orig = {"tool_calls": [{"name": "Bash"}, {"name": "Read"}]}
    same = score_replay(orig, "", [{"name": "Read"}, {"name": "Bash"}])
    assert same["match"] is True and same["tool_verdict"] == "same"

    talked = score_replay(orig, "I would just say this instead", None)
    assert talked["match"] is False and talked["tool_verdict"] == "orig-only"

    silent = score_replay({"tool_calls": None}, "", None)
    assert silent["answered"] is False and silent["match"] is False

    chatty_pair = score_replay({"tool_calls": None}, "hello", None)
    assert chatty_pair["match"] is True  # both just talked — that's a match


def _seed_run(client, n=3):
    for i in range(n):
        r = client.post("/v1/messages",
                        json={"model": "claude-sonnet-4", "max_tokens": 64,
                              "messages": [{"role": "user", "content": f"step {i}"}]},
                        headers={"x-agenticledger-session-id": "batch-sess",
                                 "x-agenticledger-run-id": "batch-run"})
        assert r.status_code == 200


def _wait_job(client, job_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/replay/jobs/{job_id}").json()
        if job["status"] == "done":
            return job
        time.sleep(0.05)
    raise AssertionError("batch job never finished")


def test_batch_replay_run_with_report_card(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json=anthropic_response()),
                   replay_targets={"openai": {"url": UPSTREAM_URL, "key": "lm-studio"}})
    _wire_target(client, "openai")
    _seed_run(client)

    client.upstream.set(lambda r: httpx.Response(200, json=openai_response(model="qwen-local")))
    resp = client.post("/api/replay/batch",
                       json={"run_id": "batch-run", "model": "qwen-local",
                             "provider": "openai"})
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["total"] == 3
    job = _wait_job(client, body["job_id"])
    assert job["done"] == 3
    assert len(job["steps"]) == 3
    assert all(s["status"] == "ok" for s in job["steps"])
    report = job["report"]
    assert report["replayed"] == 3
    # Originals had no tool calls; the openai_response mock talks too → matches.
    assert report["matched"] == 3 and report["fumbles"] == []
    # All three replays landed in ONE purple session, linked to originals.
    sess = client.get(f"/session/{body['replay_session_id']}").json()
    assert len(sess) == 3
    assert all(c["framework"] == "replay" and c["parent_action_id"] for c in sess)


def test_batch_replay_session_scope_and_validation(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json=anthropic_response()),
                   replay_targets={"openai": {"url": UPSTREAM_URL, "key": "k"}})
    _wire_target(client, "openai")
    _seed_run(client, n=2)

    assert client.post("/api/replay/batch", json={"model": "m"}).status_code == 400
    assert client.post("/api/replay/batch",
                       json={"run_id": "x", "session_id": "y", "model": "m"}).status_code == 400
    assert client.post("/api/replay/batch",
                       json={"run_id": "batch-run"}).status_code == 400
    assert client.post("/api/replay/batch",
                       json={"run_id": "nope", "model": "m"}).status_code == 404
    assert client.get("/api/replay/jobs/nope").status_code == 404

    client.upstream.set(lambda r: httpx.Response(200, json=openai_response()))
    resp = client.post("/api/replay/batch",
                       json={"session_id": "batch-sess", "model": "gpt-4o"})
    assert resp.status_code == 202
    job = _wait_job(client, resp.json()["job_id"])
    assert job["scope"] == "session" and job["report"]["replayed"] == 2


def test_batch_marks_fumbles_when_tools_diverge(proxy):
    """Original used a tool; the stand-in just talks → a fumble on the card."""
    client = proxy(handler=lambda r: httpx.Response(200, json=anthropic_response(
                       tool_uses=[{"id": "t1", "name": "Bash",
                                   "input": {"command": "ls"}}])),
                   replay_targets={"openai": {"url": UPSTREAM_URL, "key": "k"}})
    _wire_target(client, "openai")
    r = client.post("/v1/messages",
                    json={"model": "claude-sonnet-4", "max_tokens": 64,
                          "tools": [{"name": "Bash", "input_schema": {"type": "object"}}],
                          "messages": [{"role": "user", "content": "list files"}]},
                    headers={"x-agenticledger-session-id": "fumble-sess"})
    assert r.status_code == 200

    client.upstream.set(lambda r: httpx.Response(200, json=openai_response()))
    resp = client.post("/api/replay/batch",
                       json={"session_id": "fumble-sess", "model": "gpt-4o"})
    job = _wait_job(client, resp.json()["job_id"])
    assert job["report"]["matched"] == 0
    assert len(job["report"]["fumbles"]) == 1
    assert job["steps"][0]["score"]["tool_verdict"] == "orig-only"


def test_report_cards_are_reopenable(proxy):
    """Closing the panel or reloading the page must not lose a finished
    report card — the jobs list lets the panel re-attach."""
    client = proxy(handler=lambda r: httpx.Response(200, json=anthropic_response()),
                   replay_targets={"openai": {"url": UPSTREAM_URL, "key": "k"}})
    _wire_target(client, "openai")
    _seed_run(client, n=2)
    client.upstream.set(lambda r: httpx.Response(200, json=openai_response()))
    started = client.post("/api/replay/batch",
                          json={"session_id": "batch-sess", "model": "gpt-4o"}).json()
    _wait_job(client, started["job_id"])

    listed = client.get("/api/replay/jobs?scope=session&ref_id=batch-sess").json()["jobs"]
    assert [j["job_id"] for j in listed] == [started["job_id"]]
    assert listed[0]["status"] == "done"
    assert client.get("/api/replay/jobs?scope=run&ref_id=batch-sess").json()["jobs"] == []
    # A replay session can find its own comparison (the signpost's query).
    by_replay = client.get(
        f"/api/replay/jobs?replay_session_id={listed[0]['replay_session_id']}").json()["jobs"]
    assert by_replay and by_replay[0]["job_id"] == started["job_id"]
    assert by_replay[0]["ref_id"] == "batch-sess"


def test_report_cards_survive_a_restart_via_rebuild(proxy):
    """Twice in one day a proxy restart ate the report card. Never again:
    the card rebuilds from the durable replay calls themselves."""
    client = proxy(handler=lambda r: httpx.Response(200, json=anthropic_response()),
                   replay_targets={"openai": {"url": UPSTREAM_URL, "key": "k"}})
    _wire_target(client, "openai")
    _seed_run(client, n=2)
    client.upstream.set(lambda r: httpx.Response(200, json=openai_response()))
    started = client.post("/api/replay/batch",
                          json={"session_id": "batch-sess", "model": "gpt-4o"}).json()
    _wait_job(client, started["job_id"])

    client.app.state.replay_jobs.clear()   # simulate the restart

    jobs = client.get("/api/replay/jobs?scope=session&ref_id=batch-sess").json()["jobs"]
    assert len(jobs) == 1 and jobs[0]["rebuilt"] is True
    card = client.get(f"/api/replay/jobs/{jobs[0]['job_id']}").json()
    assert card["status"] == "done"
    assert card["report"]["replayed"] == 2
    assert all(st["score"] for st in card["steps"])
    assert card["ref_id"] == "batch-sess" and card["scope"] == "session"
