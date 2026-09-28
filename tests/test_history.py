"""Complete history (#122): the run and session lists page through
everything, with server-side filters, and say how much there is."""

import datetime
from urllib.parse import quote

import httpx2 as httpx
import pytest

from .conftest import openai_response


def _capture(client, session, run_id=None, model="gpt-4o", agent=None):
    headers = {"x-agenticledger-session-id": session}
    if run_id:
        headers["x-agenticledger-run-id"] = run_id
    if agent:
        headers["x-agenticledger-agent-name"] = agent
    assert client.post("/v1/chat/completions",
                       json={"model": model, "messages": [{"role": "user", "content": "hi"}]},
                       headers=headers).status_code == 200


@pytest.fixture
def history(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()))
    for i in range(7):
        _capture(client, f"s-{i}", run_id=f"run-{i}",
                 model="claude-sonnet-5" if i % 2 else "gpt-4o",
                 agent="planner" if i < 3 else "coder")
    client.put("/api/labels/run/run-1", json={"project": "alpha", "pinned": True})
    client.put("/api/labels/run/run-2", json={"project": "alpha"})
    client.put("/api/labels/run/run-3", json={"name": "the good one"})
    client.put("/api/labels/session/s-4", json={"pinned": True, "project": "beta"})
    return client


def test_runs_page_with_totals_in_the_headers(history):
    first = history.get("/api/runs?limit=3")
    assert first.status_code == 200 and len(first.json()) == 3
    assert first.headers["x-total-count"] == "7"
    assert first.headers["x-next-offset"] == "3"
    second = history.get("/api/runs?limit=3&offset=3")
    assert len(second.json()) == 3 and second.headers["x-next-offset"] == "6"
    last = history.get("/api/runs?limit=3&offset=6")
    assert len(last.json()) == 1 and "x-next-offset" not in last.headers
    ids = [r["run_id"] for p in (first, second, last) for r in p.json()]
    assert sorted(ids) == sorted(f"run-{i}" for i in range(7))
    assert len(set(ids)) == 7
    # The default page is the old default, so existing clients see no change.
    assert len(history.get("/api/runs").json()) == 7


def test_runs_filter_by_project_starred_status_model_and_text(history):
    alpha = history.get("/api/runs?project=alpha")
    assert sorted(r["run_id"] for r in alpha.json()) == ["run-1", "run-2"]
    assert alpha.headers["x-total-count"] == "2"
    assert [r["run_id"] for r in history.get("/api/runs?project=__starred__").json()] == ["run-1"]
    assert history.get("/api/runs?project=nope").json() == []
    assert history.get("/api/runs?status=running").headers["x-total-count"] == "7"
    assert history.get("/api/runs?status=stopped").json() == []
    claude = history.get("/api/runs?model=claude").json()
    assert sorted(r["run_id"] for r in claude) == ["run-1", "run-3", "run-5"]
    assert [r["run_id"] for r in history.get("/api/runs?q=good").json()] == ["run-3"]
    assert [r["run_id"] for r in history.get("/api/runs?q=run-6").json()] == ["run-6"]


def test_runs_filter_by_time_window(history):
    now = datetime.datetime.now(datetime.timezone.utc)
    future = quote((now + datetime.timedelta(minutes=5)).isoformat())
    past = quote((now - datetime.timedelta(minutes=5)).isoformat())
    assert history.get(f"/api/runs?since={past}").headers["x-total-count"] == "7"
    assert history.get(f"/api/runs?since={future}").json() == []
    assert history.get(f"/api/runs?until={past}").json() == []
    # An offset typed by hand, plus sign unencoded, still parses.
    raw_plus = (now - datetime.timedelta(minutes=5)).isoformat()
    assert history.get(f"/api/runs?since={raw_plus}").headers["x-total-count"] == "7"
    # A bare date is a valid bound too (midnight UTC).
    today = now.date().isoformat()
    assert history.get(f"/api/runs?since={today}").headers["x-total-count"] == "7"


def test_sessions_page_and_filter(history):
    page = history.get("/api/sessions?limit=2&offset=2")
    assert len(page.json()) == 2 and page.headers["x-total-count"] == "7"
    assert page.headers["x-next-offset"] == "4"
    beta = history.get("/api/sessions?project=beta").json()
    assert [s["session_id"] for s in beta] == ["s-4"]
    assert [s["session_id"] for s in history.get("/api/sessions?project=__starred__").json()] == ["s-4"]
    by_run = history.get("/api/sessions?project=run:run-2").json()
    assert [s["session_id"] for s in by_run] == ["s-2"]
    assert [s["session_id"] for s in history.get("/api/sessions?run_id=run-5").json()] == ["s-5"]
    planner = history.get("/api/sessions?q=planner").json()
    assert sorted(s["session_id"] for s in planner) == ["s-0", "s-1", "s-2"]
    assert history.get("/api/sessions?model=gpt").headers["x-total-count"] == "4"


def test_bad_paging_and_filters_are_400_with_the_reason(history):
    for url, words in (
        ("/api/runs?limit=0", "limit"),
        ("/api/runs?limit=501", "limit"),
        ("/api/runs?offset=-1", "offset"),
        ("/api/runs?limit=abc", "integers"),
        ("/api/runs?status=paused", "status"),
        ("/api/sessions?since=yesterday", "ISO"),
    ):
        resp = history.get(url)
        assert resp.status_code == 400, url
        assert words in resp.json()["detail"], url
