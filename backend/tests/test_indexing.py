import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from pymilvus import MilvusException

from app.core.config import EmbeddingSettings, VectorStoreSettings
from app.core.exceptions import AppException
from app.rag.indexing import (
    MAX_INDEX_TEXT_CHARS,
    DashScopeEmbedding,
    MilvusVectorStore,
    VectorRecord,
    collection_name,
    index_entries,
    index_text,
)

KEY = "sk-test-key-not-real"
DIM = 64


def chunk(**values):
    fields = {"chapter": "", "section": "", "article": "", "heading": "", "content": ""}
    return SimpleNamespace(**(fields | values))


def test_index_text_prefixes_title_and_location():
    text = index_text(
        "证券公司风险处置条例",
        chunk(chapter="第一章 总则", article="第一条", heading="总则", content="正文"),
    )
    assert text == "证券公司风险处置条例 第一章 总则 第一条\n正文"
    assert index_text("办法", chunk(heading="总则 / 细则", content="x")) == (
        "办法 总则 / 细则\nx"
    )
    assert index_text("", chunk(content="只有正文")) == "只有正文"
    assert len(index_text("t", chunk(content="长" * 9000))) == MAX_INDEX_TEXT_CHARS


TITLE = "关于开展消费者权益保护自查的通知"
ITEM_ONE = chunk(article="一、", content="一、各机构应当于本月底前完成自查。")
ITEM_TWO = chunk(article="二、", content="二、发现问题的，应当立即整改。")


def texts(title, chunks):
    return [text for _, text in index_entries(title, chunks)]


def test_clauses_carry_the_title_found_in_the_document():
    assert texts("通知_扫描图片", [chunk(content=TITLE), ITEM_ONE, ITEM_TWO]) == [
        f"通知_扫描图片 {TITLE} 一、\n一、各机构应当于本月底前完成自查。",
        f"通知_扫描图片 {TITLE} 二、\n二、发现问题的，应当立即整改。",
    ]


def test_title_only_chunk_is_left_out_of_the_index():
    title_chunk = chunk(content=TITLE)
    kept = [c for c, _ in index_entries("x", [title_chunk, ITEM_ONE])]
    assert kept == [ITEM_ONE]


def test_overlapping_titles_are_not_repeated():
    assert texts("消费者权益保护", [chunk(content=TITLE), ITEM_ONE])[0].startswith(
        f"{TITLE} 一、\n"
    )
    assert texts(f"{TITLE}（2026年修订）", [chunk(content=TITLE), ITEM_ONE])[
        0
    ].startswith(f"{TITLE}（2026年修订） 一、\n")


def test_preamble_with_body_stays_and_does_not_repeat_its_title():
    preamble = chunk(content=f"{TITLE}\n各分支机构：\n为保护消费者权益，通知如下。")
    assert texts("通知", [preamble, ITEM_ONE]) == [
        f"通知\n{preamble.content}",
        f"{TITLE} 一、\n{ITEM_ONE.content}",
    ]


def test_preamble_with_document_number_stays_searchable():
    preamble = chunk(content=f"银发〔2026〕12号\n{TITLE}")
    result = index_entries("办法", [preamble, ITEM_ONE])
    assert [c for c, _ in result] == [preamble, ITEM_ONE]
    assert result[1][1].startswith(f"办法 {TITLE} 一、\n")


def test_multi_line_titles_are_joined():
    preamble = chunk(content="中国人民银行关于规范\n支付业务管理的办法")
    assert texts("办法", [preamble, ITEM_ONE]) == [
        f"中国人民银行关于规范支付业务管理的办法 一、\n{ITEM_ONE.content}"
    ]


@pytest.mark.parametrize(
    "chunks",
    [
        # 按长度切片：标题路径已在 heading 中
        [
            chunk(heading="指南", content="标题样式的行"),
            chunk(heading="指南 / 流程", content="x"),
        ],
        # 没有识别到条文
        [chunk(content="标题样式的行"), chunk(content="正文")],
        # 只有一个切片时不能把它排除
        [chunk(content=TITLE)],
        # 第一行就是句子，不是标题
        [chunk(content="为保护消费者权益，通知如下。"), ITEM_ONE],
        # 第一个切片已属于章节
        [chunk(chapter="第一章 总则", content="总则的说明"), ITEM_ONE],
    ],
)
def test_documents_without_a_detectable_title_keep_every_chunk(chunks):
    assert texts("办法", chunks) == [index_text("办法", c) for c in chunks]


