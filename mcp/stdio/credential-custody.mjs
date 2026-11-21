// Compatibility for the wrapper's still-mandatory pure custody comparison. Runtime
// reads use the public credentials leaf. Remove this adapter with that comparison.
const envPackage = import.meta.resolve("aify-env/package.json");
const { fileSecurityProblem } = await import(new URL("./lib/credential-inspection.mjs", envPackage));
export const custodyProblem = fileSecurityProblem;
