#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

echo "[event-contract] validating schema v1 contract parser"

node <<'NODE'
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

const filePath = path.join(process.cwd(), "src/lib/orchestration/event-contract.ts");
const source = fs.readFileSync(filePath, "utf-8");
const transpiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2020,
  },
}).outputText;

const moduleObj = { exports: {} };
const context = {
  module: moduleObj,
  exports: moduleObj.exports,
  require,
  process,
  console,
  URL,
  Date,
  Set,
};
vm.runInNewContext(transpiled, context, { filename: filePath });

const { parseOrchestrationEventContract, ORCHESTRATION_EVENT_SCHEMA_VERSION } = moduleObj.exports;

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

const previousStrict = process.env.ORCH_REQUIRE_SCHEMA_V1;

process.env.ORCH_REQUIRE_SCHEMA_V1 = "true";
const strictLegacy = parseOrchestrationEventContract({
  agentId: "hermes",
  topicKey: "legacy",
  title: "legacy",
  summary: "legacy payload",
  priority: "normal",
  confidence: 0.6,
});
assert(!strictLegacy.ok, "strict mode should reject legacy payload");
assert(strictLegacy.error === "schema_version_required", "strict mode should require schemaVersion");

const invalidV1 = parseOrchestrationEventContract({
  schemaVersion: ORCHESTRATION_EVENT_SCHEMA_VERSION,
  eventType: "hermes.briefing.created",
  producer: "verify-script",
  occurredAt: new Date().toISOString(),
  payload: {
    agentId: "hermes",
    topicKey: "invalid-v1",
    title: "invalid event",
    priority: "high",
    confidence: 0.82,
  },
});
assert(!invalidV1.ok, "invalid v1 payload must be rejected");
assert(invalidV1.error === "invalid_event_contract", "invalid v1 payload should return invalid_event_contract");
assert(
  Array.isArray(invalidV1.errors) && invalidV1.errors.some((item) => item.path === "$.payload.summary"),
  "invalid v1 payload should include summary validation error"
);

const validV1 = parseOrchestrationEventContract({
  schemaVersion: ORCHESTRATION_EVENT_SCHEMA_VERSION,
  eventType: "hermes.briefing.created",
  producer: "verify-script",
  occurredAt: new Date().toISOString(),
  traceId: "trace-check-1",
  payload: {
    agentId: "hermes",
    topicKey: "valid-v1",
    title: "valid event",
    summary: "valid summary",
    priority: "high",
    confidence: 0.91,
    sourceRefs: [{ title: "sample", url: "https://example.com/sample" }],
  },
});
assert(validV1.ok, "valid v1 envelope must pass");
assert(validV1.contract.schemaVersion === ORCHESTRATION_EVENT_SCHEMA_VERSION, "schemaVersion should match");
assert(validV1.contract.legacy === false, "v1 envelope should not be legacy");

process.env.ORCH_REQUIRE_SCHEMA_V1 = "false";
const legacyAllowed = parseOrchestrationEventContract({
  agentId: "hermes",
  topicKey: "legacy-allowed",
  title: "legacy allowed",
  summary: "legacy payload allowed",
  priority: "normal",
  confidence: 0.64,
});
assert(legacyAllowed.ok, "legacy payload should pass when strict mode disabled");
assert(legacyAllowed.contract.legacy === true, "legacy payload should be marked as legacy");

if (previousStrict === undefined) {
  delete process.env.ORCH_REQUIRE_SCHEMA_V1;
} else {
  process.env.ORCH_REQUIRE_SCHEMA_V1 = previousStrict;
}

console.log("[event-contract] PASS");
NODE

