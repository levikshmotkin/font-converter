(() => {
  const dropzone = document.getElementById("dropzone");
  const fileInput = document.getElementById("file-input");
  const fileListWrap = document.getElementById("file-list-wrap");
  const fileList = document.getElementById("file-list");
  const clearBtn = document.getElementById("clear-btn");
  const convertBtn = document.getElementById("convert-btn");
  const statusEl = document.getElementById("status");

  const warningsEl = document.getElementById("warnings");

  /** @type {{file: File, family: string}[]} */
  let queue = [];

  function isFontFile(file) {
    const n = file.name.toLowerCase();
    return n.endsWith(".woff") || n.endsWith(".woff2");
  }

  function setStatus(msg, kind) {
    statusEl.textContent = msg;
    statusEl.classList.remove("error", "ok");
    if (kind) statusEl.classList.add(kind);
  }

  function renderWarnings(report) {
    warningsEl.innerHTML = "";
    if (!Array.isArray(report)) return;
    for (const r of report) {
      if (!r.warnings?.length) continue;
      for (const w of r.warnings) {
        const li = document.createElement("li");
        li.textContent = `${r.input}: ${w}`;
        warningsEl.append(li);
      }
    }
    warningsEl.classList.toggle("hidden", warningsEl.children.length === 0);
  }

  function renderList() {
    fileList.innerHTML = "";
    for (let i = 0; i < queue.length; i++) {
      const entry = queue[i];
      const li = document.createElement("li");

      const row = document.createElement("div");
      row.className = "file-row";
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = entry.file.name;
      name.title = entry.file.name;
      const rm = document.createElement("button");
      rm.type = "button";
      rm.className = "remove";
      rm.textContent = "Remove";
      rm.addEventListener("click", () => {
        queue = queue.filter((_, j) => j !== i);
        renderList();
        if (queue.length === 0) fileListWrap.classList.add("hidden");
      });
      row.append(name, rm);

      const rename = document.createElement("input");
      rename.type = "text";
      rename.className = "rename";
      rename.placeholder = "Custom family name (optional)";
      rename.value = entry.family;
      rename.addEventListener("input", () => {
        entry.family = rename.value;
      });

      li.append(row, rename);
      fileList.append(li);
    }
    fileListWrap.classList.toggle("hidden", queue.length === 0);
  }

  function addFiles(fileListLike) {
    const added = [];
    for (const file of fileListLike) {
      if (isFontFile(file)) added.push(file);
    }
    if (added.length === 0) {
      setStatus("Only .woff and .woff2 files are accepted.", "error");
      return;
    }
    queue = queue.concat(added.map((file) => ({ file, family: "" })));
    renderList();
    setStatus(`${added.length} file(s) added. ${queue.length} total.`);
  }

  dropzone.addEventListener("click", () => fileInput.click());

  dropzone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  });

  dropzone.addEventListener("dragleave", () => {
    dropzone.classList.remove("dragover");
  });

  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
    if (e.dataTransfer?.files?.length) addFiles(e.dataTransfer.files);
  });

  fileInput.addEventListener("change", () => {
    if (fileInput.files?.length) addFiles(fileInput.files);
    fileInput.value = "";
  });

  clearBtn.addEventListener("click", () => {
    queue = [];
    renderList();
    fileListWrap.classList.add("hidden");
    setStatus("Cleared.");
  });

  convertBtn.addEventListener("click", async () => {
    if (queue.length === 0) return;
    convertBtn.disabled = true;
    setStatus("Converting…");
    renderWarnings(null);

    const form = new FormData();
    const renames = {};
    for (const entry of queue) {
      form.append("files", entry.file, entry.file.name);
      const family = entry.family.trim();
      if (family) renames[entry.file.name] = family;
    }
    if (Object.keys(renames).length > 0) {
      form.append("renames", JSON.stringify(renames));
    }

    try {
      const res = await fetch("/api/convert", {
        method: "POST",
        body: form,
      });

      const reportHeader = res.headers.get("X-Conversion-Report");
      let report = null;
      if (reportHeader) {
        try {
          report = JSON.parse(reportHeader);
        } catch {
          /* ignore */
        }
      }

      if (!res.ok) {
        let detail = res.statusText;
        try {
          const j = await res.json();
          if (j.detail?.message) detail = j.detail.message;
          else if (typeof j.detail === "string") detail = j.detail;
        } catch {
          /* ignore */
        }
        setStatus(detail || "Conversion failed.", "error");
        return;
      }

      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "converted-fonts.zip";
      a.click();
      URL.revokeObjectURL(url);

      if (report && Array.isArray(report)) {
        const ok = report.filter((r) => r.ok).length;
        const bad = report.filter((r) => !r.ok).length;
        const warned = report.filter((r) => r.warnings?.length).length;
        let msg = `Downloaded ZIP with ${ok} font(s).`;
        if (bad > 0) {
          msg += ` ${bad} file(s) failed — see conversion-report.json inside the ZIP.`;
        }
        if (warned > 0) {
          msg += ` ${warned} font(s) have warnings:`;
        }
        setStatus(msg, "ok");
        renderWarnings(report);
      } else {
        setStatus("Download started.", "ok");
      }
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Network error.", "error");
    } finally {
      convertBtn.disabled = false;
    }
  });
})();
