// static/app.js
let pairs = [];
let cursor = 0;

async function refreshCount() {
  const r = await fetch("/api/count");
  const c = await r.json();
  document.getElementById("counts").textContent = `unlabeled: ${c.unlabeled}  |  labeled: ${c.labeled}`;
}

function render() {
  const pair = pairs[cursor];
  if (!pair) {
    document.getElementById("input-text").textContent = "(no pairs remaining)";
    document.getElementById("output-text").textContent = "";
    document.getElementById("corrected").value = "";
    return;
  }
  document.getElementById("input-text").textContent = pair.input_text;
  document.getElementById("output-text").textContent = pair.output_text;
  document.getElementById("corrected").value = pair.output_text;
  document.querySelectorAll("input[name=dispute]").forEach(cb => { cb.checked = false; });
}

async function load() {
  const r = await fetch("/api/pairs");
  pairs = await r.json();
  cursor = 0;
  render();
  refreshCount();
}

async function submitLabel() {
  const pair = pairs[cursor];
  if (!pair) return;
  const disputes = Array.from(document.querySelectorAll("input[name=dispute]:checked")).map(cb => cb.value);
  const corrected = document.getElementById("corrected").value;
  const body = {
    pair: pair,
    disputes: disputes,
    corrected_output: corrected,
    labeler: "keith",
  };
  const r = await fetch("/api/label", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    alert("save failed: " + r.status);
    return;
  }
  cursor++;
  render();
  refreshCount();
}

document.getElementById("submit").addEventListener("click", submitLabel);
document.getElementById("skip").addEventListener("click", () => { cursor++; render(); });
load();
