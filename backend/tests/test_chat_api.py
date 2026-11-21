import asyncio
import json
from contextlib import asynccontextmanager
from datetime import date, timedelta
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agent.context import SYSTEM_PROMPT
from app.agent.dsh import MAX_MODEL_CALLS
from app.agent.tools import TOOL_DEFINITIONS
from app.api.dependencies import require_current_user
from app.db.base import utc_now
from app.main import app
from app.models import Document, DocumentChunk, DocumentVersion
from app.models.chat_session import ChatSession, ChatSessionEvent
from app.models.document import content_digest
from tests.test_ingestion_api import make_user
from tests.test_search_api import search as search

URL = "/api/v1/chat/stream"
SESSIONS = "/api/v1/chat/sessions"


def test_chat_requires_login():
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(URL, json={"question": "证券公司风险如何处置？"})
    assert (response.status_code, response.json()["code"]) == (401, "AUTH_REQUIRED")


def test_legacy_chat_endpoint_is_removed():
    client = TestClient(app)
    assert (
        client.post("/api/v1/chat", json={"question": "怎么处理？"}).status_code == 404
    )


def test_model_key_alone_cannot_enable_chat(search, session_owner, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-model-key")
    monkeypatch.delenv("CHAT_DSH_TOKEN", raising=False)

    async def unexpected_network_call(*_args, **_kwargs):
        pytest.fail("Chat sent a network request without bridge credentials")

    monkeypatch.setattr(httpx.AsyncClient, "stream", unexpected_network_call)
    response = search.client.post(URL, json={"question": "怎么处理？"})
    created = event_payload(response.text, "turn")
    assert event_payload(response.text, "error")["code"] == "CHAT_UNAVAILABLE"
    (turn,) = search.client.get(f"{SESSIONS}/{created['session_id']}").json()["turns"]
    assert turn["state"] == "failed"


@pytest.fixture
def mock_bridge(monkeypatch, session_owner):
    monkeypatch.setenv("CHAT_DSH_URL", "http://dsh.test")
    monkeypatch.setenv("CHAT_DSH_TOKEN", "unit-private-token")
    receipts = {}

    async def answer_tool(_client, url, *, headers, json):
        _, _, turn_id, command, call_id = httpx.URL(url).path.split("/")
        assert command == "tools"
        assert headers["Authorization"] == "Bearer unit-private-token"
        receipts[turn_id][call_id] = json
        return httpx.Response(204, request=httpx.Request("POST", url))

    def install(frames):
        @asynccontextmanager
        async def stream(client, method, url, *, headers, json):
            assert (method, url) == ("POST", "http://dsh.test/turns")
            assert headers["Authorization"] == "Bearer unit-private-token"
            assert "model" not in json
            turn_id = json["turn_id"]
            results = receipts[turn_id] = {}
            try:
                yield httpx.Response(
                    200,
                    request=httpx.Request(method, url),
                    stream=BridgeFrames(frames(client, json, results)),
                )
            finally:
                del receipts[turn_id]

        monkeypatch.setattr(httpx.AsyncClient, "stream", stream)
        monkeypatch.setattr(httpx.AsyncClient, "post", answer_tool)

    return install


class BridgeFrames(httpx.AsyncByteStream):
    def __init__(self, frames):
        self.frames = frames

    async def __aiter__(self):
        async for frame in self.frames:
            yield (
                frame
                if isinstance(frame, bytes)
                else (json.dumps(frame) + "\n").encode()
            )

    async def aclose(self):
        await self.frames.aclose()


@pytest.fixture
def mock_dsh(mock_bridge):
    def install(reply):
        async def frames(client, payload, results):
            assert client.timeout.read is None
            for _ in range(MAX_MODEL_CALLS):
                yield {"type": "start"}
                step = reply(payload, results)
                if isinstance(step, str):
                    yield {"type": "text", "text": step}
                    yield {"type": "done", "text": step}
                    return
                for tool in [step] if isinstance(step, dict) else step:
                    yield tool
            pytest.fail("Mock DSH turn exceeded the model-call budget")

        mock_bridge(frames)

    return install


@pytest.fixture
def session_owner(search):
    owner = make_user("user")
    with search.db() as session:
        session.add(owner)
        session.commit()
    app.dependency_overrides[require_current_user] = lambda: owner
    return owner


@pytest.mark.parametrize(
    ("question", "answer"),
    [
        ("你好", "你好！有什么想了解的吗？"),
        ("你好", "你好！我是 FinOracle 金融制度助手。有什么可以帮你的吗？"),
        ("那个呢？", "你指的是哪一项？"),
        (
            "展示代码",
            "读取第二个值用 \x60arr[1]\x60。\n\n\x60\x60\x60python\nprint(arr[1])\n\x60\x60\x60",
        ),
    ],
)
def test_agent_delivers_its_own_reply_without_tools(
    search, mock_bridge, question, answer
):
    async def frames(_client, _payload, _results):
        yield {"type": "start", "attempt": 1}
        yield {"type": "end", "attempt": 1, "state": "completed"}
        yield {"type": "done", "text": answer}

    mock_bridge(frames)
    response = search.client.post(URL, json={"question": question})
    assert response.status_code == 200
    assert "event: tool" not in response.text
    assert '"stage":"retrieving"' not in response.text
    assert event_payload(response.text, "done") == {
        "status": "answered",
        "answer": answer,
        "citations": [],
        "turn_id": event_payload(response.text, "turn")["turn_id"],
    }
    history = search.client.get(
        f"{SESSIONS}/{event_payload(response.text, 'turn')['session_id']}"
    ).json()
    assert history["turns"][0]["result"]["answer"] == answer


def test_delivers_grounded_answer_with_a_natural_limits_explanation(search, mock_dsh):
    claim = "对重大风险隐患可以进行专项检查。"
    limits = "现有条文没有说明检查期限。可以补充所问制度或业务场景，我再帮你核对。"

    def reply(_payload, results):
        if not results:
            return tool_frame()
        marker = find_evidence(results, "第六条")["id"]
        return f"{claim}[[{marker}]]\n\n{limits}"

    mock_dsh(reply)
    response = search.client.post(
        URL, json={"question": "专项检查适用什么情形？有期限吗？"}
    )
    assert event_payload(response.text, "answer_delta") == {
        "text": f"{claim}\n\n{limits}"
    }
    result = event_payload(response.text, "done")
    assert result["status"] == "answered"
    assert result["answer"] == f"{claim}[1]\n\n{limits}"
    assert len(result["citations"]) == 1


def test_missing_evidence_still_streams_a_natural_reply(search, mock_dsh):
    reply = "我暂时无法核对具体监管比例。你问的是银行、证券还是保险机构？"
    mock_dsh(lambda *_: reply)
    response = search.client.post(URL, json={"question": "法定流动性覆盖率是多少？"})
    result = event_payload(response.text, "done")
    assert (result["status"], result["answer"], result["citations"]) == (
        "answered",
        reply,
        [],
    )
    assert event_payload(response.text, "answer_delta") == {"text": reply}
    assert response.text.index("event: answer_delta") < response.text.index(
        "event: done"
    )


@pytest.mark.parametrize(
    "claim",
    [
        "只有发现重大风险隐患才可以进行专项检查。",
        "第三条允许进行专项检查。",
        "证监会令第938号允许进行专项检查。",
        "本条自 2022 年 1 月 1 日起执行。",
    ],
)
def test_article_citation_cannot_support_a_different_legal_claim(
    search, mock_dsh, claim
):
    def reply(_payload, results):
        if not results:
            return tool_frame()
        marker = find_evidence(results, "第六条")["id"]
        return f"{claim}[[{marker}]]"

    mock_dsh(reply)
    response = search.client.post(URL, json={"question": "重大风险时可以怎么处理？"})
    assert event_payload(response.text, "done")["status"] == "insufficient_evidence"
    assert claim not in event_payload(response.text, "done")["answer"]


def test_version_source_is_rendered_in_its_table_cell(search, mock_dsh):
    def reply(_payload, results):
        if not results:
            return [
                tool_frame(),
                tool_frame(
                    "get_document_status",
                    {"document": search.ids.securities.title},
                    call_id="version",
                ),
            ]
        article = find_evidence(results, "第六条")["id"]
        edition = results["version"]["evidence"][0]["id"]
        return (
            "| 项目 | 内容 |\n| --- | --- |\n"
            f"| 条文 | 可以进行专项检查。[[{article}]] |\n"
            f"| 时效 | [[{edition}]] |"
        )

    mock_dsh(reply)
    response = search.client.post(
        URL, json={"question": "用表格列出专项检查和制度时效。"}
    )
    result = event_payload(response.text, "done")
    assert result["status"] == "answered"
    assert "| 条文 | 可以进行专项检查。[1] |" in result["answer"]
    assert "| 时效 | 《证券公司风险处置条例》" in result["answer"]
    assert "时效待核验。[2] |" in result["answer"]
    assert [cite["kind"] for cite in result["citations"]] == ["chunk", "version"]


@pytest.mark.parametrize("known", [True, False])
def test_citation_after_table_applies_to_the_table(search, mock_dsh, known):
    table = "| 项目 | 内容 |\n| --- | --- |\n| 第六条 | 可以进行专项检查。 |"

    def reply(_payload, results):
        if not results:
            return tool_frame()
        marker = find_evidence(results, "第六条")["id"] if known else "invented"
        return f"{table}\n\n[[{marker}]]"

    mock_dsh(reply)
    response = search.client.post(URL, json={"question": "用表格概括第六条。"})
    result = event_payload(response.text, "done")
    if known:
        assert result["status"] == "answered"
        assert result["answer"] == f"{table}\n\n[1]"
        assert result["citations"][0]["article"] == "第六条"
    else:
        assert result["status"] == "insufficient_evidence"
        assert table not in result["answer"]


@pytest.mark.parametrize("known", [True, False])
def test_single_bracket_alias_is_hidden_in_preview_and_checked_on_delivery(
    search, mock_bridge, known
):
    claim = "第六条规定可以进行专项检查。"

    async def frames(_client, _payload, results):
        yield tool_frame()
        marker = find_evidence(results, "第六条")["id"]
        if not known:
            marker = marker[:-1] + ("0" if marker[-1] != "0" else "1")
        yield {"type": "text", "text": f"{claim}[{marker[:6]}"}
        yield {"type": "text", "text": f"{marker[6:]}]"}
        yield {"type": "done", "text": f"{claim}[{marker}]"}

    mock_bridge(frames)
    response = search.client.post(URL, json={"question": "第六条怎么规定？"})
    assert list(event_payloads(response.text, "answer_delta")) == [{"text": claim}]
    result = event_payload(response.text, "done")
    if known:
        assert result["answer"] == f"{claim}[1]"
        assert result["citations"][0]["article"] == "第六条"
    else:
        assert result["status"] == "insufficient_evidence"
        assert claim not in result["answer"]


def test_prose_after_a_fenced_example_cannot_cite_a_fabricated_source(search, mock_dsh):
    mock_dsh(lambda *_: "~~~text\n合法的示例\n~~~\n监管结论。[[eforged]]")
    response = search.client.post(URL, json={"question": "展示一段示例"})
    assert event_payload(response.text, "done")["status"] == "partial"
    assert "[[eforged]]" not in response.text


@pytest.mark.parametrize("source_state", ["published", "archived", "outside_scope"])
def test_rewrite_reuses_only_visible_evidence_from_an_approved_turn(
    search,
    mock_dsh,
    source_state,
):
    previous_turn = None
    previous_marker = None
    session_id = search.client.post(SESSIONS).json()["id"]

    def reply(payload, results):
        nonlocal previous_marker
        rewriting = payload["question"] == "把上一次的回答整理成列表"
        if not results:
            if not rewriting:
                return tool_frame()
            assert payload["history"][0]["answer_excerpt"] == "可以进行专项检查。[1]"
            return tool_frame("reuse_answer_evidence", {"turn_id": previous_turn})
        if not rewriting:
            previous_marker = find_evidence(results, "第六条")["id"]
            return f"可以进行专项检查。[[{previous_marker}]]"
        if source_state != "published":
            assert results["tool-1"]["error"] == "HISTORY_EVIDENCE_UNAVAILABLE"
            return draft()
        (source,) = results["tool-1"]["evidence"]
        assert source["id"] != previous_marker
        return f"- 可以进行专项检查。[[{source['id']}]]"

    mock_dsh(reply)
    first = search.client.post(
        URL,
        json={
            "session_id": session_id,
            "question": "重大风险如何处理？",
        },
    )
    previous_turn = event_payload(first.text, "turn")["turn_id"]
    body = {"session_id": session_id, "question": "把上一次的回答整理成列表"}
    if source_state == "archived":
        with search.db() as db:
            db.get(DocumentVersion, search.ids.current.id).status = "archived"
            db.commit()
    elif source_state == "outside_scope":
        body["document_ids"] = [str(search.ids.payment.id)]

    response = search.client.post(URL, json=body)
    done = event_payload(response.text, "done")
    assert all(
        step["tool"] == "reuse_answer_evidence"
        for step in event_payloads(response.text, "tool")
    )
    if source_state == "published":
        assert done["answer"] == "- 可以进行专项检查。[1]"
        assert done["citations"] == event_payload(first.text, "done")["citations"]
    else:
        assert done["status"] == "answered" and done["answer"] == draft()
        assert not done["citations"]


def test_session_restores_answer_and_supplies_fresh_followup_context(
    search, mock_dsh, session_owner
):
    first, sixth = search.ids.current_chunks
    title = search.ids.securities.title
    previous_evidence = None

    def complete(payload, results):
        nonlocal previous_evidence
        following = payload["question"] == "第六条呢？"
        if not results:
            if following:
                (hint,) = payload["history"]
                assert hint["references"][0]["title"] == title
                assert hint["references"][0]["article"] == "第一条"
                assert hint["answer_excerpt"] == "上一轮答复正文。[1]"
            return tool_frame(
                "get_article",
                {"document": title, "article": "第六条" if following else "第一条"},
            )
        (source,) = results["tool-1"]["evidence"]
        assert source["article"] == ("第六条" if following else "第一条")
        answer = {
            "text": "需要专项检查。" if following else "上一轮答复正文。",
            "evidence_ids": [source["id"]],
        }
        if following:
            assert source["id"] != previous_evidence
            return draft(
                answer, {"text": "上一轮的原引用", "evidence_ids": [previous_evidence]}
            )
        previous_evidence = source["id"]
        return draft(answer)

    mock_dsh(complete)
    created = search.client.post(SESSIONS)
    assert created.status_code == 201, created.text
    session_id = created.json()["id"]
    initial = search.client.post(
        URL, json={"session_id": session_id, "question": f"《{title}》第一条是什么？"}
    )
    first_turn = event_payload(initial.text, "turn")["turn_id"]
    tools = [
        json.loads(line[6:])
        for frame in initial.text.split("\n\n")
        if "event: tool" in frame.splitlines()
        for line in frame.splitlines()
        if line.startswith("data: ")
    ]
    assert tools == [
        {
            "id": "tool-1",
            "tool": "get_article",
            "state": "running",
            "target": "证券公司风险处置条例 · 第1条",
            "summary": None,
        },
        {
            "id": "tool-1",
            "tool": "get_article",
            "state": "completed",
            "target": "证券公司风险处置条例 · 第1条",
            "summary": "返回 1 段条文",
        },
    ]
    assert event_payload(initial.text, "done")["turn_id"] == first_turn
    history = search.client.get(f"{SESSIONS}/{session_id}").json()["turns"]
    assert len(history) == 1 and history[0]["id"] == first_turn
    assert history[0]["state"] == "completed"
    assert history[0]["tool_steps"] == [tools[-1]]
    assert history[0]["result"]["citations"][0]["chunk_id"] == first

    followup = search.client.post(
        URL, json={"session_id": session_id, "question": "第六条呢？"}
    )
    resumed = event_payload(followup.text, "done")
    assert [citation["chunk_id"] for citation in resumed["citations"]] == [sixth]
    assert "上一轮的原引用" not in resumed["answer"]
    history = search.client.get(f"{SESSIONS}/{session_id}").json()["turns"]
    assert [turn["question"] for turn in history] == [
        f"《{title}》第一条是什么？",
        "第六条呢？",
    ]


def test_chat_session_is_private_even_for_admin(search, session_owner):
    created = search.client.post(SESSIONS)
    assert created.status_code == 201, created.text
    session_id = created.json()["id"]
    app.dependency_overrides[require_current_user] = lambda: make_user("admin")
    assert search.client.get(f"{SESSIONS}/{session_id}").status_code == 404
    denied = search.client.post(
        URL, json={"session_id": session_id, "question": "继续"}
    )
    assert denied.status_code == 404


def test_lists_searches_and_archives_owned_sessions(search, session_owner, mock_dsh):
    mock_dsh(lambda *_: draft())
    first = search.client.post(SESSIONS).json()["id"]
    second = search.client.post(SESSIONS).json()["id"]
    assert (
        search.client.post(
            URL, json={"session_id": first, "question": "证券公司风险处置"}
        ).status_code
        == 200
    )
    renamed = search.client.patch(f"{SESSIONS}/{first}", json={"title": "风险笔记"})
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "风险笔记"

    page = search.client.get(SESSIONS, params={"limit": 1}).json()
    assert [item["id"] for item in page["items"]] == [first]
    next_page = search.client.get(SESSIONS, params={"cursor": page["next_cursor"]})
    assert [item["id"] for item in next_page.json()["items"]] == [second]
    matches = search.client.get(SESSIONS, params={"q": "公司风险"}).json()
    assert [item["id"] for item in matches["items"]] == [first]

    assert (
        search.client.patch(f"{SESSIONS}/{first}", json={"archived": True}).json()[
            "archived"
        ]
        is True
    )
    assert [row["id"] for row in search.client.get(SESSIONS).json()["items"]] == [
        second
    ]
    archived = search.client.get(SESSIONS, params={"archived": True}).json()
    assert [item["id"] for item in archived["items"]] == [first]
    assert search.client.get(f"{SESSIONS}/{first}").status_code == 200
    blocked = search.client.post(URL, json={"session_id": first, "question": "继续"})
    assert (blocked.status_code, blocked.json()["code"]) == (
        409,
        "CHAT_SESSION_ARCHIVED",
    )
    assert (
        search.client.patch(f"{SESSIONS}/{first}", json={"archived": False}).json()[
            "archived"
        ]
        is False
    )

    app.dependency_overrides[require_current_user] = lambda: make_user("admin")
    assert search.client.get(SESSIONS, params={"q": "公司风险"}).json()["items"] == []
    assert (
        search.client.patch(f"{SESSIONS}/{first}", json={"archived": True}).status_code
        == 404
    )


def test_pinned_sessions_sort_first_across_pages(search, session_owner):
    older, newer = [search.client.post(SESSIONS).json()["id"] for _ in range(2)]
    path = f"{SESSIONS}/{older}"
    original = search.client.get(path).json()["updated_at"]
    pinned = search.client.patch(path, json={"pinned": True})
    assert pinned.status_code == 200
    assert pinned.json()["updated_at"] == original
    assert search.client.get(path).json()["pinned"] is True

    first = search.client.get(SESSIONS, params={"limit": 1}).json()
    assert [item["id"] for item in first["items"]] == [older]
    second = search.client.get(SESSIONS, params={"cursor": first["next_cursor"]})
    assert [item["id"] for item in second.json()["items"]] == [newer]

    for pinned_only, expected in [(True, older), (False, newer)]:
        page = search.client.get(
            SESSIONS, params={"pinned": pinned_only, "limit": 1}
        ).json()
        assert [item["id"] for item in page["items"]] == [expected]
        assert page["next_cursor"] is None

    unpinned = search.client.patch(path, json={"pinned": False})
    assert unpinned.json()["pinned"] is False
    assert [item["id"] for item in search.client.get(SESSIONS).json()["items"]] == [
        newer,
        older,
    ]


def test_delete_cascades_owned_events_and_rejects_busy_or_foreign_sessions(
    search, session_owner, mock_dsh
):
    mock_dsh(lambda *_: draft())
    session_id = search.client.post(SESSIONS).json()["id"]
    path = f"{SESSIONS}/{session_id}"
    assert (
        search.client.post(
            URL, json={"session_id": session_id, "question": "待删除的记录"}
        ).status_code
        == 200
    )

    assert search.client.patch(path, json={"pinned": True}).status_code == 200
    app.dependency_overrides[require_current_user] = lambda: make_user("admin")
    assert search.client.patch(path, json={"pinned": False}).status_code == 404
    assert search.client.delete(path).status_code == 404
    app.dependency_overrides[require_current_user] = lambda: session_owner

    with search.db() as db:
        row = db.get(ChatSession, UUID(session_id))
        row.active_until = utc_now() + timedelta(minutes=1)
        db.commit()
    locked = search.client.delete(path)
    assert (locked.status_code, locked.json()["code"]) == (409, "CHAT_SESSION_BUSY")

    with search.db() as db:
        row = db.get(ChatSession, UUID(session_id))
        row.active_until = utc_now() - timedelta(seconds=1)
        db.commit()
        assert (
            db.query(ChatSessionEvent).filter_by(session_id=UUID(session_id)).count()
            > 0
        )
    assert search.client.delete(path).status_code == 204
    assert search.client.get(path).status_code == 404
    with search.db() as db:
        assert (
            db.query(ChatSessionEvent).filter_by(session_id=UUID(session_id)).count()
            == 0
        )


def test_history_pages_are_turn_aligned_and_stay_at_the_observed_cut(
    search, session_owner, mock_dsh
):
    mock_dsh(lambda *_: draft())
    session_id = search.client.post(SESSIONS).json()["id"]
    for question in ("最早一轮", "中间一轮", "最新一轮"):
        assert (
            search.client.post(
                URL, json={"session_id": session_id, "question": question}
            ).status_code
            == 200
        )

    url = f"{SESSIONS}/{session_id}"
    first = search.client.get(url, params={"limit": 2}).json()
    assert [turn["question"] for turn in first["turns"]] == ["中间一轮", "最新一轮"]
    assert all(turn["state"] == "completed" for turn in first["turns"])
    assert (
        search.client.post(
            URL, json={"session_id": session_id, "question": "分页之后"}
        ).status_code
        == 200
    )
    older = search.client.get(
        url,
        params={
            "before_seq": first["next_before_seq"],
            "through_seq": first["through_seq"],
        },
    ).json()
    assert [turn["question"] for turn in older["turns"]] == ["最早一轮"]
    assert older["turns"][0]["state"] == "completed"
    assert older["next_before_seq"] is None
    assert older["through_seq"] == first["through_seq"]


@pytest.mark.parametrize("explicit_session", [True, False])
def test_dsh_turn_reuses_finoracle_tools_and_rejects_stale_evidence(
    search, session_owner, monkeypatch, explicit_session
):
    monkeypatch.setenv("CHAT_DSH_URL", "http://dsh.test")
    monkeypatch.setenv("CHAT_DSH_TOKEN", "unit-private-token")
    first, _ = search.ids.current_chunks
    title = search.ids.securities.title
    with search.db() as database:
        chunk = database.get(DocumentChunk, first)
        version = database.get(DocumentVersion, chunk.version_id)
        version.effective_date = date(2026, 1, 1)
        database.commit()
    session_id = search.client.post(SESSIONS).json()["id"] if explicit_session else None
    invoked = asyncio.Event()
    evidence_id = None
    submitted = {}
    submissions = []
    encode = json.dumps

    async def answer_tool(client, url, *, headers, json):
        nonlocal evidence_id
        assert url == f"http://dsh.test/turns/{submitted['turn_id']}/tools/call-1"
        assert headers["Authorization"] == "Bearer unit-private-token"
        (source,) = json["evidence"]
        assert source["article"] == "第一条"
        assert source["effective_date"] == "2026-01-01"
        assert source["id"] != first
        evidence_id = source["id"]
        invoked.set()
        return httpx.Response(204, request=httpx.Request("POST", url))

    @asynccontextmanager
    async def run(client, method, url, *, headers, json: dict):
        assert url == "http://dsh.test/turns"
        submissions.append(json)
        if len(submissions) == 1:
            yield httpx.Response(409, request=httpx.Request(method, url))
            return
        submitted.update(json)
        if explicit_session:
            assert submitted["session_id"] == session_id
        prompt = submitted["contract"]["system_prompt"]
        assert prompt.startswith(SYSTEM_PROMPT)
        assert "普通 Markdown" in prompt and "[[e" in prompt
        assert submitted["contract"]["tools"] == TOOL_DEFINITIONS
        assert submitted["contract"]["max_model_calls"] == MAX_MODEL_CALLS

        async def lines():
            yield encode(
                {
                    "type": "tool",
                    "id": "call-1",
                    "name": "get_article",
                    "arguments": {"document": title, "article": "第一条"},
                }
            )
            assert invoked.is_set()
            content = draft(
                {"text": "有效依据", "evidence_ids": [evidence_id]},
                {"text": "旧轮次依据", "evidence_ids": [first]},
            )
            yield encode({"type": "text", "text": content})
            yield encode({"type": "done", "text": content})

        response = httpx.Response(200, request=httpx.Request(method, url))
        monkeypatch.setattr(response, "aiter_lines", lines)
        yield response

    monkeypatch.setattr(httpx.AsyncClient, "stream", run)
    monkeypatch.setattr(httpx.AsyncClient, "post", answer_tool)
    body = {"question": "查第一条"}
    if explicit_session:
        body["session_id"] = session_id
    answer = search.client.post(URL, json=body)
    started = event_payload(answer.text, "turn")
    assert started["session_id"] == submitted["session_id"]
    result = event_payload(answer.text, "done")
    assert result["turn_id"] == submitted["turn_id"]
    assert len(submissions) == 2 and submissions[0] == submissions[1]
    assert invoked.is_set()
    assert "有效依据" in result["answer"] and "旧轮次依据" not in result["answer"]
    assert result["citations"][0]["chunk_id"] == first
    (restored,) = search.client.get(f"{SESSIONS}/{started['session_id']}").json()[
        "turns"
    ]
    assert restored["id"] == started["turn_id"]


def test_cross_session_reference_field_is_rejected(search, session_owner, mock_dsh):
    mock_dsh(lambda *_: draft())
    source = search.client.post(SESSIONS).json()["id"]
    target = search.client.post(SESSIONS).json()["id"]
    response = search.client.post(
        URL,
        json={
            "session_id": target,
            "question": "核对制度",
            "reference_session_ids": [source],
        },
    )
    assert response.status_code == 422
    assert search.client.get(f"{SESSIONS}/{target}").json()["turns"] == []


def test_repeating_request_id_returns_the_committed_turn(
    search, session_owner, mock_dsh
):
    requests = 0

    def complete(*_):
        nonlocal requests
        requests += 1
        return draft()

    mock_dsh(complete)
    session_id = search.client.post(SESSIONS).json()["id"]
    data = {
        "session_id": session_id,
        "question": "同一次提问",
        "request_id": "60f582fe-8037-43a9-b755-24fa7d0a3c22",
    }
    first = event_payload(search.client.post(URL, json=data).text, "done")
    assert event_payload(search.client.post(URL, json=data).text, "done") == first
    # Existing events can still contain the old cross-session fields.
    with search.db() as db:
        event = db.get(ChatSessionEvent, (UUID(session_id), 0))
        event.payload = {
            **event.payload,
            "scope": {
                **event.payload["scope"],
                "references": ["757b2ac0-9b76-4365-bdd6-27da560fb70e"],
            },
            "session_references": [
                {
                    "session_id": "757b2ac0-9b76-4365-bdd6-27da560fb70e",
                    "title": "历史资料",
                    "through_seq": 0,
                }
            ],
        }
        db.commit()
    assert event_payload(search.client.post(URL, json=data).text, "done") == first
    for change in (
        {"question": "另一条问题"},
        {"document_ids": [str(search.ids.securities.id)]},
    ):
        conflict = search.client.post(URL, json={**data, **change})
        assert (conflict.status_code, conflict.json()["code"]) == (
            409,
            "CHAT_REQUEST_CONFLICT",
        )
    assert requests == 1
    (restored,) = search.client.get(f"{SESSIONS}/{session_id}").json()["turns"]
    assert restored["result"]["answer"] == first["answer"]
    assert "session_references" not in restored


@pytest.mark.parametrize("failure", [httpx.ReadTimeout, RuntimeError])
def test_session_records_failed_turn_and_rejects_parallel_question(
    search, mock_dsh, session_owner, failure
):
    created = search.client.post(SESSIONS)
    assert created.status_code == 201, created.text
    session_id = created.json()["id"]
    body = {"session_id": session_id, "question": "怎么处理？"}

    def complete(*_args):
        assert search.client.post(URL, json=body).status_code == 409
        raise failure("upstream details")

    mock_dsh(complete)
    response = search.client.post(URL, json=body)
    assert event_payload(response.text, "error")["code"] == "CHAT_UNAVAILABLE"
    restored = search.client.get(f"{SESSIONS}/{session_id}").json()["turns"]
    assert len(restored) == 1 and restored[0]["state"] == "failed"
    assert restored[0]["result"] is None


def test_disconnected_session_restores_verified_prefix(
    search, mock_bridge, session_owner, monkeypatch
):
    session_id = search.client.post(SESSIONS).json()["id"]
    chunk_id = search.ids.current_chunks[1]

    async def frames(_client, _payload, results):
        yield tool_frame()
        yield {"type": "start", "attempt": 1}
        yield {"type": "end", "attempt": 1, "state": "completed"}
        marker = find_evidence(results, "第六条")["id"]
        yield {"type": "done", "text": f"可以进行专项检查。[[{marker}]]"}

    mock_bridge(frames)
    from app.services import chat_sessions

    finish = chat_sessions.finish_turn

    async def finish_after_disconnect(db, turn, state, result, **kwargs):
        if state == "completed":
            await asyncio.Future()
        return await finish(db, turn, state, result, **kwargs)

    monkeypatch.setattr(chat_sessions, "finish_turn", finish_after_disconnect)

    async def exercise():
        disconnected = asyncio.Event()

        async def send(message):
            if b"event: paragraph" in message.get("body", b""):
                disconnected.set()

        body = {"session_id": session_id, "question": "发现重大风险怎么处理？"}
        await asyncio.wait_for(
            post_stream(app, send, body=body, disconnected=disconnected), timeout=3
        )
        assert disconnected.is_set()

    asyncio.run(exercise())
    (turn,) = search.client.get(f"{SESSIONS}/{session_id}").json()["turns"]
    assert turn["state"] == "interrupted"
    assert turn["result"]["answer"] == "可以进行专项检查。[1]"
    assert turn["result"]["citations"][0]["chunk_id"] == chunk_id
    assert turn["execution"] == [
        *turn["tool_steps"],
        {"attempt": 1, "state": "completed"},
    ]


@pytest.mark.parametrize("invalid_reference", [90, 0, "invalid"])
def test_chat_only_returns_citations_for_current_evidence(
    search, mock_dsh, invalid_reference
):
    wanted = search.ids.current_chunks[1]

    def complete(payload, results):
        if not results:
            assert (
                payload["contract"]["tools"][0]["function"]["name"]
                == "search_knowledge"
            )
            return tool_frame()
        assert {row["article"] for row in results["tool-1"]["evidence"]} <= {
            "第一条",
            "第六条",
        }
        return draft(
            {
                "text": "存在重大风险隐患时可以进行专项检查。",
                "evidence_ids": [find_evidence(results, "第六条")["id"]],
            },
            {"text": "这段没有有效依据。", "evidence_ids": [invalid_reference]},
        )

    mock_dsh(complete)
    response = search.client.post(
        URL,
        json={
            "question": "证券公司存在重大风险时如何处理？",
            "document_ids": [str(search.ids.securities.id)],
        },
    )

    assert response.status_code == 200, response.text
    assert "no-store" in response.headers["cache-control"]
    result = event_payload(response.text, "done")
    assert result["status"] == "partial"
    assert "专项检查。[1]" in result["answer"]
    assert "这段没有有效依据" not in result["answer"]
    citation = result["citations"][0]
    assert citation["chunk_id"] == wanted
    assert citation["number"] == 1
    assert "content" not in citation

    reference_url = f"/api/v1/chat/references/chunk/{wanted}"
    reference = search.client.get(reference_url).json()
    assert reference["article"] == "第六条"
    assert reference["page_start"] == 2
    assert "专项检查" in reference["content"]

    with search.db() as session:
        session.get(DocumentVersion, search.ids.current.id).status = "archived"
        session.commit()
    assert search.client.get(reference_url).status_code == 404


def test_chat_declines_unretrieved_claims(search, mock_dsh):
    def complete(*_args):
        chunk_id = search.ids.current_chunks[1]
        return draft({"text": "无依据的结论", "evidence_ids": [chunk_id]})

    mock_dsh(complete)
    response = search.client.post(URL, json={"question": "怎么处理？"})
    assert response.status_code == 200, response.text
    result = event_payload(response.text, "done")
    assert result["status"] == "insufficient_evidence"
    assert result["citations"] == []
    assert "无依据的结论" not in result["answer"]
    assert "event: paragraph" not in response.text


def test_get_article_preserves_order_and_citations_for_all_parts(search, mock_dsh):
    first, second = search.ids.current_chunks[1], 21
    with search.db() as session:
        version = session.get(DocumentVersion, search.ids.current.id)
        version.document_number = "证监会令第203号"
        original = session.get(DocumentChunk, first)
        original.article = "第六条之一"
        original.part_count = 2
        add_article_chunk(
            session,
            version,
            second,
            3,
            "第六条之一",
            "检查范围另有明确规定。",
            part=2,
        )
        session.commit()

    def complete(payload, results):
        if not results:
            assert any(
                tool["function"]["name"] == "get_article"
                for tool in payload["contract"]["tools"]
            )
            return tool_frame(
                "get_article", {"document": "证监会令第203号", "article": "第 ６ 条之1"}
            )
        evidence = next(reversed(results.values()))["evidence"]
        assert [item["article"] for item in evidence] == ["第六条之一", "第六条之一"]
        assert evidence[1]["content"] == "检查范围另有明确规定。"
        return draft(
            {
                "text": "可开展专项检查，范围另有规定。",
                "evidence_ids": [item["id"] for item in evidence],
            }
        )

    mock_dsh(complete)
    response = search.client.post(
        URL, json={"question": "证监会令第203号第六条之一是什么？"}
    )
    result = event_payload(response.text, "done")
    assert result["status"] == "answered"
    assert [item["chunk_id"] for item in result["citations"]] == [first, second]
    reference = search.client.get(f"/api/v1/chat/references/chunk/{second}").json()
    assert reference["content"] == "检查范围另有明确规定。"


@pytest.mark.parametrize(
    ("case", "error"),
    [
        ("unknown_article", "ARTICLE_NOT_FOUND"),
        ("out_of_scope", "DOCUMENT_NOT_FOUND"),
        ("archived_number", "DOCUMENT_NOT_FOUND"),
        ("ready_document", "DOCUMENT_NOT_FOUND"),
    ],
)
def test_get_article_returns_no_evidence_for_missing_or_hidden_sources(
    search, mock_dsh, case, error
):
    document, article = "《证券公司风险处置条例》", "第一条"
    scope = [str(search.ids.securities.id)]
    if case == "unknown_article":
        article = "第999条"
    elif case == "out_of_scope":
        scope = [str(search.ids.payment.id)]
    elif case == "archived_number":
        with search.db() as session:
            archived = session.query(DocumentVersion).filter_by(status="archived").one()
            archived.document_number = "证监会令第18号"
            add_article_chunk(session, archived, 21, 1, "第一条", "旧版原文。")
            session.commit()
        document = "证监会令第18号"
    elif case == "ready_document":
        document = "证券登记结算办法"
        scope = [str(search.ids.draft.id)]

    def complete(payload, results):
        if not results:
            return tool_frame("get_article", {"document": document, "article": article})
        lookup = next(reversed(results.values()))
        assert lookup["error"] == error and not lookup.get("evidence")
        return draft()

    mock_dsh(complete)
    response = search.client.post(
        URL, json={"question": "请提供指定条文。", "document_ids": scope}
    )
    result = event_payload(response.text, "done")
    assert result["status"] == "answered" and result["answer"] == draft()
    assert result["citations"] == []


def test_get_article_allows_followup_disambiguation_without_merging_articles(
    search, mock_dsh
):
    with search.db() as session:
        version = session.get(DocumentVersion, search.ids.current.id)
        add_article_chunk(
            session, version, 22, 3, "第一条", "附件第一条原文。", chapter="附件"
        )
        session.commit()

    args = {"document": str(search.ids.securities.id), "article": "第1条"}

    def complete(payload, results):
        if not results:
            return tool_frame("get_article", args)
        lookup = next(reversed(results.values()))
        if "error" in lookup:
            assert lookup["error"] == "ARTICLE_AMBIGUOUS"
            assert [location["start_ordinal"] for location in lookup["locations"]] == [
                1,
                3,
            ]
            assert "evidence" not in lookup
            return tool_frame(
                "get_article", {**args, "start_ordinal": 3}, call_id="confirmed"
            )
        (source,) = lookup["evidence"]
        assert source["content"] == "附件第一条原文。"
        return draft({"text": "附件第一条原文。", "evidence_ids": [source["id"]]})

    mock_dsh(complete)
    response = search.client.post(
        URL, json={"question": "《证券公司风险处置条例》附件第一条是什么？"}
    )
    result = event_payload(response.text, "done")
    assert [item["chunk_id"] for item in result["citations"]] == [22]


@pytest.mark.parametrize(
    ("validity", "checked_at", "display"),
    [("effective", date(2026, 11, 19), "现行有效"), ("unknown", None, "时效待核验")],
)
def test_document_status_uses_published_metadata_for_answer_and_reference(
    search, mock_dsh, validity, checked_at, display
):
    version_id = search.ids.current.id
    with search.db() as session:
        version = session.get(DocumentVersion, version_id)
        version.validity_status = validity
        version.validity_checked_at = checked_at
        version.document_number = "证监会令第203号"
        session.commit()

    def complete(payload, results):
        if not results:
            assert any(
                tool["function"]["name"] == "get_document_status"
                for tool in payload["contract"]["tools"]
            )
            return tool_frame("get_document_status", {"document": "证监会令第203号"})
        evidence = next(reversed(results.values()))["evidence"][0]
        assert evidence["validity_status"] == validity
        assert evidence["version_id"] == str(version_id)
        assert evidence["document_id"] == str(search.ids.securities.id)
        reference = [evidence["id"]]
        return draft(
            {"text": "这份制度已废止。", "evidence_ids": reference},
            {"text": "这里另行说明核验日期。", "evidence_ids": reference},
        )

    mock_dsh(complete)
    response = search.client.post(URL, json={"question": "证监会令第203号是否仍有效？"})
    done = event_payload(response.text, "done")
    assert done["status"] == "answered"
    assert done["answer"].count(display) == 1
    assert "已废止" not in done["answer"]
    assert event_payload(response.text, "paragraph")["citations"] == done["citations"]
    (citation,) = done["citations"]
    assert citation["kind"] == "version" and citation["version"]["id"] == str(
        version_id
    )
    assert "chunk_id" not in citation
    reference_url = f"/api/v1/chat/references/version/{version_id}"
    reference = search.client.get(reference_url)
    assert reference.status_code == 200
    assert reference.json()["version"]["validity_checked_at"] == (
        checked_at.isoformat() if checked_at else None
    )

    with search.db() as session:
        session.get(DocumentVersion, version_id).status = "archived"
        session.commit()
    assert search.client.get(reference_url).status_code == 404


@pytest.mark.parametrize(
    ("year", "expected"), [("2012年版", "partial"), ("2023", "answered")]
)
def test_document_status_rechecks_the_requested_edition(
    search, mock_dsh, year, expected
):
    with search.db() as session:
        version = session.get(DocumentVersion, search.ids.current.id)
        version.document_number = "国务院令2023年第203号"
        version.validity_status = "effective"
        session.commit()

    def complete(payload, results):
        if not results:
            return tool_frame(
                "get_document_status",
                {"document": "《证券公司风险处置条例》", "version_hint": year},
            )
        result = next(reversed(results.values()))
        assert result.get("error") == (
            "VERSION_NOT_FOUND" if expected == "partial" else None
        )
        evidence = result["evidence"][0]
        return draft({"text": "指定版本现行有效。", "evidence_ids": [evidence["id"]]})

    mock_dsh(complete)
    response = search.client.post(
        URL, json={"question": f"{year}证券公司风险处置条例还有效吗？"}
    )
    result = event_payload(response.text, "done")
    assert result["status"] == expected
    assert "国务院令2023年第203号" in result["answer"]
    assert ("无法确认所问版本" in result["answer"]) == (expected == "partial")
    assert result["citations"][0]["version"]["id"] == str(search.ids.current.id)


def test_document_status_honors_selected_scope(search, mock_dsh):
    def complete(payload, results):
        if not results:
            return tool_frame(
                "get_document_status", {"document": str(search.ids.securities.id)}
            )
        assert next(reversed(results.values()))["error"] == "DOCUMENT_NOT_FOUND"
        return draft()

    mock_dsh(complete)
    body = {
        "question": "证券公司风险处置条例现在有效吗？",
        "document_ids": [str(search.ids.payment.id)],
    }
    done = event_payload(search.client.post(URL, json=body).text, "done")
    assert done["answer"] == draft() and done["citations"] == []


def test_document_status_disambiguates_same_title_using_version_hint(search, mock_dsh):
    with search.db() as session:
        session.get(Document, search.ids.payment.id).title = search.ids.securities.title
        session.get(
            DocumentVersion, search.ids.current.id
        ).document_number = "国务院令2023年第203号"
        session.commit()

    def complete(payload, results):
        if not results:
            return tool_frame(
                "get_document_status",
                {
                    "document": search.ids.securities.title,
                    "version_hint": "国务院令2023年第203号",
                },
            )
        evidence = next(reversed(results.values()))["evidence"][0]
        assert evidence["document_id"] == str(search.ids.securities.id)
        return draft({"text": "时效。", "evidence_ids": [evidence["id"]]})

    mock_dsh(complete)
    done = event_payload(
        search.client.post(
            URL, json={"question": "国务院令2023年第203号那个版本有效吗？"}
        ).text,
        "done",
    )
    assert done["status"] == "answered"
    assert done["citations"][0]["version"]["id"] == str(search.ids.current.id)


@pytest.mark.parametrize("missing_version", [False, True])
def test_document_status_keeps_grounded_article_or_explains_missing_edition(
    search, mock_dsh, missing_version
):
    title = search.ids.securities.title

    def complete(payload, results):
        if not results:
            version_args = {"document": title}
            if missing_version:
                version_args["version_hint"] = "2012年版"
            return [
                tool_frame("get_document_status", version_args, call_id="version"),
                tool_frame("get_article", {"document": title, "article": "第六条"}),
            ]
        refs = [find_evidence(results, "第六条")["id"]]
        if not missing_version:
            refs.append(results["version"]["evidence"][0]["id"])
        return draft({"text": "所问版本肯定已废止。", "evidence_ids": refs})

    mock_dsh(complete)
    result = event_payload(
        search.client.post(
            URL, json={"question": "请核对所问版本的时效与第六条。"}
        ).text,
        "done",
    )
    assert "肯定已废止" not in result["answer"]
    if missing_version:
        assert result["status"] == "partial"
        assert "无法确认所问版本" in result["answer"]
        assert [item["kind"] for item in result["citations"]] == ["version"]
    else:
        assert result["status"] == "answered"
        assert "专项检查" in result["answer"]
        assert {item["kind"] for item in result["citations"]} == {"version", "chunk"}


def test_model_timeout_returns_retryable_error(search, mock_dsh):
    def complete(*_args):
        raise httpx.ReadTimeout("upstream details")

    mock_dsh(complete)
    response = search.client.post(URL, json={"question": "怎么处理？"})
    assert response.status_code == 200
    error = event_payload(response.text, "error")
    assert error["code"] == "CHAT_UNAVAILABLE"
    assert error["request_id"] == response.headers["x-request-id"]
    assert "upstream details" not in response.text
    assert "event: done" not in response.text


def test_bridge_has_no_idle_timeout_shorter_than_chat_deadline(search, mock_bridge):
    async def frames(client, _payload, _results):
        assert client.timeout.read is None
        yield {"type": "done", "text": draft()}

    mock_bridge(frames)
    response = search.client.post(URL, json={"question": "怎么处理？"})
    assert event_payload(response.text, "done")["answer"] == draft()


def test_chat_stream_reports_malformed_model_content(search, mock_bridge):
    async def frames(_client, _payload, _results):
        yield tool_frame()
        yield {"type": "done", "text": "\x00malformed model output"}

    mock_bridge(frames)
    response = search.client.post(URL, json={"question": "怎么处理？"})
    assert event_payload(response.text, "error")["code"] == "CHAT_UNAVAILABLE"
    assert "event: done" not in response.text


@pytest.mark.parametrize("streamed", [True, False])
def test_chat_rejects_tool_protocol_in_model_text(search, mock_bridge, streamed):
    raw = (
        '<｜｜DSML｜｜ calls>\n<｜｜DSML｜｜ invoke name="get_document_status">\n'
        '<｜｜DSML｜｜ parameter name="document" string="true">银行资本管理办法'
        "</｜｜DSML｜｜ parameter>\n</｜｜DSML｜｜ invoke>\n</｜｜DSML｜｜ calls>"
    )

    async def frames(_client, _payload, _results):
        yield {"type": "start", "attempt": 3}
        if streamed:
            for character in raw:
                yield {"type": "text", "text": character}
        yield {"type": "end", "attempt": 3, "state": "completed"}
        yield {"type": "done", "text": raw}

    mock_bridge(frames)
    response = search.client.post(URL, json={"question": "资本要求有哪些？"})
    assert event_payload(response.text, "error")["code"] == "CHAT_UNAVAILABLE"
    assert all(
        f"event: {name}" not in response.text
        for name in ("answer_delta", "paragraph", "done", "tool")
    )
    session_id = event_payload(response.text, "turn")["session_id"]
    (turn,) = search.client.get(f"{SESSIONS}/{session_id}").json()["turns"]
    assert turn["state"] == "failed" and turn["result"] is None


def test_bridge_receives_remaining_query_budget(search, mock_bridge):
    async def frames(_client, _payload, results):
        for index, article in enumerate(
            ("第一条", "第二条", "第三条", "第四条", "第六条")
        ):
            call_id = f"article-{index}"
            yield tool_frame(
                "get_article",
                {"document": search.ids.securities.title, "article": article},
                call_id=call_id,
            )
            assert results[call_id]["remaining_tool_calls"] == max(0, 3 - index)
        assert results["article-4"]["error"] == "TOOL_BUDGET_EXHAUSTED"
        yield {"type": "done", "text": "我只能概括本轮已经核到的条文。"}

    mock_bridge(frames)
    response = search.client.post(URL, json={"question": "相关条文有哪些？"})
    assert event_payload(response.text, "done")["status"] == "answered"


@pytest.mark.parametrize("complete", [True, False])
def test_chat_stream_reads_split_frames_and_rejects_truncation(
    search, mock_bridge, complete
):
    executed = []

    async def frames(_client, _payload, results):
        call = (json.dumps(tool_frame()) + "\n").encode()
        yield call[:17]
        if complete:
            yield call[17:]
            assert results["tool-1"]["evidence"]
            executed.extend(results)
            yield {"type": "done", "text": draft()}

    mock_bridge(frames)
    response = search.client.post(URL, json={"question": "重大风险怎么处理？"})
    if complete:
        assert event_payload(response.text, "done")["answer"] == draft()
        assert executed == ["tool-1"]
    else:
        assert event_payload(response.text, "error")["code"] == "CHAT_UNAVAILABLE"
        assert not executed
        assert "event: tool" not in response.text


def test_chat_stream_deadline_survives_slow_delivery(search, mock_dsh, monkeypatch):
    monkeypatch.setenv("CHAT_TIMEOUT_SECONDS", "0.35")

    def complete(_payload, results):
        if not results:
            return tool_frame()
        evidence_id = find_evidence(results, "第六条")["id"]
        return draft(
            *(
                {"text": f"段落 {index}", "evidence_ids": [evidence_id]}
                for index in range(10)
            )
        )

    mock_dsh(complete)

    async def exercise():
        wire = []

        async def slow_send(message):
            if body := message.get("body"):
                if b"event: progress" in body:
                    await asyncio.sleep(0.5)
                wire.append(body)

        await asyncio.wait_for(post_stream(app, slow_send), timeout=3)
        output = b"".join(wire).decode()
        assert event_payload(output, "error")["code"] == "CHAT_UNAVAILABLE"
        assert "event: done" not in output

    asyncio.run(exercise())


def test_chat_streams_text_before_completion_without_exposing_markers(
    search, mock_bridge
):
    async def exercise():
        messages = []
        first_text = asyncio.Event()

        async def frames(_client, _payload, results):
            yield tool_frame()
            yield {"type": "start", "attempt": 2}
            marker = find_evidence(results, "第六条")["id"]
            text = f"重大风险隐患可以进行专项检查。[[{marker}]]"
            yield {"type": "text", "text": "重大风险隐患可以"}
            await asyncio.wait_for(first_text.wait(), timeout=1)
            yield {"type": "text", "text": "进行专项检查。["}
            yield {"type": "text", "text": f"[{marker}]"}
            yield {"type": "text", "text": "]"}
            yield {"type": "end", "attempt": 2, "state": "completed"}
            yield {"type": "done", "text": text + "\n\n无依据内容[[e9999]]"}

        mock_bridge(frames)

        async def receive(message):
            body = message.get("body", b"")
            if b"event: answer_delta" in body:
                first_text.set()
            messages.append(body)

        await asyncio.wait_for(post_stream(app, receive), timeout=3)
        wire = b"".join(messages).decode()
        assert (
            wire.index("event: model")
            < wire.index("event: answer_delta")
            < wire.index("event: paragraph")
            < wire.index("event: done")
        )
        assert list(event_payloads(wire, "answer_delta")) == [
            {"text": "重大风险隐患可以"},
            {"text": "进行专项检查。"},
        ]
        assert "专项检查。[1]" in wire
        assert "无依据内容" not in wire
        assert event_payload(wire, "done")["status"] == "partial"

    asyncio.run(exercise())


def test_chat_rechecks_publication_before_delivering(search, mock_bridge):
    async def frames(_client, _payload, results):
        yield tool_frame()
        marker = find_evidence(results, "第六条")["id"]
        with search.db() as session:
            session.get(DocumentVersion, search.ids.current.id).status = "archived"
            session.commit()
        yield {"type": "done", "text": f"已下架资料正文[[{marker}]]"}

    mock_bridge(frames)
    response = search.client.post(URL, json={"question": "第六条是什么？"})
    result = event_payload(response.text, "done")
    assert result["status"] == "insufficient_evidence"
    assert "已下架资料正文" not in result["answer"]


def test_chat_does_not_save_provisional_text_from_failed_attempt(search, mock_bridge):
    async def frames(_client, _payload, results):
        yield tool_frame()
        marker = find_evidence(results, "第六条")["id"]
        yield {"type": "start", "attempt": 2}
        yield {"type": "text", "text": f"段落开头[[{marker}]]"}
        yield {"type": "end", "attempt": 2, "state": "failed"}
        yield {"type": "error", "code": "TURN_FAILED"}

    mock_bridge(frames)
    response = search.client.post(URL, json={"question": "第六条是什么？"})
    assert event_payload(response.text, "answer_delta") == {"text": "段落开头"}
    assert event_payload(response.text, "error")["code"] == "CHAT_UNAVAILABLE"
    assert "event: paragraph" not in response.text
    session_id = event_payload(response.text, "turn")["session_id"]
    (turn,) = search.client.get(f"{SESSIONS}/{session_id}").json()["turns"]
    assert turn["state"] == "failed" and turn["result"] is None


def test_chat_reports_actual_model_attempts_and_tool_details(search, mock_bridge):
    async def frames(_client, _payload, results):
        yield {"type": "start", "attempt": 1}
        yield {"type": "end", "attempt": 1, "state": "completed"}
        yield tool_frame()
        yield {"type": "start", "attempt": 2}
        yield {"type": "end", "attempt": 2, "state": "completed"}
        reference = find_evidence(results, "第六条")["id"]
        yield {
            "type": "done",
            "text": draft({"text": "制度正文。", "evidence_ids": [reference]}),
        }

    mock_bridge(frames)
    response = search.client.post(URL, json={"question": "重大风险如何处置？"})
    steps = [
        (event, json.loads(data[6:]))
        for frame in response.text.split("\n\n")
        for event in ("model", "tool")
        if f"event: {event}" in frame.splitlines()
        for data in frame.splitlines()
        if data.startswith("data: ")
    ]
    assert [event for event, _ in steps] == [
        "model",
        "model",
        "tool",
        "tool",
        "model",
        "model",
    ]
    assert steps[0][1] == {"attempt": 1, "state": "running"}
    assert steps[1][1] == {"attempt": 1, "state": "completed"}
    assert steps[2][1]["target"] == "证券公司 重大风险 专项检查"
    assert steps[3][1]["summary"].startswith("返回 ")
    assert steps[-1][1] == {"attempt": 2, "state": "completed"}
    session_id = event_payload(response.text, "turn")["session_id"]
    turn = search.client.get(f"{SESSIONS}/{session_id}").json()["turns"][0]
    assert turn["tool_steps"] == [steps[3][1]]
    assert turn["execution"] == [steps[1][1], steps[3][1], steps[5][1]]

    # Sessions saved before model calls were recorded still contain tool_steps.
    with search.db() as database:
        terminal = (
            database.query(ChatSessionEvent)
            .filter_by(session_id=UUID(session_id), kind="completed")
            .one()
        )
        terminal.payload = {
            "result": terminal.payload["result"],
            "tool_steps": [steps[3][1]],
        }
        database.commit()
    (restored,) = search.client.get(f"{SESSIONS}/{session_id}").json()["turns"]
    assert restored["execution"] == restored["tool_steps"] == [steps[3][1]]


async def post_stream(asgi, send, *, body=None, disconnected=None):
    received = False

    async def receive():
        nonlocal received
        if not received:
            received = True
            encoded = json.dumps(
                body or {"question": "证券公司有重大风险隐患怎么处理？"}
            ).encode()
            return {"type": "http.request", "body": encoded, "more_body": False}
        if disconnected is not None:
            await disconnected.wait()
            return {"type": "http.disconnect"}
        await asyncio.Future()

    await asgi(
        {
            "type": "http",
            "asgi": {
                "version": "3.0",
                "spec_version": "2.3" if disconnected is not None else "2.4",
            },
            "method": "POST",
            "path": URL,
            "headers": [(b"content-type", b"application/json")],
            "query_string": b"",
        },
        receive,
        send,
    )


def draft(*paragraphs):
    return (
        "\n\n".join(
            paragraph["text"]
            + "".join(f"[[{ref}]]" for ref in paragraph["evidence_ids"])
            for paragraph in paragraphs
        )
        or "目前缺少可核对的制度资料。"
    )


def add_article_chunk(
    session, version, chunk_id, ordinal, article, content, *, part=1, chapter=""
):
    session.add(
        DocumentChunk(
            id=chunk_id,
            version_id=version.id,
            document_id=version.document_id,
            ordinal=ordinal,
            chapter=chapter,
            article=article,
            part=part,
            part_count=part,
            content=content,
            content_sha256=content_digest(content),
            sources=["text"],
        )
    )


def find_evidence(results, article):
    return next(
        row for row in results["tool-1"]["evidence"] if row["article"] == article
    )


def tool_frame(name="search_knowledge", arguments=None, *, call_id="tool-1"):
    return {
        "type": "tool",
        "id": call_id,
        "name": name,
        "arguments": arguments or {"query": "证券公司 重大风险 专项检查"},
    }


def event_payloads(wire, name):
    for frame in wire.split("\n\n"):
        lines = frame.splitlines()
        if f"event: {name}" in lines:
            yield json.loads(
                next(line[6:] for line in lines if line.startswith("data: "))
            )


def event_payload(wire, name):
    for payload in event_payloads(wire, name):
        return payload
    pytest.fail(f"Missing {name} SSE event in {wire}")
