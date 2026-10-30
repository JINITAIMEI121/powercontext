import { pipeline, env } from "/transformers.min.js";

const $ = (id) => document.getElementById(id);
const escapeHtml = (value) =>
  String(value).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);

env.allowRemoteModels = false;
env.localModelPath = "/models/";
env.backends.onnx.wasm.wasmPaths = "/models/wasm/";
env.backends.onnx.wasm.numThreads = 1;

let extractor = null;
let pendingDocuments = [];
let modelReady = null;

function banner(text, kind = "") {
  const element = $("status-banner");
  element.textContent = text;
  element.className = "banner" + (kind ? " " + kind : "");
}

async function loadModel() {
  const started = performance.now();
  banner("正在加载浏览器端 MiniLM-L6-v2 q8 模型…");
  try {
    extractor = await pipeline("feature-extraction", "Xenova/all-MiniLM-L6-v2", { quantized: true });
    const seconds = ((performance.now() - started) / 1000).toFixed(1);
    $("stat-model").textContent = "q8 ready";
    banner(`本地模型就绪 · MiniLM-L6-v2 q8 · 加载 ${seconds}s · 向量维度 384`, "ok");
    return true;
  } catch (error) {
    $("stat-model").textContent = "失败";
    banner(`模型加载失败：${error.message}。请确认 web/models 下的模型与 wasm 文件完整。`, "error");
    throw error;
  }
}

async function embed(texts) {
  if (!extractor) await modelReady;
  const output = await extractor(texts, { pooling: "mean", normalize: true });
  const [count, dimensions] = output.dims;
  const data = output.data;
  return Array.from({ length: count }, (_, index) =>
    Array.from(data.slice(index * dimensions, (index + 1) * dimensions))
  );
}

function chunkText(text, size = 380) {
  const clean = String(text || "").replace(/\s+/g, " ").trim();
  if (!clean) return [];
  const sentences = clean.split(/(?<=[。.!?！？])\s+/).filter(Boolean);
  const chunks = [];
  let buffer = "";
  for (const sentence of sentences) {
    if ((buffer + " " + sentence).trim().length > size && buffer) {
      chunks.push(buffer.trim());
      buffer = sentence;
    } else {
      buffer = (buffer + " " + sentence).trim();
    }
  }
  if (buffer) chunks.push(buffer);
  return chunks;
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

async function refreshStats() {
  try {
    const payload = await api("/api/health");
    $("stat-docs").textContent = String(payload.docs);
    $("stat-chunks").textContent = String(payload.chunks);
  } catch {
    $("stat-docs").textContent = "—";
    $("stat-chunks").textContent = "—";
  }
}

async function indexDocuments(documents) {
  await modelReady;
  if (!documents.length) {
    $("index-progress").textContent = "没有可索引的文档";
    return;
  }
  $("build-btn").disabled = true;
  $("sample-btn").disabled = true;
  const started = performance.now();
  let chunkTotal = 0;
  for (let docIndex = 0; docIndex < documents.length; docIndex++) {
    const document = documents[docIndex];
    const chunks = chunkText(document.text);
    if (!chunks.length) continue;
    $("index-progress").textContent = `编码 ${docIndex + 1}/${documents.length}：${document.title}（${chunks.length} 段）…`;
    const vectors = [];
    for (let start = 0; start < chunks.length; start += 4) {
      vectors.push(...(await embed(chunks.slice(start, start + 4))));
    }
    await api("/api/docs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: document.title,
        chunks: chunks.map((text, index) => ({ text, vector: vectors[index] })),
      }),
    });
    chunkTotal += chunks.length;
  }
  const seconds = ((performance.now() - started) / 1000).toFixed(2);
  $("index-progress").textContent = `已写入 ${documents.length} 篇 / ${chunkTotal} 个片段 · 浏览器端编码 ${seconds}s`;
  $("build-btn").disabled = false;
  $("sample-btn").disabled = false;
  await refreshStats();
}

async function loadSamples() {
  try {
    const documents = await api("/samples/docs.json");
    pendingDocuments = documents.map((item) => ({ title: item.title, text: item.text }));
    $("corpus").value = documents.map((item) => `# ${item.title}\n${item.text}`).join("\n\n");
    $("index-progress").textContent = `已载入 ${documents.length} 篇示例，正在建立索引…`;
    await indexDocuments(pendingDocuments);
  } catch (error) {
    $("index-progress").textContent = `载入示例失败：${error.message}`;
  }
}