def test_collection_names_separate_models_and_dimensions():
    assert collection_name("finoracle_chunks", "text-embedding-v4", 1024) == (
        "finoracle_chunks_text_embedding_v4_1024"
    )
    assert collection_name("p", "Qwen3.7/Text Embedding", 512) == (
        "p_qwen3_7_text_embedding_512"
    )


def settings(**values) -> EmbeddingSettings:
    base = {"dashscope_api_key": KEY, "embedding_dimension": DIM}
    return EmbeddingSettings(_env_file=None, **(base | values))


def embed(handler, texts, **values):
    async def main():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = DashScopeEmbedding(settings(**values), client=client)
        try:
            return await provider.embed_documents(texts)
        finally:
            await provider.aclose()

    return asyncio.run(main())


def test_dashscope_request_and_result_order():
    requests = []

    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        data = [
            {"index": index, "embedding": [float(index)] * DIM}
            for index in range(len(body["input"]))
        ]
        return httpx.Response(200, json={"data": list(reversed(data))})

    vectors = embed(handler, ["第一条", "第二条"])

    assert vectors == [[0.0] * DIM, [1.0] * DIM]
    request = requests[0]
    assert str(request.url) == (
        "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
    )
    assert request.headers["Authorization"] == f"Bearer {KEY}"
    assert json.loads(request.content) == {
        "model": "text-embedding-v4",
        "input": ["第一条", "第二条"],
        "dimensions": DIM,
        "encoding_format": "float",
    }


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "EMBEDDING_AUTH_FAILED"),
        (403, "EMBEDDING_AUTH_FAILED"),
        (429, "EMBEDDING_UNAVAILABLE"),
        (503, "EMBEDDING_UNAVAILABLE"),
        (400, "EMBEDDING_REJECTED"),
    ],
)
def test_dashscope_errors_map_to_retry_rules(status, code, caplog):
    def handler(request):
        return httpx.Response(status, json={"message": "回显的输入文本 secret"})

    with pytest.raises(AppException) as caught:
        embed(handler, ["内容"])
    assert caught.value.code == code
    # 只有服务暂时不可用时重试
    assert (caught.value.status_code >= 500) == (code == "EMBEDDING_UNAVAILABLE")
    assert KEY not in caplog.text and "secret" not in caplog.text


def test_dashscope_network_failures_and_bad_results_are_retryable():
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    def wrong_dimension(request):
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})

    def not_json(request):
        return httpx.Response(200, content=b"<html>")

    for handler in (timeout, wrong_dimension, not_json):
        with pytest.raises(AppException) as caught:
            embed(handler, ["内容"])
        assert caught.value.code == "EMBEDDING_UNAVAILABLE"


def test_dashscope_requires_a_key_and_respects_the_batch_limit():
    def unused(request):
        raise AssertionError("no request expected")

    with pytest.raises(AppException) as caught:
        embed(unused, ["内容"], dashscope_api_key="  ")
    assert "DASHSCOPE_API_KEY" in caught.value.message
    with pytest.raises(ValueError):
        embed(unused, ["x"] * 11, embedding_batch_size=10)
    assert embed(unused, []) == []


def test_dashscope_creates_and_closes_its_own_client(monkeypatch):
    provider = DashScopeEmbedding(settings())
    created = []
    original = httpx.AsyncClient

    def factory(**options):
        client = original(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, json={"data": [{"index": 0, "embedding": [0.0] * DIM}]}
                )
            ),
            **options,
        )
        created.append(client)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", factory)

    async def main():
        await provider.embed_documents(["a"])
        await provider.embed_documents(["b"])
        await provider.aclose()

    asyncio.run(main())
    assert len(created) == 1 and created[0].is_closed
    assert provider.model_id == f"text-embedding-v4:{DIM}"


class FakeMilvus:
    def __init__(self, exists=False):
        self.exists = exists
        self.calls = []
        self.fail = False

    def __getattr__(self, name):
        def record(*args, **kwargs):
            if self.fail:
                raise MilvusException(message="connection refused")
            self.calls.append((name, args, kwargs))
            if name == "has_collection":
                return self.exists
            if name == "create_collection":
                self.exists = True
            if name == "query":
                return [{"count(*)": 7}]
            if name == "prepare_index_params":
                return SimpleNamespace(
                    indexes=[], add_index=lambda **kw: self.calls.append(("index", kw))
                )
            return None

        return record


