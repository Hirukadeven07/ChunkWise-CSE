"""Build small Docling-format documents and bookmarked PDFs for the section tests, so no real
report is needed."""

from __future__ import annotations


def docling_doc(pages: list[list[tuple[str, str]]], grouped: bool = False) -> dict:
    """A Docling document with one list of (label, text) items per page, in reading order.

    grouped puts every page's items inside a group, as Docling does for lists and some
    layouts, so the tests check that references through groups are followed.
    """
    texts, groups, body = [], [], []
    for page_no, items in enumerate(pages, 1):
        refs = []
        for label, text in items:
            ref = f"#/texts/{len(texts)}"
            texts.append(
                {
                    "self_ref": ref,
                    "label": label,
                    "text": text,
                    "orig": text,
                    "children": [],
                    "prov": [{"page_no": page_no, "bbox": {"l": 0, "t": 0, "r": 1, "b": 1}}],
                }
            )
            refs.append({"$ref": ref})
        if grouped:
            group_ref = f"#/groups/{len(groups)}"
            groups.append({"self_ref": group_ref, "label": "unspecified", "children": refs})
            body.append({"$ref": group_ref})
        else:
            body.extend(refs)
    return {
        "schema_name": "DoclingDocument",
        "body": {"self_ref": "#/body", "children": body},
        "groups": groups,
        "texts": texts,
        "tables": [],
        "pages": {str(n): {"page_no": n} for n in range(1, len(pages) + 1)},
    }


def pdf_with_bookmarks(page_count: int, marks: list[tuple[str, int]], use_actions: bool = False) -> bytes:
    """A PDF with empty pages and one bookmark per (title, 1-based page).

    Bookmarks point to their page with /Dest, or with a /GoTo action when use_actions is set,
    since real reports use both.
    """
    objects: list[str] = []

    def add(body: str) -> int:
        objects.append(body)
        return len(objects)

    catalog = add("")
    tree = add("")
    outlines = add("")
    pages = [
        add(f"<< /Type /Page /Parent {tree} 0 R /MediaBox [0 0 595 842] >>") for _ in range(page_count)
    ]
    items = [add("") for _ in marks]
    for index, ((title, page), number) in enumerate(zip(marks, items)):
        target = f"[{pages[page - 1]} 0 R /Fit]"
        link = f"/A << /S /GoTo /D {target} >>" if use_actions else f"/Dest {target}"
        siblings = ""
        if index > 0:
            siblings += f" /Prev {items[index - 1]} 0 R"
        if index < len(items) - 1:
            siblings += f" /Next {items[index + 1]} 0 R"
        objects[number - 1] = f"<< /Title ({title}) /Parent {outlines} 0 R {link}{siblings} >>"

    objects[catalog - 1] = f"<< /Type /Catalog /Pages {tree} 0 R /Outlines {outlines} 0 R >>"
    objects[tree - 1] = (
        f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in pages)}] /Count {page_count} >>"
    )
    if items:
        objects[outlines - 1] = (
            f"<< /Type /Outlines /First {items[0]} 0 R /Last {items[-1]} 0 R /Count {len(items)} >>"
        )
    else:
        objects[outlines - 1] = "<< /Type /Outlines /Count 0 >>"

    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