async function buildFromInput() {
  if (pendingDocuments.length) {
    await indexDocuments(pendingDocuments);
    pendingDocuments = [];
    return;
  }
  const text = $("corpus").value.trim();
  if (!text) {
    $("index-progress").textContent = "请先粘贴文本或选择文件，或点击“载入示例文档”。";
    return;
  }
  await indexDocuments([{ title: "粘贴的笔记", text }]);
}

function renderResults(results, query) {
  const container = $("results");
  if (!results.length) {
    container.innerHTML = '<div class="result"><h3>没有命中</h3><p>换一个说法，或先建立索引。</p></div>';
    return;
  }
  const terms = query.split(/\s+/).filter(Boolean);
  const highlight = (text) => {
    let html = escapeHtml(text);
    for (const term of terms) {
      const safe = escapeHtml(term).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      html = html.replace(new RegExp(safe, "gi"), (match) => `<mark>${match}</mark>`);
    }
    return html;
  };
  container.innerHTML = results
    .map(
      (item) => `
        <article class="result">
          <div class="result-top">
            <h3>${escapeHtml(item.title)} · 片段 ${item.ordinal + 1}</h3>
            <span class="score">RRF ${item.score.toFixed(5)}</span>
          </div>
          <p>${highlight(item.text)}</p>
          <div class="badges">
            ${item.sources.map((source) => `<span class="badge ${source}">${source === "keyword" ? "关键词" : "语义"}</span>`).join("")}
            <span class="badge">chunk #${item.chunk_id}</span>
          </div>
        </article>`
    )
    .join("");
}

async function search() {
  const query = $("query").value.trim();
  if (!query) return;
  await modelReady;
  $("search-status").textContent = "查询编码中…";
  const embedStarted = performance.now();
  const [vector] = await embed([query]);
  const embedMs = performance.now() - embedStarted;
  const response = await api("/api/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, vector, limit: 6 }),
  });
  renderResults(response.results, query);
  $("search-status").textContent = `embedding ${embedMs.toFixed(1)}ms · seekdb ${response.elapsed_ms.toFixed(1)}ms · ${response.results.length} 条结果`;
}

async function validate() {
  await modelReady;
  let health = await api("/api/health");
  if (health.chunks === 0) await loadSamples();
  const cases = await api("/samples/queries.json");
  const lines = [];
  let passed = 0;
  for (const item of cases) {
    const [vector] = await embed([item.query]);
    const response = await api("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: item.query, vector, limit: 5 }),
    });
    const titles = response.results.map((result) => result.title);
    const ok = titles.includes(item.expect);
    if (ok) passed += 1;
    lines.push(
      `<div class="${ok ? "pass" : "fail"}">${ok ? "PASS" : "FAIL"} ${escapeHtml(item.query)} → ${titles.slice(0, 3).map(escapeHtml).join(" / ")}</div>`
    );
  }
  lines.push(`<div class="${passed === cases.length ? "pass" : "fail"}">${passed}/${cases.length} 条验证样例通过</div>`);
  $("validation").innerHTML = lines.join("");
}

function bind() {
  $("sample-btn").addEventListener("click", loadSamples);
  $("build-btn").addEventListener("click", buildFromInput);
  $("search-btn").addEventListener("click", search);
  $("validate-btn").addEventListener("click", validate);
  $("query").addEventListener("keydown", (event) => {
    if (event.key === "Enter") search();
  });
  $("reset-btn").addEventListener("click", async () => {
    await api("/api/reset", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    pendingDocuments = [];
    $("corpus").value = "";
    $("results").innerHTML = "";
    $("validation").innerHTML = "";
    $("index-progress").textContent = "索引已清空";
    await refreshStats();
  });
  $("file-input").addEventListener("change", async (event) => {
    const files = Array.from(event.target.files || []);
    const documents = [];
    for (const file of files) {
      documents.push({ title: file.name, text: await file.text() });
    }
    pendingDocuments = documents;
    $("corpus").value = documents.map((item) => `# ${item.title}\n${item.text}`).join("\n\n");
    $("index-progress").textContent = `已选择 ${documents.length} 个文件，点击“建立索引”。`;
  });
}

bind();
modelReady = loadModel();
modelReady.then(refreshStats).catch(() => {});
