"""Offline field, row, reconciliation and cited anomaly evaluation."""

import argparse
import json
import re
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from ledgerlens.analytics import detect_anomalies, reconciliation_checks
from ledgerlens.schema import DOCUMENT_ADAPTER, Invoice, Receipt, Statement

MONEY_FIELDS = {
    "debit",
    "credit",
    "balance",
    "opening_balance",
    "closing_balance",
    "unit_price",
    "amount",
    "subtotal",
    "tax",
    "total",
    "quantity",
}
DATE_FIELDS = {"date", "period_start", "period_end"}
ITEM_FIELDS = {"transactions", "line_items", "menu_items"}


def normalize_money(value: Any) -> Decimal | None:
    """Parse currency, grouping, parentheses and credit/debit suffixes exactly."""
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    text = str(value).strip().upper()
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("() ")
    suffix = re.search(r"(CR|DR)\s*$", text)
    if suffix:
        negative = negative or suffix.group(1) == "DR"
        text = text[: suffix.start()]
    text = re.sub(r"\b(?:SGD|MYR|IDR|USD|RM|RP|S\$)\b", "", text)
    text = re.sub(r"[$€£¥,\s]", "", text).strip()
    # S$ prefixes leave S after the symbol is stripped.
    text = re.sub(r"^(?:S|SG|US)(?=[+-]?\d)", "", text)
    try:
        amount = Decimal(text)
    except InvalidOperation as error:
        raise ValueError(f"Invalid monetary value: {value!r}") from error
    if not amount.is_finite():
        raise ValueError("Monetary values must be finite")
    return -abs(amount) if negative else amount


