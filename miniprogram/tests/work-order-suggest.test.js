const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const flush = async () => {
  for (let i = 0; i < 12; i += 1) await Promise.resolve();
};

let definition;
const uploaded = [];
const submits = [];
let suggestImpl = () =>
  Promise.resolve({ text: "插座面板有烧灼痕迹", source: "vlm", reason_code: null });

function loadPage() {
  vm.runInNewContext(
    fs.readFileSync(path.resolve(__dirname, "../pages/work-orders/create.js"), "utf8"),
    {
      Page(value) {
        definition = value;
      },
      wx: {
        navigateTo() {},
        getRecorderManager() {
          return undefined;
        },
      },
      require(name) {
        if (name === "../../services/api") {
          return {
            contactDefault: () => Promise.resolve({ phone: "13800000000" }),
            houses: () => Promise.resolve({ items: [], next_cursor: 0 }),
            registerAndUpload: ({ mimeType, originalName }) => {
              const file = { id: 100 + uploaded.length, mime_type: mimeType, original_name: originalName };
              uploaded.push(file);
              return Promise.resolve(file);
            },
            suggestWorkOrderDescription: (fileIds) => suggestImpl(fileIds),
            createWorkOrder: (body, key) => {
              submits.push({ body, key });
              return Promise.resolve({ id: 9001 });
            },
          };
        }
        if (name === "../../services/auth") {
          return { getSelectedHouseId: () => 5, setSelectedHouseId() {}, setLastWorkOrderId() {} };
        }
        if (name === "../../utils/ui") {
          return { withLoading: (page, work) => Promise.resolve().then(work) };
        }
        if (name === "../../utils/paging") {
          return { appendPage: (items) => ({ items, nextCursor: 0 }) };
        }
        if (name === "../../utils/house-display") {
          return { houseLabel: () => "3号楼2单元101" };
        }
        if (name === "../../utils/contact-phone") {
          return { normalizeContact: (value) => String(value || "").replace(/\s+/g, "") || null };
        }
        throw new Error(`unexpected require: ${name}`);
      },
    }
  );
}

function createPage() {
  const page = {
    ...definition,
    data: JSON.parse(JSON.stringify(definition.data)),
    hidden: false,
    unloaded: false,
    setData(value) {
      Object.assign(this.data, value);
    },
  };
  page.data.houseId = "5";
  page.data.houses = [{ house_id: 5 }];
  page.data.contactPhone = "13800000000";
  page.data.contactConfirmed = true;
  return page;
}

async function uploadPhoto(page) {
  await page._upload("/tmp/a.jpg", "image/jpeg", 2048, "repair.jpg");
  await flush();
}

async function main() {
  loadPage();

  // 1. Upload fills the description and labels it as generated.
  let page = createPage();
  await uploadPhoto(page);
  assert.equal(page.data.text, "插座面板有烧灼痕迹");
  assert.equal(page.data.suggestNote, "AI 预填，可修改");
  assert.equal(page.data.fileIds.length, 1);
  assert.equal(typeof page.data.fileIds[0], "number");

  // 2. The resident may edit; their text survives the next suggestion.
  page.onText({ detail: { value: "我家插座冒烟，请尽快来看" } });
  assert.equal(page.data.suggestNote, "");
  await uploadPhoto(page);
  assert.equal(page.data.text, "我家插座冒烟，请尽快来看");
  assert.match(page.data.suggestNote, /^AI 建议：/);

  // 3. A suggestion that comes back empty never touches the typed text.
  page = createPage();
  suggestImpl = () => Promise.resolve({ text: "", source: "none", reason_code: "AI.TIMEOUT" });
  await uploadPhoto(page);
  assert.equal(page.data.text, "");
  assert.match(page.data.suggestNote, /AI\.TIMEOUT/);

  // 4. A failed call is silent for the resident but never blocks submission.
  page = createPage();
  suggestImpl = () => Promise.reject(new Error("network down"));
  await uploadPhoto(page);
  assert.equal(page.data.text, "");
  assert.equal(page.data.suggestNote, "未生成描述，请手动填写");
  page.onText({ detail: { value: "手动填写：厨房水龙头一直滴水" } });
  await page.submit();
  await flush();
  assert.equal(submits.length, 1);
  assert.equal(submits[0].body.text, "手动填写：厨房水龙头一直滴水");

  // 5. Accepting the suggestion submits exactly the pre-filled text.
  page = createPage();
  suggestImpl = () => Promise.resolve({ text: "卫生间地漏反味", source: "vlm", reason_code: null });
  await uploadPhoto(page);
  await page.submit();
  await flush();
  assert.equal(submits[1].body.text, "卫生间地漏反味");
  assert.deepEqual(submits[1].body.file_ids, page.data.fileIds);
  assert.equal(submits[1].body.file_ids.length, 1);

  // 6. A late answer for an abandoned form is dropped.
  page = createPage();
  let release;
  suggestImpl = () => new Promise((resolve) => { release = resolve; });
  const pending = page._upload("/tmp/b.jpg", "image/jpeg", 1024, "repair.jpg");
  await flush();
  page.unloaded = true;
  release({ text: "过期结果", source: "vlm", reason_code: null });
  await pending;
  await flush();
  assert.equal(page.data.text, "");

  console.log(
    "work order suggest: prefill, user-edit wins, empty/failed answers, manual submit fallback, stale drop passed"
  );
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
