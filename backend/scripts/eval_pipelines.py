"""在同一语料和同一问题集上比较各检索方案。

  prepare  从 eval/work 下的语料抽取条文，生成问题集，并把部分文档栅格化为扫描件
  ingest   通过管理接口导入语料、发布，并建立各方案的索引（可重复运行，已完成的跳过）
  run      逐题检索，按页计分，输出指标与配对 bootstrap 置信区间

公平性约定：问题只由原文生成，生成时不接触任何检索方案；所有方案检索同一批版本，
都用“是否找到答案所在的页”计分；文字版与扫描版分开统计，因为两者偏向不同的方案。

用法（在 backend/ 下）：
  uv run python scripts/eval_pipelines.py prepare
  uv run python scripts/eval_pipelines.py ingest --pipelines pdf-block-visual page-image-visual
  uv run python scripts/eval_pipelines.py run --out ../docs/verification/pipeline-eval
"""

import argparse
import json
import math
import random
import re
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx
import pymupdf

BACKEND = Path(__file__).resolve().parents[1]
EVAL_DIR = BACKEND / "eval"
QUESTIONS = EVAL_DIR / "questions.jsonl"
MANUAL_QUESTIONS = EVAL_DIR / "manual_questions.jsonl"
WORK_DIR = EVAL_DIR / "work"
CORPUS_DIR = WORK_DIR / "corpus"
OFFICIAL_DIR = WORK_DIR / "official"
SCAN_DIR = WORK_DIR / "scans"
STATE = WORK_DIR / "state.json"
SEED = 20261110
DEFAULT_PIPELINES = ("structured-hybrid", "pdf-block-visual", "page-image-visual")

_NUMERAL = "零〇一二三四五六七八九十百千两"
# 行首的“第二条的规定”是引用，不是新条文
_ARTICLE = re.compile(rf"^\s*(第[{_NUMERAL}]+条)(?![的规所至、和及或中第])")
_WHITESPACE = re.compile(r"\s+")
_ITEM = re.compile(rf"^\s*((?:[{_NUMERAL}]+、)|(?:（[{_NUMERAL}]+）))")
OFFICIAL_RELEASE = {
    "P020240802341036118987": "official-securities",
    "非银行支付机构监督管理条例(国务院令 第768号)": "official-payment",
}
OFFICIAL_TITLES = {
    "official-securities": "证券公司风险处置条例",
    "official-payment": "非银行支付机构监督管理条例",
    "official-shfe": "上海期货交易所实际控制关系账户管理办法",
    "official-antimonopoly": "中华人民共和国反垄断法",
    "official-nfra": "国家金融监督管理总局规定汇编",
    "scan-aml": "中国人民银行关于加强金融从业人员反洗钱履职管理及相关反洗钱内控建设的通知",
}
# 抓取时混入的网站导航，不作为出题材料
_NAVIGATION = ("返回主站", "系统简介", "综合查询", "法规体系", "最新法律", "首页")


def compact(text: str) -> str:
    return _WHITESPACE.sub("", text)


@dataclass(frozen=True)
class Passage:
    doc: str
    title: str
    label: str
    text: str
    pages: tuple[int, ...]


def read_pages(path: Path) -> list[str]:
    with pymupdf.open(path) as document:
        return [page.get_text() for page in document]


# 重排版 PDF 的标题可能折行，到这些元数据行为止
_META_PREFIXES = ("文号：", "发文单位：", "发布日期：", "来源：", "文档ID：")


def document_title(pages: list[str]) -> str:
    parts: list[str] = []
    for line in pages[0].splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(_META_PREFIXES) or _ARTICLE.match(line) or _ITEM.match(line):
            break
        parts.append(line)
        if len(parts) == 3:
            break
    return "".join(parts)[:200] or "未命名文档"


def _split_labeled(
    doc: str, title: str, lines: list[tuple[int, str]], pattern: re.Pattern[str]
) -> list[Passage]:
    passages: list[Passage] = []
    current: list[tuple[int, str]] = []
    label = ""

    def flush() -> None:
        body = "".join(compact(line) for _, line in current)
        if label and len(body) >= 40:
            numbers = tuple(sorted({number for number, _ in current}))
            passages.append(Passage(doc, title, label, body, numbers))

    for number, line in lines:
        match = pattern.match(line)
        if match:
            flush()
            label, current = match.group(1), []
        if label:
            current.append((number, line))
    flush()
    return passages


