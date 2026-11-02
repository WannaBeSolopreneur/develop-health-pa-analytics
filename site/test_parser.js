const fs = require("fs");
const vm = require("vm");
const path = require("path");

const root = path.join(__dirname);
const sandbox = { console };
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(
  fs.readFileSync(path.join(root, "data.js"), "utf8") + "\n" + fs.readFileSync(path.join(root, "ask.js"), "utf8"),
  sandbox
);

let failed = 0;
for (const fixture of sandbox.DEMO.fixtures) {
  const got = sandbox.parseQuestion(fixture.question);
  const same =
    got.status === fixture.status &&
    got.signature === fixture.signature &&
    got.message === fixture.message &&
    got.code === fixture.code;
  if (!same) {
    failed += 1;
    console.error("MISMATCH", fixture.question);
    console.error(" expected", fixture);
    console.error(" got", got);
  }
}
if (failed) {
  console.error(failed + " parser fixtures failed");
  process.exit(1);
}
console.log("page parser matched " + sandbox.DEMO.fixtures.length + " questions");
