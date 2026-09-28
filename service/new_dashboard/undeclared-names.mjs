// The names a module reads that nothing in the module declares: the ReferenceError `node --check` cannot see.
//
// A real parser, not a regex: the caller hands in acorn and acorn-walk. This module holds no parser of its
// own and imports nothing, so any test can import it; `undeclared-names.test.mjs` takes both from the copy
// Node itself bundles, which is the only JS parser on this host (a parser dependency is the operator's call).
//
// THE SLICE IT DECIDES, stated so a green run is not read as more. A name is DECLARED when anything in the
// module binds it -- an import, a var/let/const, a function or class name, a parameter, a catch binding --
// in ANY scope. A read of a name bound nowhere in the file is reported unless the caller lists it as a
// global. So it finds the extraction defect (a helper that stayed in app.js, called from the module it was
// moved out of) and a dropped import, and it cannot find a name used outside the one scope that binds it.

/** Every identifier a binding pattern introduces: `a`, `{ a, b: [c] }`, `[d = 1, ...e]`. */
export function patternNames(pattern) {
  if (!pattern) return [];
  switch (pattern.type) {
    case 'Identifier': return [pattern.name];
    case 'ObjectPattern': return pattern.properties.flatMap((p) => patternNames(p.type === 'RestElement' ? p.argument : p.value));
    case 'ArrayPattern': return pattern.elements.flatMap((el) => patternNames(el));
    case 'AssignmentPattern': return patternNames(pattern.left);
    case 'RestElement': return patternNames(pattern.argument);
    default: return [];  // a MemberExpression target (`obj.x = ...`) binds nothing
  }
}

const PATTERN_WRAPPERS = new Set(['ArrayPattern', 'ObjectPattern', 'Property', 'AssignmentPattern', 'RestElement']);

/** True when a pattern identifier WRITES an existing name (`x = 1`, `[a] = y`, `for (k in o)`) rather than binding one. */
function isAssignmentTarget(ancestors) {
  let i = ancestors.length - 2;
  while (i >= 0 && PATTERN_WRAPPERS.has(ancestors[i].type)) i -= 1;
  const owner = ancestors[i];
  if (!owner) return false;
  if (owner.type === 'AssignmentExpression') return true;
  return (owner.type === 'ForInStatement' || owner.type === 'ForOfStatement') && owner.left.type !== 'VariableDeclaration';
}

/**
 * The undeclared names `source` reads, as [{ name, line }], in source order.
 *
 * `known` is the set of globals the module may read without declaring. `parse` and `walk` are acorn's
 * `parse` and acorn-walk; the source is parsed as an ES module, which every dashboard file is.
 */
export function undeclaredNames(source, { parse, walk, known }) {
  const ast = parse(source, { ecmaVersion: 'latest', sourceType: 'module', locations: true });
  const declared = new Set(['arguments']);
  const declare = (names) => { for (const name of names) declared.add(name); };
  walk.simple(ast, {
    ImportSpecifier: (node) => declared.add(node.local.name),
    ImportDefaultSpecifier: (node) => declared.add(node.local.name),
    ImportNamespaceSpecifier: (node) => declared.add(node.local.name),
    VariableDeclarator: (node) => declare(patternNames(node.id)),
    Function: (node) => { if (node.id) declared.add(node.id.name); declare(node.params.flatMap(patternNames)); },
    Class: (node) => { if (node.id) declared.add(node.id.name); },
    CatchClause: (node) => declare(patternNames(node.param)),
  });
  const reads = [];
  // acorn-walk hands an identifier in EXPRESSION position to `Identifier`, and one in a binding or
  // assignment-target position to `VariablePattern`. Property keys and `a.b`'s `b` are never visited.
  walk.ancestor(ast, {
    Identifier: (node) => reads.push(node),
    VariablePattern: (node, _state, ancestors) => { if (isAssignmentTarget(ancestors)) reads.push(node); },
  });
  return reads
    .filter((node) => !declared.has(node.name) && !known.has(node.name))
    .map((node) => ({ name: node.name, line: node.loc.start.line }));
}
