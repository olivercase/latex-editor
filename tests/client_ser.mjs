// Runs the page's own serialiser (copied out of index.html) on a minimal
// fake DOM, so the tests exercise exactly what the browser sends.
//   node client_ser.mjs < cases.json > results.json
// Each case: {tokens, kind, ops: [...]} -> {latex, shape} or {error}.
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {dirname, join} from 'node:path';

const html = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'index.html'), 'utf8');
const grab = (from, to) => {
  const a = html.indexOf(from), b = html.indexOf(to, a);
  if (a < 0 || b < 0) throw new Error('could not find ' + from + ' in index.html');
  return html.slice(a, b);
};
const code = grab('const ESC = {', '// ---------------------------------------------------------------- words');
const {serShape} = new Function(code + '\nreturn {serShape};')();

// ---- fake DOM: just what serShape reads
class Text { constructor(d) { this.nodeType = 3; this.data = d; } get textContent() { return this.data; } }
class El {
  constructor(tag, cls = '') { this.nodeType = 1; this.tagName = tag; this.cls = new Set(cls.split(' ').filter(Boolean)); this.dataset = {}; this.childNodes = []; }
  get classList() { return {contains: c => this.cls.has(c)}; }
  get textContent() { return this.childNodes.map(c => c.textContent).join(''); }
  append(n) { this.childNodes.push(n); return n; }
}
function render(tokens, parent) {
  for (const t of tokens) {
    if (t[0] === 't') parent.append(new Text(t[1]));
    else if (t[0] === 'a') { const s = new El('SPAN', 'atom ' + t[3]); s.dataset.raw = t[1]; parent.append(s); }
    else { const s = new El('SPAN', 'wrap'); s.dataset.pre = t[1]; s.dataset.suf = t[3]; render(t[2], s); parent.append(s); }
  }
}
// ops address top-level children of the region
function apply(root, op) {
  const kids = root.childNodes;
  if (op.op === 'insert') {          // text at position i among top-level children
    kids.splice(op.at, 0, new Text(op.text));
  } else if (op.op === 'delete') {
    kids.splice(op.at, 1);
  } else if (op.op === 'clear') {   // select all, delete
    root.childNodes = [];
  } else if (op.op === 'empty') {    // remove everything inside a wrapper
    kids[op.at].childNodes = [];
  } else if (op.op === 'into') {     // type text inside a wrapper, at its end
    kids[op.at].append(new Text(op.text));
  } else if (op.op === 'parbreak') {
    const s = new El('SPAN', 'atom parbreak'); s.dataset.raw = op.raw; kids.splice(op.at, 0, s);
  } else if (op.op === 'unwrap') {   // "Clear mark": the wrapper goes, its contents stay
    kids.splice(op.at, 1, ...kids[op.at].childNodes);
  } else if (op.op === 'bold') {     // Cmd-B over new text
    const b = new El('B'); b.append(new Text(op.text)); kids.splice(op.at, 0, b);
  } else throw new Error('unknown op ' + op.op);
}

const cases = JSON.parse(readFileSync(0, 'utf8'));
const out = cases.map(c => {
  try {
    const root = new El('DIV');
    render(c.tokens, root);
    for (const op of c.ops) apply(root, op);
    return serShape(root, c.kind);
  } catch (e) { return {error: String(e)}; }
});
process.stdout.write(JSON.stringify(out));
