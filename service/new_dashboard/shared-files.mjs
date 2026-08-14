// Shared files: the upload/list/delete surface, plus the two paths that attach a file to a chat message.
//
// Extracted from app.js in v0.5.4. `apiBase` is imported as a LIVE BINDING from api-client.mjs rather
// than recomputed here — these are the functions that build a URL directly (a download href, a multipart
// POST) instead of going through `api()`, and recomputing the base at module load would read
// `location`/`localStorage` and make this module unimportable in Node.
//
// The multipart calls pass `headers: {}` DELIBERATELY. `api()` defaults to application/json and spreads
// the caller's options after it, so an empty headers object clears that default and leaves fetch free to
// set `multipart/form-data` with its own boundary. See api-client.test.mjs, which pins that as an
// invariant rather than an accident.

import { api, apiBase } from './api-client.mjs';
import { persistChatDrafts } from './chat-prefs.mjs';
import { state } from './state.mjs';
import { byId, toast, uiConfirm } from './ui.js';
import { esc, fileSizeLabel, relTime } from './util.js';
import { filtered } from './work-loop-panels.mjs';

export async function loadFiles() {
  try { const res = await api('/shared'); state.files = res.files || res || []; } catch (_) { /* keep prior */ }
}
export function renderFiles() {
  const host = byId('files-list');
  if (!host) return;
  const files = filtered(state.files, ['name', 'from', 'description']);
  host.innerHTML = files.length ? files.map((f) => `
    <article class="file-row" data-kind="file" data-id="${esc(f.name)}">
      <div class="file-main">
        <strong class="clip">${esc(f.name)}</strong>
        <p class="preview">${esc(f.description || '')}</p>
        <small>${esc(f.from || 'unknown')} · ${esc(fileSizeLabel(f.size))}${f.sharedAt ? ' · ' + esc(relTime(f.sharedAt)) + ' ago' : ''}</small>
      </div>
      <div class="file-actions">
        <a class="ghost" href="${apiBase}/shared/${encodeURIComponent(f.name)}" target="_blank" rel="noreferrer">Download</a>
        <button class="ghost danger" data-file-delete="${esc(f.name)}">Delete</button>
      </div>
    </article>`).join('') : '<div class="empty-state"><span class="empty-icon">📂</span><strong>No shared files</strong><p>Upload an artifact above, or share one from an agent with comms_share.</p></div>';
}
export async function uploadSharedFile() {
  const input = byId('files-upload-input');
  const file = input?.files?.[0];
  if (!file) { toast('Choose a file to upload', 'warn'); return; }
  // Pre-check the configured size cap so we don't push a huge file just to get a 413.
  const maxMb = Number(state.settings?.max_shared_size_mb || 0);
  if (maxMb && file.size > maxMb * 1024 * 1024) {
    toast(`File is ${Math.round(file.size / (1024 * 1024))} MB — over the ${maxMb} MB limit (Settings → Max shared file size).`, 'error');
    return;
  }
  const name = (byId('files-upload-name')?.value || '').trim() || file.name;
  const description = (byId('files-upload-desc')?.value || '').trim();
  const form = new FormData();
  form.append('from_agent', 'dashboard');
  form.append('name', name);
  form.append('description', description);
  form.append('file', file, name);
  await api('/shared', { method: 'POST', body: form, headers: {} });
  if (input) input.value = '';
  if (byId('files-upload-name')) byId('files-upload-name').value = '';
  if (byId('files-upload-desc')) byId('files-upload-desc').value = '';
  await loadFiles();
  renderFiles();
  toast(`Uploaded ${name}`, 'ok');
}
export async function attachChatFile(file) {
  if (!file) return;
  const name = file.name;
  const form = new FormData();
  form.append('from_agent', state.chat.identity || 'dashboard');
  form.append('name', name);
  form.append('description', `Shared from chat by ${state.chat.identity || 'dashboard'}`);
  form.append('file', file, name);
  try {
    await api('/shared', { method: 'POST', body: form, headers: {} });
    const bodyEl = byId('chat-composer-body');
    if (bodyEl) {
      const ref = `[shared:${name}]`;
      bodyEl.value = bodyEl.value ? `${bodyEl.value} ${ref}` : ref;
      bodyEl.focus();
      const key = state.chat.selected;
      if (key) { state.chat.drafts = state.chat.drafts || {}; state.chat.drafts[key] = bodyEl.value; persistChatDrafts(); }
    }
    await loadFiles();
    toast(`Attached ${name}`, 'ok');
  } catch (err) { toast(`Attach failed: ${err?.message || err}`, 'error'); }
}
export async function deleteSharedFile(name) {
  if (!(await uiConfirm(`Delete shared file "${name}"? This removes it for everyone.`, { tone: 'danger', confirmLabel: 'Delete' }))) return;
  await api(`/shared/${encodeURIComponent(name)}`, { method: 'DELETE' });
  await loadFiles();
  renderFiles();
  toast(`Deleted ${name}`, 'ok');
}
export function pastedImageName(blob) {
  const ext = String(blob?.type || 'image/png').split('/')[1]?.replace(/[^a-z0-9]/gi, '') || 'png';
  return `img-${Date.now()}.${ext}`;
}
export async function uploadPastedImage(blob, targetEl) {
  if (!blob || !targetEl) return;
  const name = pastedImageName(blob);
  const form = new FormData();
  form.append('from_agent', 'dashboard');
  form.append('name', name);
  form.append('description', 'Pasted image from Dashboard Next');
  form.append('file', blob, name);
  const response = await fetch(`${apiBase}/shared`, { method: 'POST', body: form });
  const result = await response.json().catch(() => ({}));
  if (!response.ok || result.ok === false) throw new Error(result.detail || result.error || 'Image upload failed');
  const link = `${apiBase}/shared/${encodeURIComponent(name)}`;
  const ref = `[image: ${name}] ${link}`;
  const current = targetEl.value || '';
  targetEl.value = current ? `${current}${current.endsWith('\n') ? '' : '\n'}${ref}` : ref;
  targetEl.dispatchEvent(new Event('input', { bubbles: true }));
  targetEl.focus();
}
