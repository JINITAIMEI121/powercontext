(() => {
  const demo = window.NOTESEEK_DEMO || { notes: [], results: {} };
  let demoMode = false;

  const $ = (id) => document.getElementById(id);
  const escapeHtml = (value) =>
    String(value).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
  const escapeRegExp = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

  function highlight(text, query) {
    let html = escapeHtml(text);
    const tokens = (query || "").split(/\s+/).filter((token) => token.length > 0);
    for (const token of tokens) {
      html = html.replace(new RegExp(escapeRegExp(escapeHtml(token)), "gi"), (match) => `<mark>${match}</mark>`);
    }
    return html;
  }

  function renderResults(results, query) {
    const container = $("results");
    if (!results.length) {
      container.innerHTML = '<div class="result"><h3>没有命中</h3><p>换一个关键词，或先新增一条笔记。</p></div>';
      return;
    }
    container.innerHTML = results
      .map(
        (item) => `
        <article class="result">
          <div class="result-top">
            <h3>${escapeHtml(item.title)}</h3>
            <span class="score">RRF ${Number(item.score).toFixed(4)}</span>
          </div>
          <p>${highlight(item.snippet, query)}</p>
          <div class="badges">
            ${(item.sources || []).map((source) => `<span class="badge ${source}">${source === "keyword" ? "关键词" : "语义"}</span>`).join("")}
            <span class="badge">note #${item.id}</span>
          </div>
        </article>`
      )
      .join("");
  }

  function demoSearch(query) {
    const tokens = query.toLowerCase().split(/\s+/).filter(Boolean);
    return demo.notes
      .map((note) => {
        const haystack = `${note.title} ${note.body}`.toLowerCase();
        const hits = tokens.filter((token) => haystack.includes(token)).length;
        return { note, hits };
      })
      .filter((item) => item.hits > 0)
      .sort((left, right) => right.hits - left.hits)
      .slice(0, 8)
      .map((item, index) => ({
        id: item.note.id,
        title: item.note.title,
        snippet: item.note.body.slice(0, 180) + (item.note.body.length > 180 ? "..." : ""),
        score: 1 / (1 + index),
        sources: ["keyword", "semantic"],
      }));
  }

  async function search() {
    const query = $("query").value.trim();
    if (!query) return;
    $("search-status").textContent = "检索中…";
    if (demoMode) {
      const results = demo.results[query] || demoSearch(query);
      renderResults(results, query);
      $("search-status").textContent = `静态演示 · ${results.length} 条结果`;
      return;
    }
    try {
      const response = await fetch(`/api/search?q=${encodeURIComponent(query)}&limit=8`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "search failed");
      renderResults(payload.results, query);
      $("search-status").textContent = `${payload.count} 条结果 · 关键词 + 语义双路融合`;
    } catch (error) {
      $("search-status").textContent = `检索失败：${error.message}`;
    }
  }

  async function addNote() {
    const title = $("new-title").value.trim();
    const body = $("new-body").value.trim();
    if (!title || !body) {
      $("add-status").textContent = "标题和正文都不能为空";
      return;
    }
    $("add-status").textContent = "写入中…";
    if (demoMode) {
      $("add-status").textContent = "静态演示无法写入；请在本地运行 app.py 体验完整读写。";
      return;
    }
    try {
      const response = await fetch("/api/notes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, body }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "write failed");
      $("add-status").textContent = `已写入 note #${payload.note.id}`;
      $("new-title").value = "";
      $("new-body").value = "";
      await refreshMeta();
    } catch (error) {
      $("add-status").textContent = `写入失败：${error.message}`;
    }
  }

  async function refreshMeta() {
    if (demoMode) {
      $("note-count").textContent = String(demo.notes.length);
      return;
    }
    try {
      const response = await fetch("/api/health");
      const payload = await response.json();
      $("note-count").textContent = String(payload.notes);
    } catch {
      $("note-count").textContent = "—";
    }
  }

  async function boot() {
    $("search-btn").addEventListener("click", search);
    $("query").addEventListener("keydown", (event) => {
      if (event.key === "Enter") search();
    });
    $("add-btn").addEventListener("click", addNote);
    document.querySelectorAll(".example").forEach((button) => {
      button.addEventListener("click", () => {
        $("query").value = button.dataset.query;
        search();
      });
    });
    try {
      const response = await fetch("/api/health");
      if (!response.ok) throw new Error("offline");
      const payload = await response.json();
      $("note-count").textContent = String(payload.notes);
    } catch {
      demoMode = true;
      $("demo-banner").classList.remove("hidden");
      $("note-count").textContent = String(demo.notes.length);
    }
  }

  boot();
})();
