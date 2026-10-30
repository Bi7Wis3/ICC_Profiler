// Job page: while a stage runs in the background, show elapsed time, disable
// the run buttons, and reload the page once the stage has finished.
(function () {
  const box = document.getElementById("task-status");
  if (!box || box.dataset.state !== "running") return;

  const job = box.dataset.job;
  const elapsed = document.getElementById("task-elapsed");
  const started = Date.now();
  document.querySelectorAll(".stage-button").forEach((b) => (b.disabled = true));

  async function poll() {
    try {
      const resp = await fetch(`/jobs/${encodeURIComponent(job)}/task`, { cache: "no-store" });
      const status = await resp.json();
      if (status.state !== "running") {
        window.location.replace(`/jobs/${encodeURIComponent(job)}`);
        return;
      }
      if (elapsed && status.started) {
        const secs = Math.round(Date.now() / 1000 - status.started);
        elapsed.textContent = `(${Math.floor(secs / 60)}m ${secs % 60}s)`;
      }
    } catch (e) {
      // Server restarting (uvicorn --reload) or briefly unreachable: keep trying.
    }
    setTimeout(poll, 2000);
  }
  if (elapsed) elapsed.textContent = `(${Math.round((Date.now() - started) / 1000)}s)`;
  poll();
})();
