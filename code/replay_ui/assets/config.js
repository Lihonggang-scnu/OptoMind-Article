"use strict";

// The local server answers /api/* and may expose the verified execution API.
// The GitHub Pages exporter replaces this file with a static-data configuration.
window.OPTOMIND_PORTAL_CONFIG = Object.freeze({
  mode: "auto",
  catalogUrl: "api/catalog",
  runUrlTemplate: "api/runs/{run_id}",
  artifactBase: "artifacts",
  localStatusUrl: "api/local/status",
});
