// Shared artifacts: publishing a file to the team, reading one back, and listing what exists.
//
// Three MCP tools — `comms_share`, `comms_read`, `comms_files`. v0.5.4 layer 2 of the server.js
// decomposition, the second tool group to move.
//
// IT HAS NO GROUP-PRIVATE HELPERS AND NEEDS NO SERVER.JS FUNCTION AT ALL, which is unusual and is why it
// went second. Everything it touches now has an owner: `SHARED_DIR` from `local-store.mjs`,
// `validateName` from `safe-name.mjs`, and the HTTP surface from `aify-service-endpoint.mjs`. Three
// commits ago none of those were true and this group would have looked like it depended on server.js.
//
// EACH TOOL HAS TWO IMPLEMENTATIONS AND THAT IS THE POINT OF THE GROUP. In remote mode an artifact is a
// multipart POST to `/api/v1/shared`; in local mode it is a file under `SHARED_DIR` with a `.meta.json`
// sidecar. The two paths must agree on what a caller sees, and until now neither was reachable from a
// test — server.js is the bin entry point and nothing imports it.
//
// THE `// 6. // 7. // 8.` BANNERS BELOW ARE THE ORIGINAL TEXT and their numbers refer to server.js's
// tool ordering, which no longer exists as one list. Kept rather than renumbered: they are how these
// blocks have always been navigated, and inventing new numbers here would make two files disagree.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.

import fs from "fs";
import path from "path";

import { API_KEY, IS_REMOTE, SERVER_URL, httpCall } from "./aify-service-endpoint.mjs";
import { SHARED_DIR } from "./local-store.mjs";
import { validateName } from "./safe-name.mjs";

