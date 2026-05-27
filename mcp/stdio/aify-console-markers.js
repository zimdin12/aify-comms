export const AIFY_COMMS_RECEIPT_TEXT = "aify-comms message received";

export function claudeAifyReceiptLine() {
  return AIFY_COMMS_RECEIPT_TEXT;
}

export function codexAifyReceiptFrame() {
  return `\r\n\x1b[2m[codex] ${AIFY_COMMS_RECEIPT_TEXT}\x1b[0m\r\n`;
}
