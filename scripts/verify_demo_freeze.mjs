#!/usr/bin/env node

/** Verify the immutable Phase 0 demo evidence and source surfaces.
 *
 * The screenshots are the visual reference. The source hashes make that reference a
 * CI gate without relying on pixel rendering differences between operating systems:
 * an edit to any existing demo page, component, proxy, or shared stylesheet fails
 * until a reviewer deliberately re-baselines the manifest and screenshots together.
 */

import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const manifestPath = path.join(root, "Docs", "demo-freeze-manifest.json");

const sourcePaths = [
  "frontend/app/api/action/route.ts",
  "frontend/app/api/auth/route.ts",
  "frontend/app/api/download/[...path]/route.ts",
  "frontend/app/api/proxy/[...path]/route.ts",
  "frontend/app/api/upload/route.ts",
  "frontend/app/audit/page.tsx",
  "frontend/app/globals.css",
  "frontend/app/guide/page.tsx",
  "frontend/app/invoices/[id]/page.tsx",
  "frontend/app/layout.tsx",
  "frontend/app/loading.tsx",
  "frontend/app/login/page.tsx",
  "frontend/app/page.tsx",
  "frontend/app/promises/page.tsx",
  "frontend/app/recovered/page.tsx",
  "frontend/app/settings/page.tsx",
  "frontend/components/AutomationHealth.tsx",
  "frontend/components/Conversation.tsx",
  "frontend/components/DemoSettings.tsx",
  "frontend/components/DisputeCard.tsx",
  "frontend/components/Exceptions.tsx",
  "frontend/components/ExportMenu.tsx",
  "frontend/components/ImportLedger.tsx",
  "frontend/components/Landing.tsx",
  "frontend/components/Nav.tsx",
  "frontend/components/Overview.tsx",
  "frontend/components/PolicyCard.tsx",
  "frontend/components/ProvisionButton.tsx",
  "frontend/components/RecordPayment.tsx",
  "frontend/components/RunCycleButton.tsx",
  "frontend/components/RuntimeBanner.tsx",
  "frontend/components/SignOutButton.tsx",
  "frontend/components/SimulateReply.tsx",
  "frontend/components/ThemeToggle.tsx",
  "frontend/components/WhyCard.tsx",
  "frontend/components/badges.tsx",
  "frontend/lib/api.ts",
  "frontend/lib/money.ts",
  "frontend/lib/rate-limit.ts",
  "frontend/lib/session.ts",
  "frontend/proxy.ts",
];

const screenshotRoutes = [
  ["/", "Docs/assets/demo-baseline/01-landing.jpg"],
  ["/guide", "Docs/assets/demo-baseline/02-guide.jpg"],
  ["/login", "Docs/assets/demo-baseline/03-login.jpg"],
  ["/ (authenticated)", "Docs/assets/demo-baseline/04-dashboard.jpg"],
  ["/recovered", "Docs/assets/demo-baseline/05-recovered.jpg"],
  ["/promises", "Docs/assets/demo-baseline/06-promises.jpg"],
  ["/audit", "Docs/assets/demo-baseline/07-audit.jpg"],
  ["/settings", "Docs/assets/demo-baseline/08-settings.jpg"],
  ["/invoices/:id", "Docs/assets/demo-baseline/09-invoice-detail.jpg"],
];

const goldenPaths = [
  "backend/tests/golden/demo/api_responses.json",
  "backend/tests/golden/demo/ledger.json",
  "backend/tests/golden/demo/policy_traces.json",
];

function sha256(content) {
  return createHash("sha256").update(content).digest("hex");
}

async function fileRecord(relativePath) {
  const content = await readFile(path.join(root, relativePath));
  return { path: relativePath, sha256: sha256(content) };
}

function imageDimensions(content, relativePath) {
  if (content.toString("ascii", 1, 4) === "PNG") {
    return { format: "png", width: content.readUInt32BE(16), height: content.readUInt32BE(20) };
  }
  if (content[0] === 0xff && content[1] === 0xd8) {
    let offset = 2;
    while (offset + 9 < content.length) {
      if (content[offset] !== 0xff) {
        offset += 1;
        continue;
      }
      const marker = content[offset + 1];
      offset += 2;
      if (marker === 0xd8 || marker === 0xd9) continue;
      const segmentLength = content.readUInt16BE(offset);
      if (marker >= 0xc0 && marker <= 0xc3) {
        return {
          format: "jpeg",
          height: content.readUInt16BE(offset + 3),
          width: content.readUInt16BE(offset + 5),
        };
      }
      offset += segmentLength;
    }
  }
  throw new Error(`${relativePath} is not a supported PNG or JPEG image`);
}

async function screenshotRecord([route, relativePath]) {
  const content = await readFile(path.join(root, relativePath));
  const dimensions = imageDimensions(content, relativePath);
  return {
    route,
    path: relativePath,
    sha256: sha256(content),
    ...dimensions,
  };
}

async function currentManifest() {
  return {
    schema_version: 1,
    frozen_at_tag: "demo-freeze-2026-08-30",
    rebaselined_at: "2026-08-31",
    backend_goldens: await Promise.all(goldenPaths.map(fileRecord)),
    screenshots: await Promise.all(screenshotRoutes.map(screenshotRecord)),
    frozen_frontend_sources: await Promise.all(sourcePaths.map(fileRecord)),
  };
}

const current = await currentManifest();
const rendered = `${JSON.stringify(current, null, 2)}\n`;

if (process.argv.includes("--update")) {
  await writeFile(manifestPath, rendered, "utf8");
  console.log(`Updated ${path.relative(root, manifestPath)}`);
  process.exit(0);
}

const expected = await readFile(manifestPath, "utf8").catch(() => "");
if (expected !== rendered) {
  console.error(
    "Frozen demo evidence or source changed. Revert the demo change, or deliberately " +
      "recapture all affected screenshots/goldens and run " +
      "`node scripts/verify_demo_freeze.mjs --update` for reviewed re-baselining.",
  );
  process.exit(1);
}

console.log(
  `Demo freeze verified: ${current.backend_goldens.length} backend goldens, ` +
    `${current.screenshots.length} screens, ${current.frozen_frontend_sources.length} source files.`,
);
