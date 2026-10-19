// Run the Khronos glTF validator over every .glb of an export.
//
//   npm install --prefix <dir> gltf-validator      (once; any folder outside the export)
//   node tools/khronos_validate.js <dir> export/ report.json
//
// Prints per-category error/warning/info/hint counts and the issue codes;
// report.json also keeps up to three examples per code. Expected notes:
// UNUSED_OBJECT (bare armatures of animation-only files), NODE_EMPTY
// (reference/group nodes), INCOMPLETE_EXTENSION_SUPPORT (KHR_animation_pointer).
const fs = require('fs');
const path = require('path');
const validator = require(path.join(process.argv[2], 'node_modules', 'gltf-validator'));
const root = process.argv[3];

function walk(dir, out) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, out);
    else if (e.name.toLowerCase().endsWith('.glb')) out.push(p);
  }
  return out;
}

(async () => {
  const files = walk(root, []);
  const summary = { files: files.length, bySeverity: {}, codes: {}, examples: {}, perCategory: {} };
  const sev = ['error', 'warning', 'info', 'hint'];
  for (const f of files) {
    const rel = path.relative(root, f);
    const cat = rel.split(path.sep)[0];
    const pc = summary.perCategory[cat] = summary.perCategory[cat] || { files: 0, errors: 0, warnings: 0, infos: 0, hints: 0 };
    pc.files++;
    let report;
    try {
      report = await validator.validateBytes(new Uint8Array(fs.readFileSync(f)), { uri: rel, maxIssues: 0 });
    } catch (e) {
      summary.codes['VALIDATOR_EXCEPTION'] = (summary.codes['VALIDATOR_EXCEPTION'] || 0) + 1;
      continue;
    }
    pc.errors += report.issues.numErrors; pc.warnings += report.issues.numWarnings;
    pc.infos += report.issues.numInfos; pc.hints += report.issues.numHints;
    for (const m of report.issues.messages) {
      const key = `${sev[m.severity]}:${m.code}`;
      summary.codes[key] = (summary.codes[key] || 0) + 1;
      const ex = summary.examples[key] = summary.examples[key] || [];
      if (ex.length < 3) ex.push({ file: rel, pointer: m.pointer, message: m.message });
    }
  }
  fs.writeFileSync(process.argv[4], JSON.stringify(summary, null, 1));
  console.log(JSON.stringify({ files: summary.files, perCategory: summary.perCategory, codes: summary.codes }, null, 1));
})();
