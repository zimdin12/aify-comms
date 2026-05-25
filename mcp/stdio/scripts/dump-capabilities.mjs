// Prints all JS adapter capability values as JSON to stdout. Used by
// service/tests/test_runtime_adapter_consistency.py to verify JS and
// Python adapters agree.
//
// Output shape:
//   {
//     "claude-code": {
//       "supportsResident": true,
//       "supportsManaged": true,
//       "supportsSteering": true,
//       "supportsInterrupt": true,
//       "supportsMultiClient": true,
//       "preferredDeliveryMode": "managed-via-wrapper"
//     },
//     ...
//   }

import { adapterFor, supportedRuntimes } from "../adapters/index.js";

const out = {};
for (const name of supportedRuntimes()) {
  const a = adapterFor(name);
  out[name] = {
    supportsResident: a.supportsResident,
    supportsManaged: a.supportsManaged,
    supportsSteering: a.supportsSteering,
    supportsInterrupt: a.supportsInterrupt,
    supportsMultiClient: a.supportsMultiClient,
    preferredDeliveryMode: a.preferredDeliveryMode,
  };
}

process.stdout.write(JSON.stringify(out, null, 2));
