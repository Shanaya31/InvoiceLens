#!/usr/bin/env python3
"""Render synthetic invoices and receipts from the evaluation ground truth.

Every document is drawn from ``evaluation/ground_truth/sample_annotations.jsonl``, so the labels
match what is printed on the page by construction. Vendors, GSTINs and addresses are invented.
Output goes to ``sample_data/synthetic/`` (git-ignored; regenerate any time).

    python sample_data/generate_samples.py
"""
from __future__ import annotations

import argparse
import io
import json
import random
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pymupdf
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ANNOTATIONS = ROOT / "evaluation" / "ground_truth" / "sample_annotations.jsonl"
DEFAULT_OUTPUT = ROOT / "sample_data"

Row = Tuple[str, str, str]  # (kind, left text, right text)


def money(value: str) -> str:
    return f"Rs. {Decimal(value):,.2f}"


def build_rows(fields: Dict[str, Any], render: Dict[str, Any]) -> List[Row]:
    rows: List[Row] = [("title", fields["vendor_name"], "")]
    if render.get("address"):
        rows.append(("text", render["address"], ""))
    if fields.get("vendor_gstin"):
        rows.append(("text", f"GSTIN: {fields['vendor_gstin']}", ""))
    rows.append(("title2", "TAX INVOICE" if fields["document_type"] == "invoice" else "RECEIPT", ""))
    if fields.get("invoice_number"):
        rows.append(("text", f"Invoice No: {fields['invoice_number']}", ""))
    rows.append(("text", f"Date: {render['date_text']}", ""))
    rows.append(("rule", "", ""))
    rows.append(("head", "Description", "Qty x Rate = Amount"))

    item_total = Decimal("0")
    for name, qty, rate in render["items"]:
        amount = Decimal(qty) * Decimal(rate)
        item_total += amount
        rows.append(("text", name, f"{qty} x {Decimal(rate):,.2f} = {amount:,.2f}"))
    expected = fields.get("subtotal") or fields.get("total_amount")
    if Decimal(expected) != item_total:
        raise ValueError(f"{fields['vendor_name']}: line items sum to {item_total}, label says {expected}")

    rows.append(("rule", "", ""))
    for label, key in (
        ("Subtotal", "subtotal"),
        ("Discount", "discount_amount"),
        ("CGST", "cgst_amount"),
        ("SGST", "sgst_amount"),
        ("IGST", "igst_amount"),
    ):
        if fields.get(key) is not None:
            rows.append(("text", label, money(fields[key])))
    rows.append(("bold", "TOTAL", money(fields["total_amount"])))
    return rows


# ---- raster rendering -------------------------------------------------------------------------------
def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)


def render_png(rows: List[Row], width: int) -> Image.Image:
    scale = width / 900
    sizes = {"title": 34, "title2": 26, "head": 20, "bold": 24, "text": 20}
    height = int((120 + 34 * len(rows)) * scale) + 40
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    y = int(30 * scale)
    margin = int(40 * scale)
    for kind, left, right in rows:
        if kind == "rule":
            draw.line((margin, y + 8, width - margin, y + 8), fill="black", width=2)
            y += int(22 * scale)
            continue
        f = font(max(10, int(sizes[kind] * scale)))
        draw.text((margin, y), left, fill="black", font=f)
        if right:
            draw.text((width - margin - draw.textlength(right, font=f), y), right, fill="black", font=f)
        y += int((sizes[kind] + 14) * scale)
    return image


def degrade(image: Image.Image, *, rotate: float, blur: float, quality: int, seed: int) -> bytes:
    rng = random.Random(seed)
    width, height = image.size
    background = Image.new("RGB", (width, height), (232, 232, 226))
    image = image.rotate(rotate, expand=True, fillcolor=(232, 232, 226))
    noise = Image.frombytes("L", image.size, rng.randbytes(image.size[0] * image.size[1])).convert("RGB")
    image = Image.blend(image, noise, 0.10)
    if blur:
        image = image.filter(ImageFilter.GaussianBlur(blur))
    del background
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=quality)
    return out.getvalue()


def render_pdf(rows: List[Row]) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()  # A4
    y = 60
    sizes = {"title": 18, "title2": 14, "head": 10, "bold": 12, "text": 10}
    for kind, left, right in rows:
        if kind == "rule":
            page.draw_line((50, y - 4), (545, y - 4))
            y += 10
            continue
        size = sizes[kind]
        fontname = "hebo" if kind in ("title", "title2", "bold", "head") else "helv"
        page.insert_text((50, y), left, fontsize=size, fontname=fontname)
        if right:
            x = 545 - pymupdf.get_text_length(right, fontname=fontname, fontsize=size)
            page.insert_text((x, y), right, fontsize=size, fontname=fontname)
        y += size + 8
    data = doc.tobytes()
    doc.close()
    return data


def render_document(record: Dict[str, Any], seed: int) -> bytes:
    rows = build_rows(record["fields"], record["render"])
    style = record["render"]["style"]
    if style == "pdf":
        return render_pdf(rows)
    if style == "png":
        out = io.BytesIO()
        render_png(rows, 900).save(out, format="PNG")
        return out.getvalue()
    if style == "scan":
        return degrade(render_png(rows, 900), rotate=2.2, blur=0.8, quality=50, seed=seed)
    if style == "blurry_receipt":
        return degrade(render_png(rows, 420), rotate=-3.0, blur=1.3, quality=35, seed=seed)
    raise ValueError(f"unknown render style: {style}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--annotations", type=Path, default=DEFAULT_ANNOTATIONS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    count = 0
    with args.annotations.open(encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if not line.strip():
                continue
            record = json.loads(line)
            target = args.output_dir / record["file"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(render_document(record, seed=1000 + index))
            print(f"wrote {target.relative_to(ROOT) if target.is_relative_to(ROOT) else target}")
            count += 1
    print(f"{count} synthetic documents generated.")


if __name__ == "__main__":
    main()
