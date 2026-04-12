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

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fontTools.ttLib import TTFont

SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")

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


def convert_to_sfnt(data: bytes) -> tuple[bytes, str]:
    """Decompress WOFF/WOFF2 to standard SFNT; return bytes and file extension."""
    font = TTFont(BytesIO(data))
    font.flavor = None
    ext = (
        ".otf"
        if ("CFF " in font or "CFF2" in font) and "glyf" not in font
        else ".ttf"
    )
    out = BytesIO()
    font.save(out, reorderTables=False)
    return out.getvalue(), ext


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    with open("static/index.html", encoding="utf-8") as f:
        return f.read()


@app.post("/api/convert")
async def convert_batch(files: list[UploadFile] = File(...)) -> Response:
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded.")

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

                sfnt_bytes, ext = convert_to_sfnt(raw)
                stem = _safe_stem(name)
                count = used_names.get(stem, 0)
                used_names[stem] = count + 1
                suffix = ext
                out_name = (
                    f"{stem}{suffix}"
                    if count == 0
                    else f"{stem}_{count + 1}{suffix}"
                )

                zf.writestr(out_name, sfnt_bytes)
                results.append({"input": name, "ok": True, "output": out_name})
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
