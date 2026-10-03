const assert = require("assert");
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const read = (relativePath) => fs.readFileSync(path.join(ROOT, relativePath), "utf8");

function ruleBody(source, selector) {
  const selectorIndex = source.indexOf(selector);
  assert(selectorIndex >= 0, `missing CSS selector ${selector}`);
  const openIndex = source.indexOf("{", selectorIndex + selector.length);
  assert(openIndex >= 0, `missing CSS body for ${selector}`);
  let depth = 1;
  for (let index = openIndex + 1; index < source.length; index += 1) {
    if (source[index] === "{") depth += 1;
    if (source[index] === "}") depth -= 1;
    if (depth === 0) return source.slice(openIndex + 1, index);
  }
  assert.fail(`unterminated CSS body for ${selector}`);
}

const app = JSON.parse(read("app.json"));
const routes = [
  ...(app.pages || []),
  ...(app.subpackages || []).flatMap((subpackage) =>
    (subpackage.pages || []).map((page) => `${subpackage.root}/${page}`)
  ),
];

assert(routes.length > 0, "the mini program must register at least one page");
for (const route of routes) {
  for (const extension of ["js", "json", "wxml"]) {
    assert(
      fs.existsSync(path.join(ROOT, `${route}.${extension}`)),
      `${route} is missing ${extension}`
    );
  }
}

const appWxss = read("app.wxss");
const buttonRule = ruleBody(appWxss, "button.btn,\n.btn");
assert.match(buttonRule, /display:\s*flex/);
assert.match(buttonRule, /align-items:\s*center/);
assert.match(buttonRule, /justify-content:\s*center/);
assert.match(buttonRule, /width:\s*calc\(100%\s*-\s*48rpx\)/);
const inputRule = ruleBody(appWxss, "input,\ninput.field,\ninput.input");
assert.match(inputRule, /height:\s*88rpx/);
assert.match(inputRule, /padding:\s*0\s+22rpx/);
assert.match(ruleBody(appWxss, "textarea,\ntextarea.field"), /line-height:\s*1\.5/);
const pickerRule = ruleBody(appWxss, ".picker,\nview.field");
assert.match(pickerRule, /min-height:\s*88rpx/);
assert.match(pickerRule, /align-items:\s*center/);
assert.match(ruleBody(appWxss, ".action-row > button.btn"), /width:\s*auto/);
assert.match(ruleBody(appWxss, ".link"), /min-height:\s*88rpx/);
assert.match(ruleBody(appWxss, ".btn-compact,\nbutton.btn-compact"), /min-height:\s*88rpx/);
assert.match(
  ruleBody(appWxss, ".card > .loading,\n.card > .empty,\n.card > .hint,\n.card > .error,\n.card > .notice"),
  /margin-left:\s*0/
);

const wxmlFiles = [];
for (const route of routes) wxmlFiles.push(`${route}.wxml`);
const componentRoot = path.join(ROOT, "components");
if (fs.existsSync(componentRoot)) {
  const visit = (directory) => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const absolute = path.join(directory, entry.name);
      if (entry.isDirectory()) visit(absolute);
      else if (entry.name.endsWith(".wxml")) wxmlFiles.push(path.relative(ROOT, absolute));
    }
  };
  visit(componentRoot);
}

for (const relativePath of wxmlFiles) {
  const source = read(relativePath);
  const buttons = source.match(/<button\b[^>]*>/g) || [];
  for (const button of buttons) {
    const classMatch = button.match(/\bclass="([^"]+)"/);
    assert(classMatch, `${relativePath} contains an unclassified native button: ${button}`);
    assert(
      /(^|\s)(btn|link|house-choice|inline-button)(\s|$)/.test(classMatch[1]),
      `${relativePath} contains a button without an approved interaction style: ${button}`
    );
  }
}

assert(!/class="btn secondary"/.test(read("pages/supervisor/properties.wxml")));
assert(!/class="btn compact"/.test(read("pages/supervisor/staff.wxml")));
assert.match(
  read("pages/work-orders/create.wxss"),
  /\.house-choice\s*\{[\s\S]*display:\s*flex[\s\S]*width:\s*100%/
);
assert.doesNotMatch(read("pages/login/login.wxss"), /\.link\s*\{/);
assert.doesNotMatch(read("pages/password-reset/password-reset.wxss"), /\.link\s*\{/);
assert.strictEqual(
  JSON.parse(read("components/work-order-contact/index.json")).styleIsolation,
  "apply-shared"
);

for (const relativePath of [
  "pages/assistant/assistant.wxss",
  "pages/supervisor/knowledge.wxss",
  "pages/supervisor/properties.wxss",
  "pages/supervisor/sla.wxss",
  "pages/supervisor/staff.wxss",
  "pages/system/organizations.wxss",
]) {
  const source = read(relativePath);
  assert.doesNotMatch(
    source,
    /(^|\n)\s*(input|textarea|\.picker)\s*(,|\{)/,
    `${relativePath} must not override the shared native-control box model`
  );
}

console.log(`mini program UI layout contract passed for ${routes.length} registered pages`);
