# Messenger formatting dependencies

These files run locally. The dashboard does not load a CDN at runtime.

| Local file | Source | SHA-256 | License |
|---|---|---|---|
| purify.mjs | https://cdn.jsdelivr.net/npm/dompurify@3.4.15/dist/purify.es.min.mjs | 1ef8c0415fbf7b2600e1cbe8c8840f5ab9ebcbaf849405c66649b1ef8def7797 | MPL-2.0 OR Apache-2.0 |
| marked.mjs | https://registry.npmjs.org/marked/-/marked-18.0.12.tgz, package/lib/marked.esm.js | 4f7117dd998780fe6fa17a25d06489b8ddaabda73d3a5a9451cb400f1925c56d | MIT |

DOMPurify uses jsDelivr's minified ESM distribution. Its bytes were fetched twice and matched. The previous locally minified copy had no retained build recipe and was replaced, not treated as an upstream byte match. Marked matches its npm tarball byte-for-byte. Both retained license files match their npm tarballs. Tarball SHA-1 values match registry metadata. No cryptographic publisher-signature claim is made.

`message-format.mjs` permits formatting tags and only href/title/open attributes. It disables data-* and ARIA attributes to prevent delegated dashboard action injection. Links permit HTTP, HTTPS and mailto only. Images, style, scripts, SVG, MathML, iframes, forms and template content are excluded. Parsing failures, missing browser sanitizer support, and bodies over 200,000 characters fall back to escaped text. Raw message bodies are never rewritten.
