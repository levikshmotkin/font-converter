(() => {
  const dropzone = document.getElementById("dropzone");
  const fileInput = document.getElementById("file-input");
  const fileListWrap = document.getElementById("file-list-wrap");
  const fileList = document.getElementById("file-list");
  const clearBtn = document.getElementById("clear-btn");
  const convertBtn = document.getElementById("convert-btn");
  const statusEl = document.getElementById("status");

  /** @type {File[]} */
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

  function renderList() {
    fileList.innerHTML = "";
    for (let i = 0; i < queue.length; i++) {
      const f = queue[i];
      const li = document.createElement("li");
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = f.name;
      name.title = f.name;
      const rm = document.createElement("button");
      rm.type = "button";
      rm.className = "remove";
      rm.textContent = "Remove";
      rm.addEventListener("click", () => {
        queue = queue.filter((_, j) => j !== i);
        renderList();
        if (queue.length === 0) fileListWrap.classList.add("hidden");
      });
      li.append(name, rm);
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
    queue = queue.concat(added);
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

    const form = new FormData();
    for (const f of queue) form.append("files", f, f.name);

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
        let msg = `Downloaded ZIP with ${ok} font(s).`;
        if (bad > 0) {
          msg += ` ${bad} file(s) failed — see conversion-report.json inside the ZIP.`;
        }
        setStatus(msg, "ok");
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
