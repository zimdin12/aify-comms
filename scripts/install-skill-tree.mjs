// Standalone has no Env placement receipt. Create only absent skills; never replace a tree.
import fs from "node:fs";
import path from "node:path";

function stat(entry) {
  try { return fs.lstatSync(entry); }
  catch (error) { if (error.code === "ENOENT") return null; throw error; }
}

function directory(dir, create = false) {
  const parent = path.dirname(dir);
  if (parent !== dir) directory(parent, create);
  const existing = stat(dir);
  if (!existing && create) {
    try { fs.mkdirSync(dir); }
    catch (error) { if (error.code !== "EEXIST") throw error; }
  }
  if (!(existing ?? stat(dir))?.isDirectory()) throw new Error(`not a real directory: ${dir}`);
}

function snapshot(dir, prefix = "", tree = new Map()) {
  for (const name of fs.readdirSync(dir).sort()) {
    const entry = path.join(dir, name);
    const relative = path.join(prefix, name);
    const info = fs.lstatSync(entry);
    if (info.isDirectory()) {
      tree.set(relative, null);
      snapshot(entry, relative, tree);
    } else if (info.isFile() && info.nlink === 1) {
      tree.set(relative, fs.readFileSync(entry));
    } else {
      throw new Error(`not a regular, unlinked skill entry: ${entry}`);
    }
  }
  return tree;
}

function sameTree(left, right) {
  return left.size === right.size && [...left].every(([name, bytes]) => {
    const other = right.get(name);
    return bytes === null ? other === null : Buffer.isBuffer(other) && bytes.equals(other);
  });
}

function installAbsent(target, tree) {
  directory(path.dirname(target));
  fs.mkdirSync(target); // exclusive: a skill appearing since the check is not ours to edit
  for (const [relative, bytes] of tree) {
    const entry = path.join(target, relative);
    directory(path.dirname(entry));
    if (bytes === null) fs.mkdirSync(entry);
    else fs.writeFileSync(entry, bytes, { flag: "wx" });
  }
}

function installTree(source, destination) {
  directory(source);
  directory(destination, true);
  for (const name of fs.readdirSync(source).sort()) {
    const target = path.join(destination, name);
    try {
      const skill = path.join(source, name);
      directory(skill);
      const tree = snapshot(skill);
      if (!Buffer.isBuffer(tree.get("SKILL.md"))) throw new Error("source has no regular SKILL.md");
      const existing = stat(target);
      if (!existing) {
        installAbsent(target, tree);
        console.log(`  Installed skill: ${target}`);
      } else if (!existing.isDirectory() || !sameTree(tree, snapshot(target))) {
        console.warn(`  Left skill unchanged (different or unverified): ${target}`);
      }
    } catch (error) {
      console.warn(`  Skill not replaced: ${target} (${error.message})`);
    }
  }
}

const [, , source, destination] = process.argv;
try {
  if (!source || !destination) throw new Error("expected source and destination paths");
  installTree(path.resolve(source), path.resolve(destination));
} catch (error) {
  console.warn(`  Skills not changed: ${error.message}`);
}