def extract_passages(doc: str, pages: list[str]) -> list[Passage]:
    """优先按“第X条”切分，其次按“一、”“（一）”，都没有时取较长的段落。"""
    title = document_title(pages)
    lines = [
        (number, line)
        for number, text in enumerate(pages, start=1)
        for line in text.splitlines()
        if line.strip()
    ]
    for pattern in (_ARTICLE, _ITEM):
        passages = _split_labeled(doc, title, lines, pattern)
        if len(passages) >= 2:
            return passages
    passages = []
    for number, text in enumerate(pages, start=1):
        for line in text.splitlines():
            body = compact(line)
            if len(body) >= 45 and not any(word in body for word in _NAVIGATION):
                passages.append(Passage(doc, title, "", body, (number,)))
    return passages


def official_passages() -> dict[str, list[Passage]]:
    """官方版式文档的条文与页码来自人工核对过的 release 数据。"""
    chunks = json.loads((WORK_DIR / "release_chunks.json").read_text(encoding="utf-8"))
    result: dict[str, list[Passage]] = {}
    for chunk in chunks:
        doc = OFFICIAL_RELEASE.get(chunk["doc_id"])
        body = compact(chunk.get("content") or "")
        if doc is None or len(body) < 40:
            continue
        first, last = int(chunk["page_start"]), int(chunk["page_end"])
        result.setdefault(doc, []).append(
            Passage(
                doc,
                OFFICIAL_TITLES[doc],
                compact(chunk.get("article") or ""),
                body,
                tuple(range(first, last + 1)),
            )
        )
    return result


def sample_passages(by_doc: dict[str, list[Passage]], count: int) -> list[Passage]:
    """各文档轮流抽取，避免长文档占满问题集。"""
    rng = random.Random(SEED)
    pools = {doc: rng.sample(items, len(items)) for doc, items in by_doc.items()}
    chosen: list[Passage] = []
    order = sorted(pools)
    while len(chosen) < count and any(pools.values()):
        for doc in order:
            if pools[doc] and len(chosen) < count:
                chosen.append(pools[doc].pop())
    return chosen


def copies_source(question: str, source: str, length: int = 8) -> bool:
    text = compact(question)
    return any(
        text[start : start + length] in source
        for start in range(len(text) - length + 1)
    )


PROMPTS = {
    "paraphrase": (
        "下面是一段金融监管规定。请站在业务人员的角度提一个问题，答案必须能在这段文字中找到。"
        "要求：问题要具体，写明涉及的机构类型、业务或事项，别人只看问题就能判断问的是什么，"
        "不能是放到任何规定都成立的笼统问题；用自己的话表达，不要照抄原文中连续 8 个字以上的"
        "内容，不要提到文件名称和条号。"
        '只输出 JSON：{"question": "..."}\n\n'
    ),
    "keyword": (
        "下面是一段金融监管规定。请从原文中挑选 2 到 4 个最有区分度的词，用空格连接，"
        '模拟用户的关键词搜索。只输出 JSON：{"question": "..."}\n\n'
    ),
}


def ask_llm(client: httpx.Client, key: str, prompt: str) -> str:
    for attempt in range(4):
        try:
            response = client.post(
                "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": "qwen-plus",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "response_format": {"type": "json_object"},
                },
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return str(json.loads(content)["question"]).strip()
        except (httpx.HTTPError, KeyError, ValueError):
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("问题生成失败")


def is_article(label: str) -> bool:
    return bool(_ARTICLE.match(label))


def make_question(
    client: httpx.Client, key: str, passage: Passage, kind: str
) -> str | None:
    if kind == "article":
        return f"《{passage.title}》{passage.label}规定了什么"
    prompt = PROMPTS[kind] + passage.text[:1500]
    for _ in range(3):
        question = ask_llm(client, key, prompt)
        if kind == "keyword" or not copies_source(question, passage.text):
            return question
    return None


def rasterize(source: Path, target: Path, dpi: int = 150) -> None:
    """把 PDF 转成只含图片的 PDF，模拟扫描件。"""
    with pymupdf.open(source) as document, pymupdf.open() as output:
        for page in document:
            image = page.get_pixmap(dpi=dpi, alpha=False)
            sheet = output.new_page(width=page.rect.width, height=page.rect.height)
            sheet.insert_image(sheet.rect, stream=image.tobytes("jpeg"))
        output.save(target, deflate=True)


