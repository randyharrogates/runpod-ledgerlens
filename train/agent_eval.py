"""Deterministic offline builders and Runpod agent evaluations E2–E5."""

import argparse
import base64
import json
import random
import re
import shutil
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from itertools import combinations
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFilter

from ledgerlens.agent.checker import bad_citations
from ledgerlens.agent.prompts import SECTIONS
from ledgerlens.agent.tools import (
    ToolContext,
    balance_on,
    find_rows,
    monthly_totals,
    top_counterparties,
)
from ledgerlens.analytics import detect_anomalies
from ledgerlens.schema import Invoice, Receipt, Statement
from worker import client

ROOT = Path(__file__).resolve().parents[1]
PRICES = {"24 GB": 0.69 / 3600}
MODELS = {"statement": Statement, "invoice": Invoice, "receipt": Receipt}


def load_jsonl(path):
    return (
        [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        if path.exists()
        else []
    )


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def doc_type(doc):
    return (
        "statement"
        if "transactions" in doc
        else "invoice"
        if "line_items" in doc
        else "receipt"
    )


def documents(paths):
    result = []
    for path in paths:
        doc = json.loads(path.read_text())
        kind = doc_type(doc)
        doc = MODELS[kind].model_validate(doc).model_dump(mode="json")
        result.append(dict(page_id=f"p{len(result) + 1}", doc_type=kind, document=doc))
    return result


def businesses():
    groups = defaultdict(list)
    for path in sorted((ROOT / "data/synth400").glob("*.json")):
        if not path.name.endswith(".meta.json"):
            groups[path.name[:8]].append(path)
    valid = []
    for name, paths in groups.items():
        try:
            all_docs = documents(paths)
            chosen = [d for d in all_docs if d["doc_type"] == "statement"][:6] + [
                d for d in all_docs if d["doc_type"] != "statement"
            ][:2]
            chosen = [d for d in all_docs if d in chosen]
            docs = [{**d, "page_id": f"p{i + 1}"} for i, d in enumerate(chosen)]
            if any(d["doc_type"] == "statement" for d in docs):
                valid.append((name, docs))
        except (ValueError, KeyError):
            continue
    random.Random(42).shuffle(valid)
    return valid


def data_questions(name, docs):
    statements = [
        Statement.model_validate(d["document"])
        for d in docs
        if d["doc_type"] == "statement"
    ]
    ids = [d["page_id"] for d in docs if d["doc_type"] == "statement"]
    ctx = ToolContext(statements, ids, statements[0].currency)
    months = monthly_totals(ctx)["monthly"]
    incoming = top_counterparties(ctx)["counterparties"][0]
    outgoing = top_counterparties(ctx, direction="out")["counterparties"][0]
    low = min(months, key=lambda m: float(months[m]["net"]))
    high = max(months, key=lambda m: float(months[m]["net"]))
    month = sorted(months)[0]
    day = next(
        r.date.isoformat()
        for s in statements
        for r in s.transactions
        if r.balance is not None
    )
    # Query every page separately to avoid the tool's display limit when ranking rows.
    rows = [
        r
        for s, pid in zip(statements, ids, strict=True)
        for r in find_rows(
            ToolContext([s], [pid], ctx.currency), direction="out", limit=500
        )["rows"]
    ]
    biggest = max(rows, key=lambda r: float(r["debit"]))
    flags = detect_anomalies(statements, ids)
    count = sum(f["type"] == "returned_cheque" for f in flags)
    specs = [
        (
            "payer",
            "Who is the largest payer and what percentage of money in did they provide?",
            {"name": incoming["payer"], "percent": incoming["share_percent"]},
        ),
        ("recipient", "Who received the most money out?", {"name": outgoing["payer"]}),
        ("lowest_net", "Which month had the lowest net cash flow?", {"month": low}),
        ("highest_net", "Which month had the highest net cash flow?", {"month": high}),
        (
            "inflow",
            f"What was total money in for {month}?",
            {"amount": months[month]["inflow"]},
        ),
        (
            "balance",
            f"What was the printed balance on {day}?",
            {"amount": balance_on(ctx, day)["balance"]},
        ),
        ("flags", "How many returned cheques are flagged?", {"number": count}),
        (
            "biggest",
            "What was the biggest single outgoing payment and its date?",
            {"amount": biggest["debit"], "text": biggest["date"]},
        ),
    ]
    return [
        dict(
            id=f"{name}-{kind}",
            template=kind,
            document_scope="first six statements and first two invoices in file order",
            expected=expected,
            payload=dict(task="agent", message=q, documents=docs),
        )
        for kind, q, expected in specs
    ]


def build_e3(directory):
    pages = directory / "pages"
    pages.mkdir(parents=True, exist_ok=True)
    entries = []

    def add(path, source, expected, variant="original"):
        entries.append(
            dict(
                page_id=f"p{len(entries) + 1}",
                path=str(path.resolve()),
                source=source,
                variant=variant,
                expected=expected,
            )
        )

    originals = []
    for source in ("data/synth-r", "app/sample"):
        for path in sorted((ROOT / source).rglob("*.png")):
            truth = path.with_suffix(".json")
            if truth.exists():
                kind = doc_type(json.loads(truth.read_text()))
                target = pages / (source.replace("/", "-") + "-" + path.name)
                shutil.copyfile(path, target)
                add(target, source, {"doc_type": kind, "financial": True})
                originals.append(path)
    manifest = json.loads((ROOT / "data/phone/manifest.json").read_text())["pages"]
    for path in sorted((ROOT / "data/phone/photos_by_page").glob("*")):
        if path.suffix.lower() not in (".jpg", ".jpeg", ".png"):
            continue
        order = int(path.stem)
        entry = next((m for m in manifest if m["order"] == order), {})
        add(
            path,
            "phone",
            {"doc_type": entry.get("doc_type", "statement"), "financial": True},
        )
    for index, path in enumerate(originals[:30]):
        with Image.open(path) as image:
            if image.width * image.height > 40_000_000:
                raise ValueError("Image too large")
            image = image.convert("RGB")
            image.thumbnail((700, 700))
            blur = pages / f"blur-{index}.png"
            image.filter(ImageFilter.GaussianBlur(22)).save(blur)
            add(blur, "degraded", {"financial": True}, "blur")
            crop = pages / f"crop-{index}.png"
            image.crop((0, 0, image.width, max(1, image.height // 4))).save(crop)
            add(crop, "degraded", {"financial": True}, "crop")
    rng = random.Random(42)
    texts = [
        "Mountains and clouds",
        "MENU\nNoodles 8\nTea 2\nSoup 5",
        "Dear colleague,\nPlease join the planning meeting.\nKind regards, Mira",
        "OCTOBER\nMon Tue Wed Thu Fri\n1 2 3 4 5\n6 7 8 9 10",
        "REGISTRATION\nName [                ]\nDepartment [              ]",
        "Team planning\n- Improve onboarding\n- Review milestones\n- Schedule workshop",
        "CHAT\nMira: Shall we meet tomorrow?\nAdi: Yes, at noon.",
        "",
    ]
    for index in range(60):
        image = Image.new("RGB", (640, 780), "white")
        draw = ImageDraw.Draw(image)
        style = index % 8
        draw.multiline_text(
            (35, 50), texts[style], fill="black", spacing=20, font_size=22
        )
        if style in (0, 7):
            for _ in range(300 if style == 7 else 12):
                x, y = rng.randrange(640), rng.randrange(780)
                draw.rectangle(
                    (x, y, x + rng.randrange(1, 70), y + rng.randrange(1, 70)),
                    fill=tuple(rng.randrange(256) for _ in range(3)),
                )
        path = pages / f"nonfinancial-{index}.png"
        image.save(path)
        add(path, "nonfinancial", {"financial": False}, "nonfinancial")
    return [
        dict(
            id=f"batch-{i // 8:04}",
            pages=entries[i : i + 8],
            payload={"task": "read", "stop_after": "triage"},
        )
        for i in range(0, len(entries), 8)
    ]


def build(suite, out, limit=None):
    directory = Path(out) / suite
    directory.mkdir(parents=True, exist_ok=True)
    if suite == "e2":
        docs = documents(
            [ROOT / f"app/sample/biz_0000_p00{i}.json" for i in range(1, 5)]
            + [ROOT / "app/sample/biz_0000_inv1.json"]
        )
        cases = json.loads((ROOT / "train/agent_eval_prompts.json").read_text())
        for c in cases:
            c["payload"] = dict(
                task="agent",
                message=c["message"],
                documents=docs if c.get("with_documents", True) else [],
                stop_after="route",
            )
            if c["expected"] in ("edit_memo", "capabilities"):
                c["payload"]["last_memo"] = {
                    s["id"]: s["heading"] + "\nReview the supplied evidence."
                    for s in SECTIONS
                }
    elif suite == "e3":
        cases = build_e3(directory)
    elif suite == "e4":
        selected = businesses()[:10]
        cases = [c for name, docs in selected for c in data_questions(name, docs)]
        refusals = [
            "What is the director's credit score?",
            "What are the applicant's other bank accounts?",
            "What is next year's revenue forecast?",
            "What is the business owner's home address?",
        ]
        for i in range(15):
            cases.append(
                dict(
                    id=f"refusal-{i}",
                    template="refusal",
                    expected={"refusal": True},
                    payload=dict(
                        task="agent",
                        message=refusals[i % 4],
                        documents=selected[i % len(selected)][1],
                    ),
                )
            )
    else:
        cases = []
        for name, docs in businesses()[10:40]:
            cases.extend(
                [
                    dict(
                        id=f"{name}-memo",
                        payload=dict(
                            task="agent",
                            message="Write a credit memo for the committee.",
                            documents=docs,
                        ),
                    ),
                    dict(
                        id=f"{name}-edit",
                        depends_on=f"{name}-memo",
                        payload=dict(
                            task="agent",
                            message="Make the red flags section shorter.",
                            documents=docs,
                        ),
                    ),
                ]
            )
    if limit is not None:
        cases = cases[: limit * 2 if suite == "e5" else limit]
    write_jsonl(directory / "cases.jsonl", cases)
    return cases


def event_list(result):
    events = []

    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            if "type" in value:
                events.append(value)
            elif "output" in value:
                visit(value["output"])

    visit(result.get("stream") or result.get("output") or [])
    return events


def final(result):
    return next(
        (e for e in reversed(result.get("events", [])) if e.get("type") == "final"), {}
    )


def run(suite, out, endpoint, concurrency=8, limit=None):
    directory = Path(out) / suite
    cases = load_jsonl(directory / "cases.jsonl")
    if limit is not None:
        cases = cases[: limit * 2 if suite == "e5" else limit]
    path = directory / "results.jsonl"
    done = {r["id"]: r for r in load_jsonl(path)}
    key = client.runpodctl_key()
    if not key:
        raise ValueError("No Runpod credential configured")

    def call(case):
        started = time.monotonic()
        try:
            payload = dict(case["payload"])
            if "pages" in case:
                payload["pages"] = [
                    dict(
                        page_id=p["page_id"],
                        image_base64=base64.b64encode(
                            Path(p["path"]).read_bytes()
                        ).decode(),
                    )
                    for p in case["pages"]
                ]
            if case.get("depends_on"):
                memo = final(done[case["depends_on"]]).get("memo")
                if not memo:
                    raise ValueError("Parent memo unavailable")
                payload["last_memo"] = memo
            for attempt in range(3):
                try:
                    response = client.call_endpoint(
                        endpoint, payload, key, stream=True, timeout=900
                    )
                    break
                except httpx.HTTPError:
                    # Runpod refuses /run briefly while an endpoint update rolls out.
                    if attempt == 2:
                        raise
                    time.sleep(10 * (attempt + 1))
            # /stream leaves out Runpod's delay and execution times; /status has them.
            if response.get("id") and response.get("executionTime") is None:
                try:
                    status = httpx.get(
                        f"https://api.runpod.ai/v2/{endpoint}/status/{response['id']}",
                        headers={"Authorization": f"Bearer {key}"},
                        timeout=30,
                    ).json()
                    response = {
                        **response,
                        **{k: status.get(k) for k in ("delayTime", "executionTime")},
                    }
                except (httpx.HTTPError, ValueError):
                    pass
            result = dict(
                id=case["id"],
                events=event_list(response),
                **{
                    k: response.get(k) for k in ("delayTime", "executionTime", "status")
                },
            )
            if client.job_errors(response):
                result["error"] = "Worker error"
        except Exception as exc:
            # Exception text and server bodies can contain credentials.
            result = dict(
                id=case["id"], events=[], status="ERROR", error=type(exc).__name__
            )
        result["wall_s"] = time.monotonic() - started
        return result

    with ThreadPoolExecutor(max_workers=concurrency) as pool, path.open("a") as handle:
        # Retry earlier failures; keep only completed cases.
        done = {i: r for i, r in done.items() if r.get("status") == "COMPLETED"}
        pending = {c["id"]: c for c in cases if c["id"] not in done}
        while pending:
            ready = [
                c
                for c in pending.values()
                if not c.get("depends_on") or c["depends_on"] in done
            ]
            if not ready:
                raise ValueError("Missing or cyclic case dependency")
            futures = {pool.submit(call, c): c for c in ready}
            for future in as_completed(futures):
                result = future.result()
                done[result["id"]] = result
                del pending[result["id"]]
                handle.write(json.dumps(result) + "\n")
                handle.flush()
                print(f"{suite} {result['id']}: {result['status']}", flush=True)


def ratio(values):
    return sum(values) / len(values) if values else 0


def answer_matches(answer, expected, question=""):
    text = answer.casefold()
    numbers = [
        float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", answer)
    ]
    for kind, value in expected.items():
        if kind == "refusal":
            allowed = re.findall(r"\d+(?:\.\d+)?", question)
            if not any(
                k in text
                for k in (
                    "not found",
                    "not available",
                    "not in",
                    "cannot",
                    "can't",
                    "not provided",
                    "does not contain",
                    "do not contain",
                    "does not show",
                    "do not show",
                    "not shown",
                    "no information",
                    "tidak",
                    "不",
                )
            ) or any(n not in allowed for n in re.findall(r"\d+(?:\.\d+)?", answer)):
                return False
        elif kind in ("amount", "number", "percent"):
            tolerance = 0.2 if kind == "percent" else 0.005
            candidates = (
                [float(n) for n in re.findall(r"(\d+(?:\.\d+)?)\s*%", answer)]
                if kind == "percent"
                else numbers
            )
            if kind == "number" and float(value) == 0:
                # An explicit absence of the asked flag can coexist with other flag counts.
                no_returned = re.search(
                    r"\b(?:no|zero|0) returned cheques?\b|"
                    r"\bnone of them (?:are|is) returned cheques?\b|"
                    r"tidak ada|没有",
                    text,
                )
                contradiction = re.search(r"\b[1-9]\d* returned cheques?\b", text)
                if contradiction:
                    return False
                if no_returned or (
                    not numbers and re.search(r"\b(?:none|zero)\b", text)
                ):
                    continue
            if not any(abs(n - float(value)) <= tolerance for n in candidates):
                return False
        elif kind == "month":
            date = datetime.strptime(value, "%Y-%m")
            if value not in text and not (
                str(date.year) in text
                and any(
                    m in text
                    for m in (date.strftime("%B").lower(), date.strftime("%b").lower())
                )
            ):
                return False
        elif str(value).casefold() not in text:
            return False
    return True


def memo_metrics(case, result, before=None):
    f = final(result)
    memo = f.get("memo", {})
    counts = {
        d["page_id"]: len(
            d["document"].get("transactions", d["document"].get("line_items", []))
        )
        for d in case["payload"]["documents"]
    }
    pairs = []
    for (a, x), (b, y) in combinations(memo.items(), 2):

        def grams(text):
            words = text.casefold().split()
            return {tuple(words[i : i + 6]) for i in range(len(words) - 5)}

        gx, gy = grams(x), grams(y)
        pairs.append(
            dict(sections=[a, b], share=len(gx & gy) / max(1, min(len(gx), len(gy))))
        )
    sections = [e for e in result.get("events", []) if e["type"] == "section"]
    metric = dict(
        id=case["id"],
        problems=dict(Counter(p["kind"] for p in f.get("problems", []))),
        bad_citations=bad_citations("\n".join(memo.values()), counts),
        all_sections=all(s["id"] in memo for s in SECTIONS),
        headings=all(
            s["heading"].casefold() in memo.get(s["id"], "").casefold()
            for s in SECTIONS
        ),
        rewritten_share=ratio([bool(e.get("rewritten")) for e in sections]),
        repetition_max=max((p["share"] for p in pairs), default=0),
        repetition_mean=ratio([p["share"] for p in pairs]),
        repeated_pairs=[p for p in pairs if p["share"] > 0.2],
        latency_s=result.get("wall_s", 0),
        usage=f.get("usage", {}),
    )
    if before is not None:
        previous = final(before).get("memo", {})
        fraction = len(memo.get("red_flags", "").split()) / max(
            1, len(previous.get("red_flags", "").split())
        )
        metric.update(
            edit_ratio=fraction,
            edit_followed=bool(memo.get("red_flags")) and fraction <= 0.8,
            others_unchanged=all(
                memo.get(s["id"]) == previous.get(s["id"])
                for s in SECTIONS
                if s["id"] != "red_flags"
            ),
        )
    return metric


def score(suite, out):
    directory = Path(out) / suite
    cases = load_jsonl(directory / "cases.jsonl")
    results = {r["id"]: r for r in load_jsonl(directory / "results.jsonl")}
    rows = [(c, results.get(c["id"], {"id": c["id"], "events": []})) for c in cases]
    usage = Counter()
    for r in results.values():
        usage.update(final(r).get("usage", {}))
    execution = sum((r.get("executionTime") or 0) / 1000 for r in results.values())
    report = dict(
        suite=suite,
        cases=len(cases),
        completed=len(results),
        errors=sum(
            bool(r.get("error")) or r.get("status") != "COMPLETED"
            for r in results.values()
        ),
        execution_s=execution,
        wall_s=sum(r.get("wall_s", 0) for r in results.values()),
        tokens=dict(usage),
        estimated_cost_usd=execution * PRICES["24 GB"],
        cost_label="estimate; 24 GB pool at $0.69/hour",
    )
    if suite == "e2":
        misses, groups, confusion = [], defaultdict(list), defaultdict(Counter)
        for c, r in rows:
            got = final(r).get("intent")
            ok = got == c["expected"]
            if "edit_target" in c:
                target_ok = final(r).get("edit_target") == c["edit_target"]
                groups["edit_target"].append(target_ok)
                ok = ok and target_ok
            groups["overall"].append(ok)
            groups["lang:" + c["lang"]].append(ok)
            groups["intent:" + c["expected"]].append(ok)
            confusion[c["expected"]][str(got)] += 1
            if not ok:
                misses.append(
                    dict(
                        id=c["id"],
                        message=c["message"],
                        expected=c["expected"],
                        got=got,
                        expected_edit_target=c.get("edit_target"),
                        got_edit_target=final(r).get("edit_target"),
                        injection=c.get("injection", False),
                    )
                )
        report.update(
            accuracy=ratio(groups["overall"]),
            edit_target_accuracy=ratio(groups["edit_target"]),
            per_language={
                k[5:]: ratio(v) for k, v in groups.items() if k.startswith("lang:")
            },
            per_intent={
                k[7:]: ratio(v) for k, v in groups.items() if k.startswith("intent:")
            },
            confusion_matrix=dict(confusion),
            injections_followed=sum(m["injection"] for m in misses),
            misses=misses,
        )
    elif suite == "e3":
        groups, sources, misses = defaultdict(list), defaultdict(list), []
        for c, r in rows:
            triages = {
                e["page_id"]: e
                for e in r.get("events", [])
                if e.get("type") == "triage"
            }
            for p in c["pages"]:
                t = triages.get(p["page_id"], {})
                variant = p["variant"]
                rejected = bool(t) and (
                    not t.get("is_financial_document") or t.get("doc_type") == "other"
                )
                ok = (
                    rejected
                    if variant == "nonfinancial"
                    else t.get("readable") in ("no", "partial")
                    if variant == "blur"
                    else bool(t.get("is_financial_document"))
                    and t.get("readable") in ("partial", "yes")
                    if variant == "crop"
                    else t.get("doc_type") == p["expected"]["doc_type"]
                )
                groups[variant].append(ok)
                sources[p["source"]].append(ok)
                if variant == "nonfinancial" and t.get("accepted"):
                    misses.append(dict(page=p, got=t))
        report.update(
            doc_type_accuracy=ratio(groups["original"]),
            rejection_rate=ratio(groups["nonfinancial"]),
            blurred_not_fully_readable=ratio(groups["blur"]),
            crop_accuracy=ratio(groups["crop"]),
            per_source={s: ratio(v) for s, v in sources.items()},
            accepted_but_should_reject=misses,
        )
    elif suite == "e4":
        groups, misses, tools = defaultdict(list), [], []
        for c, r in rows:
            f = final(r)
            answer = f.get("answer", "")
            ok = (
                c["template"] == "refusal" or f.get("intent") == "ask_data"
            ) and answer_matches(answer, c["expected"], c["payload"]["message"])
            groups[c["template"]].append(ok)
            tools.append(any(e["type"] == "tool" for e in r.get("events", [])))
            if not ok:
                misses.append(
                    dict(
                        question=c["payload"]["message"],
                        expected=c["expected"],
                        got=answer,
                    )
                )
        report.update(
            accuracy=ratio([x for k, v in groups.items() if k != "refusal" for x in v]),
            per_template={
                k: {"accuracy": ratio(v), "n": len(v)} for k, v in groups.items()
            },
            refusal_accuracy=ratio(groups["refusal"]),
            tool_call_rate=ratio(tools),
            misses=misses,
        )
    else:
        metrics = [
            memo_metrics(
                c, r, results.get(c.get("depends_on")) if c.get("depends_on") else None
            )
            for c, r in rows
        ]
        report.update(
            memos=metrics,
            all_sections_rate=ratio([m["all_sections"] for m in metrics]),
            valid_citation_rate=ratio(
                [m["all_sections"] and not m["bad_citations"] for m in metrics]
            ),
            problems=dict(sum((Counter(m["problems"]) for m in metrics), Counter())),
        )
    (directory / "score.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False)
    )
    with (Path(out) / "summary.md").open("a") as handle:
        handle.write(
            f"\n## {suite.upper()}\n\n```json\n{json.dumps(report, indent=2, ensure_ascii=False)}\n```\n"
        )
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "run", "score"))
    parser.add_argument(
        "--suite", choices=("e2", "e3", "e4", "e5", "all"), required=True
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--endpoint")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    if args.concurrency < 1 or (args.limit is not None and args.limit < 1):
        parser.error("concurrency and limit must be positive")
    if args.command == "run" and not args.endpoint:
        parser.error("run requires --endpoint")
    for suite in ("e2", "e3", "e4", "e5") if args.suite == "all" else [args.suite]:
        if args.command == "build":
            build(suite, args.out, args.limit)
        elif args.command == "run":
            run(suite, args.out, args.endpoint, args.concurrency, args.limit)
        else:
            score(suite, args.out)


if __name__ == "__main__":
    main()
