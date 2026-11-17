(() => {
  const out = document.querySelector("#m4-result");
  const show = (text) => { if (out) out.textContent = text; };
  const post = async (endpoint, payload) => {
    const res = await fetch(endpoint, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      show(`拒绝 ${res.status}: ${body.error || ""} ${body.message || ""}`.trim());
      return false;
    }
    show(JSON.stringify(body, null, 2));
    location.reload();
    return true;
  };
  document.querySelector("#m4-run")?.addEventListener("click", () => {
    const profile = document.querySelector("#m4-profile")?.value || "standard";
    post("/api/content/normalize", { profile });
  });
  document.querySelectorAll(".m4-rev-save").forEach((btn) =>
    btn.addEventListener("click", () => {
      const input = btn.parentElement.querySelector(".m4-rev-input");
      post("/api/content/revise", {
        archive_id: btn.dataset.aid,
        line_no: btn.dataset.lno,
        value: input ? input.value : "",
        base_version: Number(btn.dataset.ver),
      });
    }));
  document.querySelectorAll(".m4-undo").forEach((btn) =>
    btn.addEventListener("click", () =>
      post("/api/content/undo", { op_id: btn.dataset.op })));
  document.querySelectorAll(".m4-adj").forEach((btn) =>
    btn.addEventListener("click", () =>
      post("/api/content/adjudicate", {
        undo_id: btn.dataset.undo,
        archive_id: btn.dataset.aid,
        line_no: btn.dataset.lno,
        decision: btn.dataset.decision,
        conflict_version: Number(btn.dataset.ver),
      })));
})();