def read_env() -> dict[str, str]:
    values = {}
    for line in (BACKEND / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            name, value = line.split("=", 1)
            values[name.strip()] = value.strip().strip("\"'")
    return values


def prepare(args: argparse.Namespace) -> None:
    files = sorted(CORPUS_DIR.glob("*.pdf"))
    if not files:
        sys.exit(f"{CORPUS_DIR} 中没有 PDF")
    by_doc = {}
    for path in files:
        passages = extract_passages(path.stem, read_pages(path))
        if passages:
            by_doc[path.stem] = passages
    official = official_passages()
    chosen = sample_passages(by_doc, args.questions) + sample_passages(
        official, args.official_questions
    )
    kinds = []
    for index, passage in enumerate(chosen):
        kind = ("paraphrase", "keyword", "article")[index % 3]
        kinds.append(
            "paraphrase"
            if kind == "article" and not is_article(passage.label)
            else kind
        )

    key = read_env()["DASHSCOPE_API_KEY"]
    client = httpx.Client(timeout=60, trust_env=False)
    with ThreadPoolExecutor(6) as pool:
        questions = list(
            pool.map(lambda pair: make_question(client, key, *pair), zip(chosen, kinds))
        )
    rng = random.Random(SEED)
    # 只从确实出了题的文档中选扫描件
    docs = sorted(
        {p.doc for p, q in zip(chosen, questions) if q and p.doc.startswith("nfra-")}
    )
    scan_docs = set(rng.sample(docs, min(args.scan_docs, len(docs))))
    SCAN_DIR.mkdir(parents=True, exist_ok=True)
    for doc in scan_docs:
        rasterize(CORPUS_DIR / f"{doc}.pdf", SCAN_DIR / f"{doc}.pdf")
    rows = []
    seen: set[tuple[str, str]] = set()
    for index, (passage, kind, question) in enumerate(
        zip(chosen, kinds, questions), start=1
    ):
        if not question or (passage.doc, question) in seen:
            continue
        seen.add((passage.doc, question))
        source = "official" if passage.doc.startswith("official-") else "nfra"
        for corpus_name in ("text", "scan"):
            if corpus_name == "scan" and passage.doc not in scan_docs:
                continue
            rows.append(
                {
                    "id": f"{corpus_name}-{index:03d}",
                    "corpus": corpus_name,
                    "source": source,
                    "type": kind,
                    "query": question,
                    "doc": passage.doc,
                    "pages": list(passage.pages),
                    "anchor": passage.text[:60],
                    "label": passage.label,
                }
            )
    if MANUAL_QUESTIONS.exists():
        rows += [
            json.loads(line)
            for line in MANUAL_QUESTIONS.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    with QUESTIONS.open("w", encoding="utf-8") as output:
        for row in rows:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"{len(rows)} 条问题，扫描件 {len(scan_docs)} 份，写入 {QUESTIONS}")


def load_state() -> dict:
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


class _AdminLogin(httpx.Auth):
    """访问令牌约 30 分钟过期，长时间运行时遇到 401 重新登录一次再重试。"""

    def __init__(self, base: str) -> None:
        self.base = base
        self.token: str | None = None

    def _login(self) -> None:
        env = read_env()
        response = httpx.post(
            f"{self.base}/auth/login",
            json={
                "identifier": env["ADMIN_USERNAME"],
                "password": env["ADMIN_PASSWORD"],
            },
            timeout=60,
            trust_env=False,
        )
        response.raise_for_status()
        self.token = response.json()["access_token"]

    def auth_flow(self, request):
        if self.token is None:
            self._login()
        request.headers["Authorization"] = f"Bearer {self.token}"
        response = yield request
        if response.status_code == 401:
            self._login()
            request.headers["Authorization"] = f"Bearer {self.token}"
            yield request


def api_client(base: str) -> httpx.Client:
    return httpx.Client(
        base_url=base, timeout=180, trust_env=False, auth=_AdminLogin(base)
    )


def wait_for(check, timeout: float, what: str):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = check()
        if value is not None:
            return value
        time.sleep(2)
    raise TimeoutError(what)


def job_seconds(job: dict) -> float:
    if not job.get("started_at") or not job.get("finished_at"):
        return 0.0
    started = datetime.fromisoformat(job["started_at"].replace("Z", "+00:00"))
    finished = datetime.fromisoformat(job["finished_at"].replace("Z", "+00:00"))
    return max(0.0, (finished - started).total_seconds())


def ingest_one(client: httpx.Client, path: Path, title: str) -> dict:
    with path.open("rb") as file:
        response = client.post(
            "/admin/imports",
            data={"title": title},
            files={"file": (path.name, file, "application/pdf")},
        )
    response.raise_for_status()
    job = response.json()
    version_id = job["version_id"]

    def ready():
        info = client.get(f"/admin/versions/{version_id}").json()
        return info if info["status"] in ("ready", "failed", "parsed") else None

    info = wait_for(ready, 900, f"{path.name} 入库超时")
    if info["status"] == "parsed":
        approved = client.post(f"/admin/versions/{version_id}/approve")
        approved.raise_for_status()
        info = wait_for(ready, 900, f"{path.name} 向量化超时")
    entry = {
        "document_id": job["document_id"],
        "version_id": version_id,
        "structured": info["status"],
    }
    if info["status"] == "ready":
        client.post(f"/admin/versions/{version_id}/publish").raise_for_status()
    return entry


def build_pipeline(client: httpx.Client, entry: dict, name: str) -> dict:
    version_id = entry["version_id"]
    response = client.post(f"/admin/versions/{version_id}/pipelines/{name}")
    if response.status_code != 202:
        return {"status": "rejected", "error": response.json().get("code")}
    job_id = response.json()["id"]

    def finished():
        response = client.get(f"/admin/imports/{job_id}")
        response.raise_for_status()
        job = response.json()
        return job if job["status"] in ("succeeded", "failed") else None

    job = wait_for(finished, 1800, f"{name} 构建超时")
    return {
        "status": "ready" if job["status"] == "succeeded" else "failed",
        "error": job["error_code"],
        "seconds": job_seconds(job),
    }


def corpus_sources() -> list[tuple[str, Path, str]]:
    """(语料名, 文件, 登记标题)；扫描件沿用原文标题，两类文档的登记信息相同。"""
    sources = []
    for path in sorted(CORPUS_DIR.glob("*.pdf")):
        sources.append(("text", path, document_title(read_pages(path))))
    for path in sorted(OFFICIAL_DIR.glob("official-*.pdf")):
        sources.append(("text", path, OFFICIAL_TITLES[path.stem]))
    for path in sorted(SCAN_DIR.glob("*.pdf")):
        title = document_title(read_pages(CORPUS_DIR / path.name))
        sources.append(("scan", path, title))
    sources.append(("scan", OFFICIAL_DIR / "scan-aml.pdf", OFFICIAL_TITLES["scan-aml"]))
    return sources


def ingest(args: argparse.Namespace) -> None:
    state = load_state()
    client = api_client(args.api)
    documents = state.setdefault("documents", {})
    for corpus_name, path, title in corpus_sources():
        key = f"{corpus_name}:{path.stem}"
        if key not in documents:
            documents[key] = ingest_one(client, path, title[:250])
            save_state(state)
            print("ingested", key, documents[key]["structured"], flush=True)
        entry = documents[key]
        if entry["structured"] != "ready":
            continue
        for name in args.pipelines:
            done = entry.setdefault("pipelines", {})
            if done.get(name, {}).get("status") == "ready":
                continue
            done[name] = build_pipeline(client, entry, name)
            save_state(state)
            print(" ", key, name, done[name], flush=True)


def ndcg_at(pages: list[int], relevant: set[int], k: int) -> float:
    gain = sum(
        1 / math.log2(rank + 1)
        for rank, page in enumerate(pages[:k], start=1)
        if page in relevant
    )
    ideal = sum(1 / math.log2(rank + 1) for rank in range(1, min(len(relevant), k) + 1))
    return gain / ideal if ideal else 0.0


def recall_at(pages: list[int], relevant: set[int], k: int) -> float:
    return len(set(pages[:k]) & relevant) / len(relevant) if relevant else 0.0


def mrr_at(pages: list[int], relevant: set[int], k: int) -> float:
    for rank, page in enumerate(pages[:k], start=1):
        if page in relevant:
            return 1 / rank
    return 0.0


def ranked_pages(results: list[dict], version_id: str) -> list[int]:
    """把结果转成按名次去重的页序列；其他文档的命中占位 0，同样占据名次。"""
    pages: list[int] = []
    seen: set[tuple[str, int]] = set()
    for hit in results:
        version = hit["version"]["id"]
        start = hit["page_start"] or 0
        for page in range(start, (hit["page_end"] or start) + 1):
            if (version, page) in seen:
                continue
            seen.add((version, page))
            pages.append(page if version == version_id else 0)
    return pages


def bootstrap_diff(a: list[float], b: list[float], rounds: int = 2000) -> list[float]:
    """配对 bootstrap：a - b 均值差的 95% 置信区间。"""
    rng = random.Random(SEED)
    size = len(a)
    means = sorted(
        sum(a[i] - b[i] for i in (rng.randrange(size) for _ in range(size))) / size
        for _ in range(rounds)
    )
    return [means[int(rounds * 0.025)], means[int(rounds * 0.975)]]


def score(
    client: httpx.Client,
    questions: list[dict],
    state: dict,
    pipeline: str,
) -> list[dict]:
    documents = state["documents"]
    rows = []
    groups = {"text": [], "scan": []}
    for key, entry in documents.items():
        groups[key.split(":", 1)[0]].append(entry)
    for question in questions:
        entry = documents.get(f"{question['corpus']}:{question['doc']}")
        scope = [item["document_id"] for item in groups[question["corpus"]]]
        started = time.perf_counter()
        response = client.post(
            "/search",
            json={
                "query": question["query"],
                "top_k": 10,
                "pipeline": pipeline,
                "document_ids": scope,
            },
        )
        elapsed = (time.perf_counter() - started) * 1000
        response.raise_for_status()
        results = response.json()["results"]
        pages = ranked_pages(results, entry["version_id"])
        relevant = set(question["pages"])
        top = results[0] if results else {}
        rows.append(
            {
                "id": question["id"],
                "corpus": question["corpus"],
                "source": question.get("source", "nfra"),
                "type": question["type"],
                "ms": elapsed,
                "recall5": recall_at(pages, relevant, 5),
                "hit1": float(bool(pages[:1]) and pages[0] in relevant),
                "mrr10": mrr_at(pages, relevant, 10),
                "ndcg10": ndcg_at(pages, relevant, 10),
                "has_text": bool(top.get("content")),
                "has_article": bool(top.get("article")),
                "has_bbox": bool(top.get("bbox")),
                "has_image": bool(top.get("has_image")),
                "indexed": entry.get("pipelines", {}).get(pipeline, {}).get("status")
                == "ready"
                or pipeline == "structured-hybrid",
            }
        )
    return rows


METRICS = ("hit1", "recall5", "mrr10", "ndcg10")


def summarize(rows: list[dict]) -> dict:
    if not rows:
        return {}
    summary = {name: statistics.fmean(row[name] for row in rows) for name in METRICS}
    latencies = sorted(row["ms"] for row in rows)
    summary["n"] = len(rows)
    summary["p50_ms"] = latencies[len(latencies) // 2]
    summary["p95_ms"] = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]
    return summary


def table(title: str, sections: dict[str, dict]) -> list[str]:
    lines = [
        f"### {title}",
        "",
        "| 方案 | 题数 | Hit@1 | Recall@5 | MRR@10 | nDCG@10 | P50 ms | P95 ms |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, item in sections.items():
        if item:
            lines.append(
                f"| {name} | {item['n']} | {item['hit1']:.3f} | {item['recall5']:.3f} "
                f"| {item['mrr10']:.3f} | {item['ndcg10']:.3f} "
                f"| {item['p50_ms']:.0f} | {item['p95_ms']:.0f} |"
            )
    return lines + [""]


def run(args: argparse.Namespace) -> None:
    state = load_state()
    client = api_client(args.api)
    questions = [
        json.loads(line)
        for line in QUESTIONS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    known = {question for question in (q["id"] for q in questions)}
    assert known
    all_rows = {}
    for name in args.pipelines:
        started = time.time()
        all_rows[name] = score(client, questions, state, name)
        print(name, f"{time.time() - started:.0f}s", flush=True)

    report: dict = {"questions": len(questions), "pipelines": list(args.pipelines)}
    lines = ["# 检索方案评测结果", ""]
    slices: dict[str, dict] = {}
    for corpus_name, title in (("text", "文字版 PDF"), ("scan", "扫描件")):
        section = {
            name: summarize([r for r in rows if r["corpus"] == corpus_name])
            for name, rows in all_rows.items()
        }
        slices[corpus_name] = section
        lines += table(title, section)
        for kind in ("paraphrase", "keyword", "article"):
            part = {
                name: summarize(
                    [
                        r
                        for r in rows
                        if r["corpus"] == corpus_name and r["type"] == kind
                    ]
                )
                for name, rows in all_rows.items()
            }
            slices[f"{corpus_name}/{kind}"] = part
            lines += table(f"{title} · {kind}", part)
    for label, corpus_name, source in (
        ("官方版式 PDF（文字版）", "text", "official"),
        ("重排版法规（文字版）", "text", "nfra"),
        ("真实扫描件", "scan", "aml"),
        ("模拟扫描件", "scan", "nfra"),
    ):
        part = {
            name: summarize(
                [
                    r
                    for r in rows
                    if r["corpus"] == corpus_name and r["source"] == source
                ]
            )
            for name, rows in all_rows.items()
        }
        slices[f"{corpus_name}/{source}"] = part
        lines += table(label, part)
    report["slices"] = slices

    base = args.pipelines[0]
    comparisons = {}
    lines += [
        "### 与基线的配对差异（95% 置信区间）",
        "",
        f"基线：{base}。区间不跨 0 才算有显著差异。",
        "",
        "| 语料 | 方案 | Recall@5 差 | MRR@10 差 |",
        "| --- | --- | --- | --- |",
    ]
    for corpus_name in ("text", "scan"):
        reference = [r for r in all_rows[base] if r["corpus"] == corpus_name]
        for name in args.pipelines[1:]:
            rows = [r for r in all_rows[name] if r["corpus"] == corpus_name]
            if not rows:
                continue
            item = {}
            for metric in ("recall5", "mrr10"):
                ours = [r[metric] for r in rows]
                theirs = [r[metric] for r in reference]
                item[metric] = {
                    "diff": statistics.fmean(ours) - statistics.fmean(theirs),
                    "ci95": bootstrap_diff(ours, theirs),
                }
            comparisons[f"{corpus_name}/{name}"] = item
            lines.append(
                "| {} | {} | {:+.3f} [{:+.3f}, {:+.3f}] | {:+.3f} [{:+.3f}, {:+.3f}] |".format(
                    corpus_name,
                    name,
                    item["recall5"]["diff"],
                    *item["recall5"]["ci95"],
                    item["mrr10"]["diff"],
                    *item["mrr10"]["ci95"],
                )
            )
    report["comparisons"] = comparisons

    completeness = {}
    lines += [
        "",
        "### 首条结果的证据形态",
        "",
        "| 方案 | 有文字 | 有条号 | 有坐标 | 有页面图片 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name, rows in all_rows.items():
        item = {
            field: statistics.fmean(float(r[field]) for r in rows)
            for field in ("has_text", "has_article", "has_bbox", "has_image")
        }
        completeness[name] = item
        lines.append(
            f"| {name} | {item['has_text']:.0%} | {item['has_article']:.0%} "
            f"| {item['has_bbox']:.0%} | {item['has_image']:.0%} |"
        )
    report["evidence"] = completeness

    ingest_costs = {}
    for key, entry in state["documents"].items():
        for name, info in entry.get("pipelines", {}).items():
            bucket = ingest_costs.setdefault(
                f"{key.split(':', 1)[0]}/{name}",
                {"ready": 0, "failed": 0, "seconds": 0.0},
            )
            bucket["ready" if info["status"] == "ready" else "failed"] += 1
            bucket["seconds"] += info.get("seconds", 0.0)
    report["ingest"] = ingest_costs
    lines += [
        "",
        "### 附加方案的入库结果",
        "",
        "| 语料/方案 | 成功 | 失败或不支持 | 总耗时 s |",
        "| --- | --- | --- | --- |",
    ]
    for name, bucket in ingest_costs.items():
        lines.append(
            f"| {name} | {bucket['ready']} | {bucket['failed']} | {bucket['seconds']:.0f} |"
        )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(
        json.dumps({**report, "rows": all_rows}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    out.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    step = commands.add_parser("prepare")
    step.add_argument("--questions", type=int, default=130)
    step.add_argument("--official-questions", type=int, default=40)
    step.add_argument("--scan-docs", type=int, default=16)
    step.set_defaults(func=prepare)
    for name, func in (("ingest", ingest), ("run", run)):
        step = commands.add_parser(name)
        step.add_argument("--api", default="http://127.0.0.1:8001/api/v1")
        default = DEFAULT_PIPELINES if name == "run" else DEFAULT_PIPELINES[1:]
        step.add_argument("--pipelines", nargs="+", default=list(default))
        if name == "run":
            step.add_argument("--out", default="eval/work/report")
        step.set_defaults(func=func)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
