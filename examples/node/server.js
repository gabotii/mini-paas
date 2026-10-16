const express = require("express");

const app = express();
const port = Number(process.env.PORT || 3000);
const started = Date.now();

app.get("/", (_req, res) => {
  res.send(`Hello from Node (${process.version}) on Mini PaaS — deployment #${process.env.MINIPAAS_DEPLOYMENT ?? "?"}`);
});

app.get("/health", (_req, res) => {
  res.json({ status: "ok", uptimeSeconds: Math.round((Date.now() - started) / 1000) });
});

const server = app.listen(port, "0.0.0.0", () => {
  console.log(`Listening on port ${port}`);
});

process.on("SIGTERM", () => {
  console.log("SIGTERM received, closing server");
  server.close(() => process.exit(0));
});