// Registers the three shared-artifact tools on an MCP server.
//
// A function, not a module-scope side effect: registration at import time would fire on any import,
// including a test's, and a fake server is how these handlers become callable without an MCP transport.
// `z` is the caller's zod — server.js loads it below its `AIFY_BRIDGE_DISABLED` early-exit so an RPC
// child never pays for it, and a static import here would be hoisted above that guard.
//
// The three bodies below are the original server.js text, indented one level to sit inside this
// function. Nothing else about them changed.
export function registerArtifactTools(server, z) {
  server.tool(
    "comms_share",
    "Share an artifact (code, results, images, any file) with other agents. " +
      "Pass text content directly, or a file path for images/binaries.",
    {
      from: z.string().describe("Your agent ID"),
      name: z.string().describe("Artifact name (e.g. 'test-results.txt', 'screenshot.png')"),
      content: z.string().optional().describe("Text content (omit if using filePath)"),
      filePath: z.string().optional().describe("Absolute path to file to copy into shared space"),
      description: z.string().optional().describe("Short description"),
    },
    async ({ from, name, content, filePath, description }) => {
      try { validateName(name); } catch (e) { return { content: [{ type: "text", text: e.message }], isError: true }; }

      if (IS_REMOTE) {
        const headers = {};
        if (API_KEY) headers["X-API-Key"] = API_KEY;

        // Binary file upload (images, etc.)
        if (filePath && fs.existsSync(filePath)) {
          const fileData = fs.readFileSync(filePath);
          const boundary = `----aify${Date.now()}`;
          const parts = [];
          parts.push(`--${boundary}\r\nContent-Disposition: form-data; name="from_agent"\r\n\r\n${from}`);
          parts.push(`--${boundary}\r\nContent-Disposition: form-data; name="name"\r\n\r\n${name}`);
          parts.push(`--${boundary}\r\nContent-Disposition: form-data; name="description"\r\n\r\n${description || ""}`);
          if (content) {
            parts.push(`--${boundary}\r\nContent-Disposition: form-data; name="content"\r\n\r\n${content}`);
          }
          // NOTE THE MISSING TRAILING CRLF, and do not "tidy" it back in. The file part's header
          // block already ends with the blank line (`\r\n\r\n`) that terminates headers. Appending
          // another `\r\n` after the join put a THIRD CRLF between the headers and the payload, and
          // multipart treats everything after the FIRST blank line as body — so every binary upload
          // was stored with two extra leading bytes.
          //
          // Reported 2026-08-10 by graph-senior-dev-hermes with byte evidence: a 23,620-byte .log
          // arrived as 23,622 bytes, `stored[2:] == original`, and recipient hash verification
          // therefore failed for every shared file. Reproduced exactly from this code before
          // changing it: payload began `0d0a`, and dropping the byte after it recovered the original.
          //
          // The server was never at fault — it does `file_path.write_bytes(data)` and stores
          // faithfully whatever the multipart parser hands it. The corruption was manufactured here,
          // in the request, which is why it looked like a storage bug from the outside.
          parts.push(`--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="${name}"\r\nContent-Type: application/octet-stream\r\n\r\n`);
          const bodyParts = [Buffer.from(parts.join("\r\n")), fileData, Buffer.from(`\r\n--${boundary}--\r\n`)];
          headers["Content-Type"] = `multipart/form-data; boundary=${boundary}`;
          const res = await fetch(`${SERVER_URL}/api/v1/shared`, { method: "POST", headers, body: Buffer.concat(bodyParts) });
          const r = await res.json().catch(() => ({}));
          // A rejected upload (413/422/500) still returns a JSON body, so res.json()
          // doesn't throw — report the FAILURE instead of a false success that leaves
          // a downstream comms_read seeing "not found" (bughunt 2026-07-03).
          if (!res.ok) return { content: [{ type: "text", text: `Share failed (HTTP ${res.status}): ${r.detail || r.error || "server rejected the upload"}` }], isError: true };
          return { content: [{ type: "text", text: `Shared "${name}" (${fileData.length} bytes, binary) on server.` }] };
        }

        // Text content
        if (!content && !filePath) return { content: [{ type: "text", text: "Need content or filePath." }], isError: true };
        let body = content;
        if (filePath && !content) { try { body = fs.readFileSync(filePath, "utf-8"); } catch { return { content: [{ type: "text", text: `Cannot read file: ${filePath}` }], isError: true }; } }
        const formData = new URLSearchParams({ from_agent: from, name, description: description || "", content: body });
        const res = await fetch(`${SERVER_URL}/api/v1/shared`, { method: "POST", headers, body: formData });
        const r = await res.json().catch(() => ({}));
        if (!res.ok) return { content: [{ type: "text", text: `Share failed (HTTP ${res.status}): ${r.detail || r.error || "server rejected the upload"}` }], isError: true };
        return { content: [{ type: "text", text: `Shared "${r.name || name}" on server.` }] };
      }

      const destPath = path.join(SHARED_DIR, name);
      try {
        if (filePath) {
          fs.copyFileSync(filePath, destPath);
        } else if (content) {
          fs.writeFileSync(destPath, content);
        } else {
          return { content: [{ type: "text", text: "Need either content or filePath." }], isError: true };
        }

        const stat = fs.statSync(destPath);
        fs.writeFileSync(
          destPath + ".meta.json",
          JSON.stringify({
            from, name, description: description || "",
            sharedAt: new Date().toISOString(), size: stat.size,
            source: filePath ? "file" : "text",
          }, null, 2)
        );
        return {
          content: [{ type: "text", text: `Shared "${name}" (${stat.size} bytes). Path: ${destPath.replace(/\\/g, "/")}` }],
        };
      } catch (err) {
        return { content: [{ type: "text", text: `Error: ${err.message}` }], isError: true };
      }
    }
  );

  // ═══════════════════════════════════════════════════════════════════════════════
  // 7. comms_read -- Read a shared artifact
  // ═══════════════════════════════════════════════════════════════════════════════

  server.tool(
    "comms_read",
    "Read a shared artifact by name.",
    {
      name: z.string().describe("Artifact name to read"),
    },
    async ({ name }) => {
      try { validateName(name); } catch (e) { return { content: [{ type: "text", text: e.message }], isError: true }; }

      if (IS_REMOTE) {
        const url = `${SERVER_URL}/api/v1/shared/${encodeURIComponent(name)}`;
        const options = { headers: {} };
        if (API_KEY) options.headers["X-API-Key"] = API_KEY;
        const res = await fetch(url, options);
        if (!res.ok) {
          return { content: [{ type: "text", text: `Artifact "${name}" not found.` }], isError: true };
        }
        const contentType = res.headers.get("content-type") || "";
        // Binary file — save locally and return path
        if (!contentType.includes("application/json")) {
          const tmpDir = process.env.TEMP || process.env.TMP || "/tmp";
          const localPath = path.join(tmpDir, `aify-shared-${name}`);
          const buffer = Buffer.from(await res.arrayBuffer());
          fs.writeFileSync(localPath, buffer);
          return { content: [{ type: "text", text:
            `Binary artifact "${name}" (${buffer.length} bytes)\n` +
            `Saved to: ${localPath.replace(/\\/g, "/")}\n` +
            `(Use the Read tool on the path to view images)` }] };
        }
        // Text content — return inline
        const r = await res.json();
        if (r.content) {
          const meta = r.meta || {};
          const header = meta.from
            ? `From: ${meta.from} | ${meta.sharedAt || ""}${meta.description ? ` | ${meta.description}` : ""}\n\n`
            : "";
          return { content: [{ type: "text", text: header + r.content }] };
        }
        return { content: [{ type: "text", text: `"${name}" — empty or unreadable.` }] };
      }

      const artifactPath = path.join(SHARED_DIR, name);
      try {
        let meta = {};
        try { meta = JSON.parse(fs.readFileSync(artifactPath + ".meta.json", "utf-8")); } catch { /* no meta */ }

        const stat = fs.statSync(artifactPath);
        const ext = path.extname(name).toLowerCase();
        const binaryExts = [".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".pdf", ".zip", ".tar", ".gz"];

        if (binaryExts.includes(ext)) {
          return {
            content: [{
              type: "text",
              text: `Binary artifact "${name}" (${stat.size} bytes)\n` +
                `From: ${meta.from || "?"} | ${meta.description || ""}\n` +
                `Path: ${artifactPath.replace(/\\/g, "/")}\n` +
                `(Use Read tool on the path to view images)`,
            }],
          };
        }

        const fileContent = fs.readFileSync(artifactPath, "utf-8");
        const header = meta.from
          ? `From: ${meta.from} | ${meta.sharedAt || ""}${meta.description ? ` | ${meta.description}` : ""}\n\n`
          : "";
        return { content: [{ type: "text", text: header + fileContent }] };
      } catch {
        return { content: [{ type: "text", text: `"${name}" not found.` }], isError: true };
      }
    }
  );

  // ═══════════════════════════════════════════════════════════════════════════════
  // 8. comms_files -- List shared artifacts
  // ═══════════════════════════════════════════════════════════════════════════════

  server.tool(
    "comms_files",
    "List all shared artifacts.",
    {},
    async () => {
      if (IS_REMOTE) {
        const r = await httpCall("GET", "/shared");
        if (!r.files.length) return { content: [{ type: "text", text: "No shared artifacts." }] };
        const lines = r.files.map((f) =>
          `- ${f.name} (${f.size}B, from: ${f.from}, ${f.sharedAt})${f.description ? ` -- ${f.description}` : ""}`
        );
        return { content: [{ type: "text", text: lines.join("\n") }] };
      }

      try {
        const files = fs.readdirSync(SHARED_DIR).filter((f) => !f.endsWith(".meta.json"));
        if (!files.length) return { content: [{ type: "text", text: "No shared artifacts." }] };
        const lines = files.map((f) => {
          try {
            const meta = JSON.parse(fs.readFileSync(path.join(SHARED_DIR, f + ".meta.json"), "utf-8"));
            return `- ${f} (${meta.size}B, from: ${meta.from}, ${meta.sharedAt})${meta.description ? ` -- ${meta.description}` : ""}`;
          } catch {
            const stat = fs.statSync(path.join(SHARED_DIR, f));
            return `- ${f} (${stat.size}B)`;
          }
        });
        return { content: [{ type: "text", text: lines.join("\n") }] };
      } catch {
        return { content: [{ type: "text", text: "No shared artifacts." }] };
      }
    }
  );
}
