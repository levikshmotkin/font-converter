"""
Batch WOFF / WOFF2 → TTF conversion.

WOFF and WOFF2 wrap the same SFNT structure as TrueType/OpenType. Decompressing
with fontTools preserves all outline, hinting, layout (GSUB/GPOS), and variable
font (fvar, gvar, etc.) tables — no re-encoding of glyph data.
"""

from __future__ import annotations

import json
import re
import zipfile
from io import BytesIO
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fontTools.ttLib import TTFont

SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
PS_UNSAFE = re.compile(r"[^A-Za-z0-9-]+")
RIBBI = {"Regular", "Bold", "Italic", "Bold Italic"}

app = FastAPI(title="WOFF → TTF Converter")
app.mount("/static", StaticFiles(directory="static"), name="static")


def _safe_stem(name: str) -> str:
    base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    for suf in (".woff2", ".woff", ".ttf", ".otf"):
        if base.lower().endswith(suf):
            base = base[: -len(suf)]
            break
    cleaned = SAFE_NAME.sub("_", base).strip("._") or "font"
    return cleaned[:120]


def _degenerate(s: str | None) -> bool:
    """True for names that are missing or effectively blank (e.g. '.')."""
    return not (s or "").strip(" .\u00a0")


def check_installability(font: TTFont) -> list[str]:
    """Warn about traits that make the output useless as a desktop font."""
    warnings: list[str] = []
    name = font["name"]
    family = name.getDebugName(16) or name.getDebugName(1)
    if _degenerate(family):
        warnings.append(
            "The family name in this font is blank, so macOS and Windows "
            "will refuse to install it. Web-licensed fonts are often "
            "stripped this way on purpose. Setting a custom family name "
            "makes the file installable."
        )
    version = name.getDebugName(5) or ""
    if "subset" in version.lower():
        warnings.append(
            "The version string marks this as a subset web build, so part "
            "of the character set is missing. The desktop release from the "
            "foundry is the complete font."
        )
    return warnings


def rename_family(font: TTFont, family: str) -> None:
    """Rewrite the name table records that identify the font family.

    Sets legacy family/subfamily (IDs 1/2), unique ID (3), full name (4),
    PostScript name (6), and typographic family/subfamily (16/17) when the
    style is not one of the four style-linked names.
    """
    family = " ".join(family.split())
    name = font["name"]

    sub = name.getDebugName(17) or name.getDebugName(2)
    if _degenerate(sub):
        sub = "Regular"

    full = family if sub == "Regular" else f"{family} {sub}"
    ps_family = PS_UNSAFE.sub("", family.replace(" ", ""))
    ps_sub = PS_UNSAFE.sub("", sub.replace(" ", ""))
    ps_name = f"{ps_family}-{ps_sub}"[:63]
    version_match = re.search(r"\d+\.\d+", name.getDebugName(5) or "")
    unique_id = f"{version_match.group(0) if version_match else '1.000'};{ps_name}"

    if sub in RIBBI:
        updates = {1: family, 2: sub}
        if name.getDebugName(16) is not None:
            updates[16] = family
            updates[17] = sub
    else:
        # Non-style-linked style: legacy family carries the style, the
        # typographic pair carries the clean split.
        updates = {1: full, 2: "Regular", 16: family, 17: sub}

    updates.update({3: unique_id, 4: full, 6: ps_name})
    if name.getDebugName(25) is not None:  # variations PostScript prefix
        updates[25] = ps_family[:63]

    has_mac = any(rec.platformID == 1 for rec in name.names)
    for nid, value in updates.items():
        name.removeNames(nameID=nid)
        name.setName(value, nid, 3, 1, 0x409)
        if has_mac:
            name.setName(value, nid, 1, 0, 0)


def convert_to_sfnt(
    data: bytes, family: str | None = None
) -> tuple[bytes, str, list[str], str | None]:
    """Decompress WOFF/WOFF2 to standard SFNT.

    Returns (bytes, extension, installability warnings, PostScript name if
    the font was renamed).
    """
    font = TTFont(BytesIO(data))
    font.flavor = None
    renamed_ps = None
    if family:
        rename_family(font, family)
        renamed_ps = font["name"].getDebugName(6)
    warnings = check_installability(font)
    ext = (
        ".otf"
        if ("CFF " in font or "CFF2" in font) and "glyf" not in font
        else ".ttf"
    )
    out = BytesIO()
    font.save(out, reorderTables=False)
    return out.getvalue(), ext, warnings, renamed_ps


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    with open("static/index.html", encoding="utf-8") as f:
        return f.read()


@app.post("/api/convert")
async def convert_batch(
    files: list[UploadFile] = File(...),
    renames: Optional[str] = Form(None),
) -> Response:
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded.")

    rename_map: dict[str, str] = {}
    if renames:
        try:
            parsed = json.loads(renames)
            if not isinstance(parsed, dict):
                raise ValueError
            rename_map = {
                str(k): str(v).strip()
                for k, v in parsed.items()
                if str(v).strip()
            }
        except (json.JSONDecodeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail="renames must be a JSON object mapping filename to family name.",
            )

    results: list[dict] = []
    zip_buffer = BytesIO()

    with zipfile.ZipFile(
        zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED
    ) as zf:
        used_names: dict[str, int] = {}

        for upload in files:
            name = upload.filename or "unknown"
            lower = name.lower()
            if not (
                lower.endswith(".woff2")
                or (lower.endswith(".woff") and not lower.endswith(".woff2"))
            ):
                results.append(
                    {
                        "input": name,
                        "ok": False,
                        "error": "Not a .woff or .woff2 file.",
                    }
                )
                continue

            try:
                raw = await upload.read()
                if not raw:
                    results.append(
                        {
                            "input": name,
                            "ok": False,
                            "error": "Empty file.",
                        }
                    )
                    continue

                sfnt_bytes, ext, warnings, renamed_ps = convert_to_sfnt(
                    raw, rename_map.get(name)
                )
                stem = _safe_stem(renamed_ps or name)
                count = used_names.get(stem, 0)
                used_names[stem] = count + 1
                suffix = ext
                out_name = (
                    f"{stem}{suffix}"
                    if count == 0
                    else f"{stem}_{count + 1}{suffix}"
                )

                zf.writestr(out_name, sfnt_bytes)
                result = {"input": name, "ok": True, "output": out_name}
                if renamed_ps:
                    result["renamed_to"] = rename_map[name]
                if warnings:
                    result["warnings"] = warnings
                results.append(result)
            except Exception as e:  # noqa: BLE001 — surface any parse error
                results.append(
                    {
                        "input": name,
                        "ok": False,
                        "error": str(e) or type(e).__name__,
                    }
                )

        zf.writestr(
            "conversion-report.json",
            json.dumps(results, indent=2),
            compress_type=zipfile.ZIP_DEFLATED,
        )

    if not any(r["ok"] for r in results):
        raise HTTPException(
            status_code=422,
            detail={"message": "No files could be converted.", "results": results},
        )

    zip_buffer.seek(0)
    headers = {
        "X-Conversion-Report": json.dumps(results),
        "Content-Disposition": 'attachment; filename="converted-fonts.zip"',
    }
    return StreamingResponse(
        iter([zip_buffer.getvalue()]),
        media_type="application/zip",
        headers=headers,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8765, reload=True)