def store(client) -> MilvusVectorStore:
    return MilvusVectorStore(
        VectorStoreSettings(_env_file=None, milvus_collection_prefix="test"),
        model="text-embedding-v4",
        dimension=4,
        client_factory=lambda: client,
    )


def test_milvus_collection_is_created_once_with_dense_bm25_and_version_indexes():
    client = FakeMilvus()
    vectors = store(client)
    vectors.ensure_collection()
    vectors.ensure_collection()

    names = [name for name, *_ in client.calls]
    assert names.count("create_collection") == 1
    assert names.count("load_collection") == 2
    _, _, created = next(
        call for call in client.calls if call[0] == "create_collection"
    )
    assert created["collection_name"] == "test_text_embedding_v4_4"
    schema = created["schema"]
    fields = {field.name: field for field in schema.fields}
    assert fields["chunk_id"].is_primary
    assert json.loads(fields["text"].params["analyzer_params"]) == {"type": "chinese"}
    assert schema.functions[0].output_field_names == ["sparse"]
    indexes = [call[1] for call in client.calls if call[0] == "index"]
    assert {(kw["field_name"], kw.get("metric_type")) for kw in indexes} == {
        ("dense", "COSINE"),
        ("sparse", "BM25"),
        ("version_id", None),
    }


def test_milvus_writes_deletes_and_counts_by_version():
    client = FakeMilvus(exists=True)
    vectors = store(client)
    document, version = uuid4(), uuid4()
    vectors.upsert([VectorRecord(5, document, version, "文本", [0.1] * 4)])
    vectors.upsert([])
    vectors.delete_by_version(version)
    assert vectors.count_by_version(version) == 7

    upsert = next(call for call in client.calls if call[0] == "upsert")
    assert upsert[2]["data"] == [
        {
            "chunk_id": 5,
            "document_id": str(document),
            "version_id": str(version),
            "text": "文本",
            "dense": [0.1] * 4,
        }
    ]
    delete = next(call for call in client.calls if call[0] == "delete")
    assert delete[2]["filter"] == f'version_id == "{version}"'

    client.exists = False
    calls = len(client.calls)
    vectors.delete_by_version(version)
    assert vectors.count_by_version(version) == 0
    assert [name for name, *_ in client.calls[calls:]] == [
        "has_collection",
        "has_collection",
    ]


def test_milvus_failures_become_vector_store_unavailable():
    client = FakeMilvus(exists=True)
    client.fail = True
    with pytest.raises(AppException) as caught:
        store(client).delete_by_version(uuid4())
    assert (caught.value.code, caught.value.status_code) == (
        "VECTOR_STORE_UNAVAILABLE",
        503,
    )


def test_settings_validate_models_dimensions_and_urls(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    defaults = EmbeddingSettings(_env_file=None)
    assert (defaults.embedding_model, defaults.embedding_dimension) == (
        "text-embedding-v4",
        1024,
    )
    assert defaults.dashscope_api_key is None
    for values in (
        {"embedding_dimension": 1000},
        {"embedding_batch_size": 11},
        {"embedding_base_url": "dashscope.aliyuncs.com"},
        {"embedding_model": "bad model"},
    ):
        with pytest.raises(ValueError):
            EmbeddingSettings(_env_file=None, **values)
    with pytest.raises(ValueError):
        VectorStoreSettings(_env_file=None, milvus_collection_prefix="1bad")
    assert VectorStoreSettings(_env_file=None, milvus_token=" ").milvus_token is None


def test_importing_the_vector_store_does_not_load_dotenv_files():
    import os

    import app.rag.indexing.vector_store  # noqa: F401

    assert os.environ["PYTHON_DOTENV_DISABLED"] == "1"


def test_settings_accept_environment_strings(monkeypatch):
    monkeypatch.setenv("EMBEDDING_DIMENSION", "512")
    monkeypatch.setenv("EMBEDDING_BATCH_SIZE", "8")
    monkeypatch.setenv("MILVUS_URI", "http://milvus:19530")
    embedding = EmbeddingSettings(_env_file=None)
    assert (embedding.embedding_dimension, embedding.embedding_batch_size) == (512, 8)
    assert VectorStoreSettings(_env_file=None).milvus_uri == "http://milvus:19530"
    monkeypatch.setenv("EMBEDDING_DIMENSION", "1000")
    with pytest.raises(ValueError):
        EmbeddingSettings(_env_file=None)
