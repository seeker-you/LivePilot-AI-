/* Node runner for DouyinBarrageGrab-style X-Bogus signing.
 * Usage: node sign_runner.js '{"X-MS-STUB": "<md5>"}'
 * Concatenates sign.js (which defines getSign) with a small CLI tail and
 * evaluates the result as a CommonJS module so require("jsdom") resolves.
 */
const fs = require("fs");
const path = require("path");

const src = fs.readFileSync(path.join(__dirname, "sign.js"), "utf8");
// Unique temp name so concurrent sign calls (multiple sessions) don't race.
const out = path.join(__dirname, `_sign_run_${process.pid}_${Date.now()}.js`);
fs.writeFileSync(
    out,
    src +
        "\ntry { process.stdout.write(JSON.stringify(getSign(JSON.parse(process.argv[2])))); }" +
        " catch (e) { console.error(String(e)); process.exit(1); }\n"
);
try {
    require(out);
} finally {
    try { fs.unlinkSync(out); } catch (e) { /* best effort */ }
}