def normalize_date(value: Any) -> date:
    """Parse ISO and day-first numeric or textual dates without guessing locale."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            pass
    raise ValueError(f"Unsupported date: {value!r}")


def normalize_fields(value: Any) -> Any:
    """Recursively normalize known date and money fields before validation."""
    if isinstance(value, list):
        return [normalize_fields(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        key: normalize_money(item)
        if key in MONEY_FIELDS
        else normalize_date(item)
        if key in DATE_FIELDS
        else normalize_fields(item)
        for key, item in value.items()
    }


def parse_document(value: dict | str | BaseModel) -> Statement | Invoice | Receipt:
    """Validate a normalized extraction through the discriminated union."""
    if isinstance(value, BaseModel):
        value = value.model_dump()
    if isinstance(value, str):
        value = json.loads(value)
    return DOCUMENT_ADAPTER.validate_python(normalize_fields(value))


def row_metrics(predicted: list, gold: list, statement: bool = True) -> dict:
    """Greedily match identical date/amount keys, consuming duplicates once."""

    def key(row: Any) -> tuple:
        if statement:
            return row.date, row.debit or Decimal(0), row.credit or Decimal(0)
        return row.description, row.quantity, row.unit_price, row.amount

    available: dict[tuple, list[int]] = defaultdict(list)
    for i, row in enumerate(gold):
        available[key(row)].append(i)
    matches = []
    for i, row in enumerate(predicted):
        candidates = available[key(row)]
        if candidates:
            matches.append({"predicted_row": i, "gold_row": candidates.pop(0)})
    count = len(matches)
    precision = count / len(predicted) if predicted else float(not gold)
    recall = count / len(gold) if gold else float(not predicted)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "matched": count,
        "predicted": len(predicted),
        "gold": len(gold),
        "matches": matches,
    }


def score_document(
    predicted: dict | str | BaseModel, gold: dict | str | BaseModel
) -> dict:
    """Score a valid document, including printed-page reconciliation agreement."""
    prediction = parse_document(predicted)
    target = parse_document(gold)
    header = {
        key: getattr(prediction, key, None) == value
        for key, value in target.model_dump().items()
        if key not in ITEM_FIELDS
    }
    row_field = (
        "transactions"
        if isinstance(target, Statement)
        else "line_items"
        if isinstance(target, Invoice)
        else "menu_items"
    )
    rows = row_metrics(
        getattr(prediction, row_field, []),
        getattr(target, row_field),
        isinstance(target, Statement),
    )
    pred_reconciles = None
    gold_reconciles = None
    if isinstance(target, Statement):
        gold_reconciles = reconciliation_checks([target])[0]["reconciles"]
        pred_reconciles = (
            reconciliation_checks([prediction])[0]["reconciles"]
            if isinstance(prediction, Statement)
            else False
        )
    return {
        "header_fields": header,
        "header_accuracy": sum(header.values()) / len(header),
        "rows": rows,
        "page_reconciles": pred_reconciles,
        "gold_page_reconciles": gold_reconciles,
        "reconciliation_accuracy": float(pred_reconciles == gold_reconciles),
    }


def anomaly_recall(
    statements: list[Statement], page_ids: list[str], labels: list[dict]
) -> dict:
    """Match planted events to cited findings, including events spanning pages."""
    findings = detect_anomalies(statements, page_ids)
    detected = 0
    used: set[int] = set()
    kinds = {
        "returned_cheque",
        "round_trip",
        "large_cash_deposit",
        "customer_concentration",
        "unreconciled_page",
    }
    by_type = {
        kind: {
            "detected": 0,
            "total": 0,
            "predicted": sum(f["type"] == kind for f in findings),
        }
        for kind in kinds
    }
    for label in labels:
        expected = {
            (citation["page_id"], citation["row_index"])
            for citation in label["citations"]
        }
        found = False
        for index, finding in enumerate(findings):
            if index in used or finding["type"] != label["type"]:
                continue
            actual = {
                (citation["page_id"], citation["row_index"])
                for citation in finding["citations"]
            }
            if label["type"] == "unreconciled_page":
                found = bool(
                    {page for page, _ in expected} & {page for page, _ in actual}
                )
            else:
                found = bool(expected) and expected <= actual
            if found:
                used.add(index)
                break
        bucket = by_type.setdefault(label["type"], {"detected": 0, "total": 0})
        bucket["total"] += 1
        bucket["detected"] += int(found)
        detected += int(found)
    for bucket in by_type.values():
        bucket["false_positives"] = bucket["predicted"] - bucket["detected"]
        bucket["precision"] = (
            bucket["detected"] / bucket["predicted"] if bucket["predicted"] else 1.0
        )
        bucket["recall"] = (
            bucket["detected"] / bucket["total"] if bucket["total"] else 1.0
        )
    return {
        "predicted": len(findings),
        "precision": detected / len(findings) if findings else 1.0,
        "detected": detected,
        "total": len(labels),
        "recall": detected / len(labels) if labels else 1.0,
        "by_type": by_type,
    }


def score_batch(pred_dir: Path, gold_dir: Path) -> dict:
    """Evaluate expected files; missing or invalid predictions count as failures."""
    gold_paths = sorted(
        path
        for path in gold_dir.glob("*.json")
        if not path.name.endswith(".meta.json") and path.name != "splits.json"
    )
    if not gold_paths:
        raise ValueError("No ground-truth documents found")
    pages: dict[str, dict] = {}
    groups: dict[str, dict] = {}
    valid_json = valid_documents = header_correct = header_total = 0
    matched = predicted_count = gold_count = reconciles = reconcile_agreement = (
        statement_count
    ) = 0
    for gold_path in gold_paths:
        page_id = gold_path.stem
        target = parse_document(gold_path.read_text(encoding="utf-8"))
        prediction = None
        error = None
        try:
            raw = json.loads((pred_dir / gold_path.name).read_text(encoding="utf-8"))
            valid_json += 1
            prediction = parse_document(raw)
            valid_documents += 1
        except (OSError, ValueError, TypeError, ValidationError) as exc:
            error = str(exc)
        if prediction is not None:
            result = score_document(prediction, target)
        else:
            fields = {
                key: False for key in target.model_dump() if key not in ITEM_FIELDS
            }
            field = (
                "transactions"
                if isinstance(target, Statement)
                else "line_items"
                if isinstance(target, Invoice)
                else "menu_items"
            )
            result = {
                "header_fields": fields,
                "header_accuracy": 0.0,
                "rows": row_metrics(
                    [], getattr(target, field), isinstance(target, Statement)
                ),
                "page_reconciles": False,
                "reconciliation_accuracy": 0.0,
                "error": error,
            }
        pages[page_id] = result
        header_correct += sum(result["header_fields"].values())
        header_total += len(result["header_fields"])
        matched += result["rows"]["matched"]
        predicted_count += result["rows"]["predicted"]
        gold_count += result["rows"]["gold"]
        if not isinstance(target, Statement):
            continue
        statement_count += 1
        reconciles += int(bool(result["page_reconciles"]))
        reconcile_agreement += int(result["reconciliation_accuracy"])
        metadata_path = gold_dir / f"{page_id}.meta.json"
        metadata = (
            json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata_path.exists()
            else {}
        )
        business_id = metadata.get("business_id", target.account_holder)
        group = groups.setdefault(
            business_id, {"statements": [], "page_ids": [], "labels": {}}
        )
        if isinstance(prediction, Statement):
            # Remap citations back to gold indices so a dropped row cannot shift labels.
            group["statements"].append(prediction)
            group["page_ids"].append(page_id)
        for label in metadata.get("anomalies", []):
            event_id = (label["type"], label.get("label_id", label["type"]))
            event = group["labels"].setdefault(
                event_id, {"type": label["type"], "citations": []}
            )
            event["citations"].extend(
                {"page_id": page_id, "row_index": index}
                for index in label["transaction_indices"]
            )
    anomaly_detected = anomaly_total = anomaly_predicted = 0
    by_type: dict[str, dict] = {}
    for group in groups.values():
        # Findings use prediction indices; map matching rows into the gold coordinate system.
        labels = list(group["labels"].values())
        mapped_labels = []
        for label in labels:
            citations = []
            for citation in label["citations"]:
                page_id = citation["page_id"]
                mapping = {
                    match["gold_row"]: match["predicted_row"]
                    for match in pages[page_id]["rows"]["matches"]
                }
                if label["type"] == "unreconciled_page":
                    citations.append(citation)
                elif citation["row_index"] in mapping:
                    citations.append(
                        {
                            "page_id": page_id,
                            "row_index": mapping[citation["row_index"]],
                        }
                    )
            mapped_labels.append({**label, "citations": citations})
        result = anomaly_recall(group["statements"], group["page_ids"], mapped_labels)
        anomaly_detected += result["detected"]
        anomaly_total += result["total"]
        anomaly_predicted += result["predicted"]
        for kind, bucket in result["by_type"].items():
            aggregate = by_type.setdefault(
                kind, {"detected": 0, "total": 0, "predicted": 0}
            )
            aggregate["detected"] += bucket["detected"]
            aggregate["total"] += bucket["total"]
            aggregate["predicted"] += bucket["predicted"]
    for bucket in by_type.values():
        bucket["false_positives"] = bucket["predicted"] - bucket["detected"]
        bucket["precision"] = (
            bucket["detected"] / bucket["predicted"] if bucket["predicted"] else 1.0
        )
        bucket["recall"] = (
            bucket["detected"] / bucket["total"] if bucket["total"] else 1.0
        )
    precision = matched / predicted_count if predicted_count else float(not gold_count)
    recall = matched / gold_count if gold_count else float(not predicted_count)
    return {
        "documents": len(gold_paths),
        "valid_json_rate": valid_json / len(gold_paths),
        "valid_document_rate": valid_documents / len(gold_paths),
        "header_accuracy": header_correct / header_total,
        "row_precision": precision,
        "row_recall": recall,
        "row_f1": 2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0,
        "page_reconciliation_rate": reconciles / statement_count
        if statement_count
        else None,
        "reconciliation_accuracy": reconcile_agreement / statement_count
        if statement_count
        else None,
        "anomaly_precision": anomaly_detected / anomaly_predicted
        if anomaly_predicted
        else 1.0,
        "anomaly_recall": anomaly_detected / anomaly_total if anomaly_total else None,
        "anomalies": {
            "detected": anomaly_detected,
            "total": anomaly_total,
            "by_type": by_type,
        },
        "pages": pages,
    }


def write_markdown(report: dict, out: Path) -> None:
    """Write an aggregate metric table, with per-type anomaly counts."""
    lines = ["| Metric | Value |", "| --- | ---: |"]
    for key, value in report.items():
        if isinstance(value, (int, float)) or value is None:
            formatted = (
                f"{value:.4f}"
                if isinstance(value, float)
                else "N/A"
                if value is None
                else str(value)
            )
            lines.append(f"| {key} | {formatted} |")
    lines.extend(
        [
            "",
            "| Anomaly | Detected | Labelled | Precision | Recall |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for kind, bucket in report["anomalies"]["by_type"].items():
        lines.append(
            f"| {kind} | {bucket['detected']} | {bucket['total']} | {bucket['precision']:.4f} | {bucket['recall']:.4f} |"
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    """Score local directories and write Markdown plus a JSON companion."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pred", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = score_batch(args.pred, args.gold)
    markdown = args.out if args.out.suffix == ".md" else args.out.with_suffix(".md")
    json_path = (
        args.out if args.out.suffix == ".json" else args.out.with_suffix(".json")
    )
    write_markdown(report, markdown)
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote {markdown} and {json_path}")


if __name__ == "__main__":
    main()
