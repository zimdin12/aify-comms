// The two comms tools that must run on the agent's own machine, served by the Claude channel (0.9.11 slice 3).
//
// Every other comms tool is the service's (`/mcp/http`), which cannot read or write a file on the agent's disk. So
// the channel, Claude's local comms process, offers exactly these: share a local file by path, and save a shared
// artifact to a local path. Distinct names from the service's `comms_share` and `comms_read`, which handle text.
//
// The upload is the platform's `FormData`, not a hand-built multipart body: the hand-built one in
// `artifact-tools.mjs` once put two framing bytes into every shared file (share-multipart-integrity.test.js).

import fs from "node:fs";
import path from "node:path";

import { ERROR_BODY_LIMIT } from "./aify-service-endpoint.mjs";
import { validateName } from "./safe-name.mjs";

export const SHARED_FILE_TOOLS = [
  {
    name: "comms_share_file",
    description: "Share a file from this machine (any type: images, binaries, logs) by its path. For text you already hold, use comms_share.",
    inputSchema: {
      type: "object",
      required: ["filePath"],
      properties: {
        filePath: { type: "string", description: "Absolute path of the file" },
        name: { type: "string", description: "Artifact name; defaults to the file's name" },
        description: { type: "string", description: "Short description" },
      },
    },
  },
  {
    name: "comms_save_file",
    description: "Save a shared artifact (any type) to a path on this machine, to open with your own tools. comms_read shows text inline.",
    inputSchema: {
      type: "object",
      required: ["name"],
      properties: {
        name: { type: "string", description: "Artifact name" },
        savePath: { type: "string", description: "Where to write it; defaults to a temp file" },
      },
    },
  },
];

/**
 * PURE. Is this read response a stored file's own bytes? The read route sends a file as an attachment
 * (`FileResponse(path, filename=name)`), typed from its name, so a shared `.json` file arrives as
 * `application/json` too. Only text shared inline comes back as the `{content, meta}` envelope, which has no
 * attachment header.
 */
export function isStoredFile(headers) {
  return Boolean(headers.get("content-disposition")) || !(headers.get("content-type") || "").includes("application/json");
}

const text = (message, isError = false) => ({ content: [{ type: "text", text: message }], ...(isError ? { isError: true } : {}) });

async function failure(res, what) {
  const body = await res.text().catch(() => "");
  let detail = body;
  try { const parsed = JSON.parse(body); detail = parsed.detail || parsed.error || detail; } catch { /* not JSON */ }
  return text(`${what} failed (HTTP ${res.status}): ${String(detail).slice(0, ERROR_BODY_LIMIT)}`, true);
}

/** Run one of SHARED_FILE_TOOLS as `agentId`, against the service at `serverUrl`. */
export async function callSharedFileTool(tool, args, { agentId, serverUrl, apiKey = "", fetchImpl = fetch, tmpDir = process.env.TEMP || process.env.TMP || "/tmp" }) {
  const headers = apiKey ? { "X-API-Key": apiKey } : {};
  if (!agentId) return text("This session is not registered yet, so a share would have no sender.", true);
  if (tool === "comms_share_file") {
    const filePath = String(args?.filePath || "");
    const name = String(args?.name || path.basename(filePath));
    try { validateName(name); } catch (error) { return text(error.message, true); }
    let data;
    try { data = fs.readFileSync(filePath); } catch (error) { return text(`Cannot read ${filePath}: ${error.code || error.message}`, true); }
    const form = new FormData();
    form.append("from_agent", agentId);
    form.append("name", name);
    form.append("description", String(args?.description || ""));
    form.append("file", new Blob([data]), name);
    // NEVER FOLLOWED: a redirect re-sends the headers, which carry the key.
    const res = await fetchImpl(`${serverUrl}/api/v1/shared`, { method: "POST", headers, body: form, redirect: "manual" });
    if (!res.ok) return failure(res, `Sharing "${name}"`);
    return text(`Shared "${name}" (${data.length} bytes) from ${filePath}.`);
  }
  if (tool === "comms_save_file") {
    const name = String(args?.name || "");
    try { validateName(name); } catch (error) { return text(error.message, true); }
    const res = await fetchImpl(`${serverUrl}/api/v1/shared/${encodeURIComponent(name)}`, { headers, redirect: "manual" });
    if (res.status === 404) return text(`Artifact "${name}" not found.`, true);
    if (!res.ok) return failure(res, `Reading "${name}"`);
    const data = isStoredFile(res.headers)
      ? Buffer.from(await res.arrayBuffer())
      : Buffer.from(String((await res.json()).content ?? ""), "utf8");
    const target = String(args?.savePath || path.join(tmpDir, `aify-shared-${name}`));
    try { fs.writeFileSync(target, data); } catch (error) { return text(`Cannot write ${target}: ${error.code || error.message}`, true); }
    return text(`Saved "${name}" (${data.length} bytes) to ${target.replace(/\\/g, "/")}.`);
  }
  return text(`Unknown tool: ${tool}`, true);
}
