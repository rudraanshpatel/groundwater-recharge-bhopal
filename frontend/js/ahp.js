/*
 * Layer weight sliders and the AHP matrix panel.
 *
 * Sliders hold raw importance values (0-50). The weights sent to the backend
 * are those values normalised to sum to 1, shown next to each slider.
 * "Use AHP matrix" loads the eigenvector weights of the default pairwise
 * matrix from /api/ahp; "Default weights" restores the literature weights.
 */
"use strict";

(function () {
  const SLIDER_MAX = 50;
  let ahpResult = null;

  function labels() {
    const out = {};
    GWR.config.layers.forEach((l) => (out[l.id] = l.label));
    return out;
  }

  function buildSliders() {
    const container = document.getElementById("weight-sliders");
    container.innerHTML = GWR.config.layers.map((l) => `
      <div class="slider">
        <label for="w-${l.id}">${GWR.escape(l.label)}</label>
        <input type="range" id="w-${l.id}" data-layer="${l.id}" min="0" max="${SLIDER_MAX}" step="0.5">
        <output id="o-${l.id}" for="w-${l.id}"></output>
      </div>`).join("");
    container.querySelectorAll("input[type=range]").forEach((input) =>
      input.addEventListener("input", readSliders));
  }

  function setSliders(weights) {
    // Scale so the largest weight sits at 80% of the slider range.
    const max = Math.max(...Object.values(weights));
    GWR.config.layers.forEach((l) => {
      const v = max > 0 ? ((weights[l.id] || 0) / max) * SLIDER_MAX * 0.8 : 0;
      document.getElementById(`w-${l.id}`).value = v.toFixed(1);
    });
    readSliders();
  }

  function readSliders() {
    const raw = {};
    let total = 0;
    GWR.config.layers.forEach((l) => {
      const v = parseFloat(document.getElementById(`w-${l.id}`).value) || 0;
      raw[l.id] = v;
      total += v;
    });
    const weights = {};
    GWR.config.layers.forEach((l) => {
      weights[l.id] = total > 0 ? raw[l.id] / total : 0;
      document.getElementById(`o-${l.id}`).value = weights[l.id].toFixed(3);
    });
    GWR.weights = weights;
    const run = document.getElementById("run");
    run.disabled = total <= 0;
    if (total <= 0) GWR.setStatus("At least one layer needs a weight above zero.", true);
  }

  function renderMatrix(res) {
    const names = labels();
    const short = (id) => GWR.escape((names[id] || id).split(" ")[0]);
    const fmt = (v) => (v >= 1 ? (Math.round(v * 100) / 100).toString() : `1/${Math.round(1 / v)}`);
    const head = `<tr><th></th>${res.criteria.map((c) => `<th>${short(c)}</th>`).join("")}<th>w</th></tr>`;
    const rows = res.matrix.map((row, i) => `
      <tr><th>${short(res.criteria[i])}</th>${row.map((v) => `<td>${fmt(v)}</td>`).join("")}
          <td><b>${res.weights[res.criteria[i]].toFixed(3)}</b></td></tr>`).join("");
    document.getElementById("ahp-matrix").innerHTML = `<table>${head}${rows}</table>`;
    document.getElementById("ahp-cr").innerHTML =
      `&lambda;<sub>max</sub> = ${res.lambda_max.toFixed(3)}, CI = ${res.consistency_index.toFixed(4)}, ` +
      `RI = ${res.random_index}, <b>CR = ${res.consistency_ratio.toFixed(4)}</b> ` +
      (res.is_consistent ? "(consistent, CR &lt; 0.10)" : "(NOT consistent, revise judgements)");
  }

  async function loadAhp() {
    try {
      ahpResult = await (await GWR.api("/api/ahp")).json();
      renderMatrix(ahpResult);
    } catch (err) {
      document.getElementById("ahp-cr").textContent = `Could not load AHP matrix: ${err.message}`;
    }
  }

  GWR.onReady.push(() => {
    buildSliders();
    setSliders(GWR.config.default_weights);
    loadAhp();
    document.getElementById("weights-reset").addEventListener("click", () =>
      setSliders(GWR.config.default_weights));
    document.getElementById("weights-ahp").addEventListener("click", () => {
      if (ahpResult) setSliders(ahpResult.weights);
      document.getElementById("ahp-details").open = true;
    });
  });
})();
