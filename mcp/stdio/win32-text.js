// win32-text.js — Windows codepage-safety helpers.
//
// Windows child processes that PRINT paths are a mojibake trap: powershell.exe
// (5.1) and where.exe write stdout in the console's OEM codepage, while Node
// decodes the pipe as UTF-8. Any non-ASCII character in a path — e.g. a user
// profile like C:\Users\KertMõttus — is either lossily transcoded by the OS
// (õ -> o, best-fit) or turns into mojibake (õ -> ├╡) on decode. Paths read
// that way may simply not exist on disk, and substring matches against
// process command lines silently miss.
//
// Two defenses, used across the bridge:
//   1. Don't shell out for path work at all when an in-process fs walk can
//      answer (see resolveExecutable in runtimes-exec.js).
//   2. When PowerShell output IS required (Win32_Process command lines),
//      force its stdout to UTF-8 first via PS_UTF8_PRELUDE so Node's utf-8
//      decode is byte-exact.

// Prepend to every `powershell.exe -Command` script whose OUTPUT is parsed.
// [Console]::OutputEncoding drives how PS encodes the stdout pipe; the setter
// can throw in exotic no-console hosts, so it is guarded — worst case the
// output stays OEM-encoded, which is no worse than before.
export const PS_UTF8_PRELUDE =
  "try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}; " +
  "$OutputEncoding = [System.Text.Encoding]::UTF8; ";
