from datetime import date
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import require_current_user
from app.main import app
from app.models import Document, DocumentChunk, DocumentVersion
from app.models.chat_session import ChatSession, ChatSessionEvent
from app.models.document import content_digest
from tests.test_ingestion_api import make_user
from tests.test_search_api import search as search

URL = "/api/v1/favorites"


def _seed_published_chunk(search_env, *, status: str = "published") -> int:
    with search_env.db() as session:
        doc = Document(title="商业银行理财业务监督管理办法", issuer="银保监会")
        session.add(doc)
        session.flush()
        version = DocumentVersion(
            document_id=doc.id,
            status=status,
            document_number="银保监会令2018年第6号",
            version_note="2018年版",
            effective_date=date(2018, 9, 28),
            validity_status="effective",
            source_url="https://example.gov.cn/rule/6",
            file_name="rule.pdf",
            file_type="pdf",
            size_bytes=1024,
            sha256=uuid4().hex + uuid4().hex,
            object_key=f"docs/{uuid4().hex}.pdf",
            parse_options={},
        )
        session.add(version)
        session.flush()
        chunk = DocumentChunk(
            version_id=version.id,
            document_id=doc.id,
            ordinal=1,
            chapter="第三章",
            section="",
            article="第二十六条",
            heading="销售管理",
            content="商业银行销售理财产品，应当加强投资者适当性管理。",
            content_sha256=content_digest("商业银行销售理财产品"),
            page_start=5,
            page_end=5,
            sources=["text"],
        )
        session.add(chunk)
        session.commit()
        return chunk.id


def _seed_chat_turn(
    search_env, user_id, *, with_result: bool = True
) -> tuple[str, str]:
    with search_env.db() as session:
        chat = ChatSession(user_id=user_id, title="理财销售适当性", next_seq=2)
        session.add(chat)
        session.flush()
        turn_id = uuid4()
        session.add(
            ChatSessionEvent(
                session_id=chat.id,
                seq=0,
                turn_id=turn_id,
                kind="question",
                payload={"question": "理财产品销售有哪些适当性要求？"},
            )
        )
        if with_result:
            session.add(
                ChatSessionEvent(
                    session_id=chat.id,
                    seq=1,
                    turn_id=turn_id,
                    kind="completed",
                    payload={
                        "result": {
                            "status": "answered",
                            "answer": "商业银行销售理财产品应当加强投资者适当性管理。[1]",
                            "citations": [],
                        }
                    },
                )
            )
        session.commit()
        return str(chat.id), str(turn_id)


@pytest.fixture
def owner(search):
    with search.db() as session:
        user = make_user("user")
        session.add(user)
        session.commit()
    app.dependency_overrides[require_current_user] = lambda: user
    return user


def test_favorites_requires_login():
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get(f"{URL}/tree")
    assert (response.status_code, response.json()["code"]) == (401, "AUTH_REQUIRED")


def test_folder_tree_crud_constraints_and_cascade(search, owner):
    client = search.client
    assert client.get(f"{URL}/tree").json() == {
        "folders": [],
        "root_item_count": 0,
        "total_item_count": 0,
    }

    # Create root & nested folders
    bank = client.post(f"{URL}/folders", json={"name": "银行监管"}).json()
    assert bank["parent_id"] is None
    sec = client.post(f"{URL}/folders", json={"name": "证券法规"}).json()
    sub = client.post(
        f"{URL}/folders", json={"name": "理财办法", "parent_id": bank["id"]}
    ).json()
    assert sub["parent_id"] == bank["id"]

    # Duplicate sibling name rejected
    dup = client.post(f"{URL}/folders", json={"name": "银行监管"})
    assert (dup.status_code, dup.json()["code"]) == (409, "FAVORITE_FOLDER_EXISTS")

    # Unknown parent rejected
    missing = client.post(
        f"{URL}/folders", json={"name": "无效子目录", "parent_id": str(uuid4())}
    )
    assert (missing.status_code, missing.json()["code"]) == (
        404,
        "FAVORITE_FOLDER_NOT_FOUND",
    )

    # Rename and move folder
    renamed = client.patch(
        f"{URL}/folders/{sub['id']}", json={"name": "理财细则", "parent_id": sec["id"]}
    )
    assert renamed.status_code == 200
    assert (renamed.json()["name"], renamed.json()["parent_id"]) == (
        "理财细则",
        sec["id"],
    )

    # Cycle detection: cannot move sec into its own child sub
    cycle = client.patch(f"{URL}/folders/{sec['id']}", json={"parent_id": sub["id"]})
    assert (cycle.status_code, cycle.json()["code"]) == (400, "FAVORITE_FOLDER_CYCLE")

    # Max depth enforcement (MAX_FOLDER_DEPTH = 5)
    parent = sub["id"]
    for level in range(3, 6):
        created = client.post(
            f"{URL}/folders", json={"name": f"层级{level}", "parent_id": parent}
        )
        assert created.status_code == 201
        parent = created.json()["id"]
    too_deep = client.post(
        f"{URL}/folders", json={"name": "层级6", "parent_id": parent}
    )
    assert (too_deep.status_code, too_deep.json()["code"]) == (
        400,
        "FAVORITE_FOLDER_DEPTH_EXCEEDED",
    )

    # Move bank under depth-5 leaf also exceeds max depth
    move_deep = client.patch(f"{URL}/folders/{bank['id']}", json={"parent_id": parent})
    assert (move_deep.status_code, move_deep.json()["code"]) == (
        400,
        "FAVORITE_FOLDER_DEPTH_EXCEEDED",
    )

    # Move sub back to root with explicit null parent_id
    to_root = client.patch(f"{URL}/folders/{sub['id']}", json={"parent_id": None})
    assert to_root.status_code == 200
    assert to_root.json()["parent_id"] is None

    # Add a link inside sub, then delete sub -> cascades folder, descendants & item
    link = client.post(
        URL,
        json={
            "kind": "link",
            "folder_id": sub["id"],
            "title": "金融监管总局",
            "url": "https://www.nfra.gov.cn",
        },
    )
    assert link.status_code == 201
    assert client.delete(f"{URL}/folders/{sub['id']}").status_code == 204
    tree = client.get(f"{URL}/tree").json()
    assert {f["id"] for f in tree["folders"]} == {bank["id"], sec["id"]}
    assert tree["total_item_count"] == 0


def test_favorite_chunk_qa_link_and_lookup(search, owner):
    client = search.client
    chunk_id = _seed_published_chunk(search)
    draft_chunk_id = _seed_published_chunk(search, status="ready")
    session_id, turn_id = _seed_chat_turn(search, owner.id)
    _, pending_turn_id = _seed_chat_turn(search, owner.id, with_result=False)

    folder = client.post(f"{URL}/folders", json={"name": "常用制度"}).json()

    # Save published chunk
    chunk_fav = client.post(
        URL,
        json={
            "kind": "chunk",
            "chunk_id": chunk_id,
            "folder_id": folder["id"],
            "note": "重点适当性条款",
        },
    )
    assert chunk_fav.status_code == 201
    chunk_data = chunk_fav.json()
    assert chunk_data["source_key"] == f"chunk:{chunk_id}"
    assert "第二十六条" in chunk_data["title"]
    assert chunk_data["payload"]["validity_status"] == "effective"
    assert chunk_data["note"] == "重点适当性条款"

    # Duplicate chunk rejected
    dup_chunk = client.post(URL, json={"kind": "chunk", "chunk_id": chunk_id})
    assert (dup_chunk.status_code, dup_chunk.json()["code"]) == (
        409,
        "FAVORITE_ALREADY_EXISTS",
    )

    # Unpublished chunk rejected
    unpub = client.post(URL, json={"kind": "chunk", "chunk_id": draft_chunk_id})
    assert (unpub.status_code, unpub.json()["code"]) == (
        404,
        "FAVORITE_SOURCE_NOT_FOUND",
    )

    # Save Q&A turn to root
    qa_fav = client.post(
        URL,
        json={"kind": "qa", "session_id": session_id, "turn_id": turn_id},
    )
    assert qa_fav.status_code == 201
    qa_data = qa_fav.json()
    assert qa_data["folder_id"] is None
    assert qa_data["source_key"] == f"qa:{turn_id}"
    assert qa_data["payload"]["question"] == "理财产品销售有哪些适当性要求？"

    # Duplicate QA & incomplete QA rejected
    assert (
        client.post(
            URL, json={"kind": "qa", "session_id": session_id, "turn_id": turn_id}
        ).status_code
        == 409
    )
    assert (
        client.post(
            URL,
            json={"kind": "qa", "session_id": session_id, "turn_id": pending_turn_id},
        ).status_code
        == 404
    )

    # Save custom link
    link_fav = client.post(
        URL,
        json={
            "kind": "link",
            "title": "中国人民银行",
            "url": "https://www.pbc.gov.cn",
            "note": "官网法规库",
        },
    )
    assert link_fav.status_code == 201
    link_data = link_fav.json()

    # Lookup sources
    lookup = client.get(
        f"{URL}/lookup",
        params={"keys": f"chunk:{chunk_id},qa:{turn_id},chunk:99999"},
    )
    assert lookup.status_code == 200
    assert set(lookup.json()["items"]) == {f"chunk:{chunk_id}", f"qa:{turn_id}"}
    assert client.get(f"{URL}/lookup").json() == {"items": {}}

    # Listing & searching
    root_view = client.get(URL).json()
    assert len(root_view["folders"]) == 1
    assert {item["id"] for item in root_view["items"]} == {
        qa_data["id"],
        link_data["id"],
    }

    folder_view = client.get(URL, params={"folder_id": folder["id"]}).json()
    assert [item["id"] for item in folder_view["items"]] == [chunk_data["id"]]

    all_view = client.get(URL, params={"scope": "all", "kind": "chunk"}).json()
    assert all_view["folders"] == []
    assert [item["id"] for item in all_view["items"]] == [chunk_data["id"]]

    search_view = client.get(URL, params={"q": "适当性"}).json()
    assert {item["id"] for item in search_view["items"]} == {
        chunk_data["id"],
        qa_data["id"],
    }

    folder_search = client.get(URL, params={"q": "常用"}).json()
    assert [f["id"] for f in folder_search["folders"]] == [folder["id"]]

    # Update favorite (rename, clear note, update url, move to folder)
    updated = client.patch(
        f"{URL}/{link_data['id']}",
        json={
            "title": "央行公开数据",
            "note": "",
            "url": "https://www.pbc.gov.cn/tiaofasi/",
            "folder_id": folder["id"],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "央行公开数据"
    assert updated.json()["note"] is None
    assert updated.json()["folder_id"] == folder["id"]
    assert updated.json()["payload"]["url"] == "https://www.pbc.gov.cn/tiaofasi/"

    # Delete favorite
    assert client.delete(f"{URL}/{link_data['id']}").status_code == 204
    assert client.delete(f"{URL}/{link_data['id']}").status_code == 404


def test_batch_move_and_batch_delete(search, owner):
    client = search.client
    target = client.post(f"{URL}/folders", json={"name": "目标夹"}).json()
    source = client.post(f"{URL}/folders", json={"name": "源文件夹"}).json()
    item_a = client.post(
        URL, json={"kind": "link", "title": "A", "url": "https://a.example.com"}
    ).json()
    item_b = client.post(
        URL, json={"kind": "link", "title": "B", "url": "https://b.example.com"}
    ).json()

    # Batch move source folder and item_a into target
    res = client.post(
        f"{URL}/batch-move",
        json={
            "folder_ids": [source["id"]],
            "item_ids": [item_a["id"]],
            "target_folder_id": target["id"],
        },
    )
    assert res.status_code == 204

    view = client.get(URL, params={"folder_id": target["id"]}).json()
    assert [f["id"] for f in view["folders"]] == [source["id"]]
    assert [i["id"] for i in view["items"]] == [item_a["id"]]

    # Batch move cycle rejected
    cycle = client.post(
        f"{URL}/batch-move",
        json={"folder_ids": [target["id"]], "target_folder_id": source["id"]},
    )
    assert (cycle.status_code, cycle.json()["code"]) == (400, "FAVORITE_FOLDER_CYCLE")

    # Batch delete target (cascades source & item_a) + item_b
    deleted = client.post(
        f"{URL}/batch-delete",
        json={"folder_ids": [target["id"]], "item_ids": [item_b["id"]]},
    )
    assert deleted.status_code == 204
    tree = client.get(f"{URL}/tree").json()
    assert tree == {"folders": [], "root_item_count": 0, "total_item_count": 0}


def test_favorites_owner_isolation_and_validation(search, owner):
    client = search.client
    folder = client.post(f"{URL}/folders", json={"name": "我的私密夹"}).json()
    fav = client.post(
        URL,
        json={
            "kind": "link",
            "folder_id": folder["id"],
            "title": "内部书签",
            "url": "https://example.com",
        },
    ).json()
    session_id, turn_id = _seed_chat_turn(search, owner.id)

    # Validation checks
    assert (
        client.post(
            URL,
            json={"kind": "link", "title": "bad", "url": "javascript:alert(1)"},
        ).status_code
        == 422
    )
    assert client.post(URL, json={"kind": "chunk"}).status_code == 422
    assert (
        client.post(URL, json={"kind": "qa", "session_id": session_id}).status_code
        == 422
    )
    assert client.patch(f"{URL}/folders/{folder['id']}", json={}).status_code == 422
    assert client.patch(f"{URL}/{fav['id']}", json={}).status_code == 422
    assert client.post(f"{URL}/batch-delete", json={}).status_code == 422
    assert client.post(f"{URL}/batch-move", json={}).status_code == 422

    # Switch to another user
    with search.db() as session:
        other_user = make_user("user")
        other_user.username = "user_02"
        other_user.email = "user02@example.com"
        session.add(other_user)
        session.commit()
    app.dependency_overrides[require_current_user] = lambda: other_user

    assert client.get(f"{URL}/tree").json()["folders"] == []
    assert client.get(URL, params={"folder_id": folder["id"]}).status_code == 404
    assert (
        client.patch(f"{URL}/folders/{folder['id']}", json={"name": "窃取"}).status_code
        == 404
    )
    assert client.delete(f"{URL}/folders/{folder['id']}").status_code == 404
    assert client.patch(f"{URL}/{fav['id']}", json={"title": "窃取"}).status_code == 404
    assert client.delete(f"{URL}/{fav['id']}").status_code == 404
    # Cannot favorite another user's chat session turn
    assert (
        client.post(
            URL, json={"kind": "qa", "session_id": session_id, "turn_id": turn_id}
        ).status_code
        == 404
    )
