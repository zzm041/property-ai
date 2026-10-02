const assert = require("assert");
const path = require("path");

let definition;
let startHandler;
let stopHandler;
let errorHandler;
let authorizeOptions;
let startOptions;
let startCount = 0;
let stopCount = 0;
let startImmediately = true;

const recorder = {
  onStart(handler) { startHandler = handler; },
  onStop(handler) { stopHandler = handler; },
  onError(handler) { errorHandler = handler; },
  offStart() {},
  offStop() {},
  offError() {},
  start(options) { startCount += 1; startOptions = options; if (startImmediately) startHandler(); },
  stop() { stopCount += 1; },
};

global.Page = (value) => { definition = value; };
global.wx = {
  getRecorderManager() { return recorder; },
  authorize(options) { authorizeOptions = options; },
};

require(path.resolve(__dirname, "../pages/work-orders/create.js"));

function createPage() {
  const state = {
    loading: false,
    houseLoading: false,
    recording: false,
    recordingBusy: false,
    fileIds: [],
  };
  const page = {
    ...definition,
    data: state,
    hidden: false,
    unloaded: false,
    setData(value) { Object.assign(state, value); },
  };
  page._bindRecorder();
  return { page, state };
}

async function main() {
  let current = createPage();
  let uploaded;
  let resolveUpload;
  current.page._upload = (...args) => {
    uploaded = args;
    return new Promise((resolve) => {
      resolveUpload = () => {
        if (!args[5] || args[5]()) args[4]();
        resolve();
      };
    });
  };

  current.page.startRecording();
  assert.strictEqual(authorizeOptions.scope, "scope.record");
  assert.strictEqual(current.state.recordingBusy, true);
  await current.page.submit();
  authorizeOptions.success();
  assert.deepStrictEqual(startOptions, {
    duration: 30000,
    sampleRate: 16000,
    numberOfChannels: 1,
    encodeBitRate: 48000,
    format: "mp3",
  });
  assert.strictEqual(current.state.recording, true);
  assert.strictEqual(current.state.recordingBusy, true);
  await current.page.submit();

  current.page.stopRecording();
  assert.strictEqual(stopCount, 1);
  assert.strictEqual(current.state.recordingBusy, true, "stop callback pending must block submit");
  stopHandler({ tempFilePath: "wxfile://record.mp3", fileSize: 1234, duration: 2400 });
  assert.deepStrictEqual(uploaded.slice(0, 4), [
    "wxfile://record.mp3",
    "audio/mpeg",
    1234,
    "repair-recording.mp3",
  ]);
  assert.strictEqual(current.state.recording, false);
  assert.strictEqual(current.state.recordingBusy, true, "recording upload pending must block submit");
  await current.page.submit();
  resolveUpload();
  await Promise.resolve();
  await Promise.resolve();
  assert.strictEqual(current.state.recordingBusy, false);
  assert.strictEqual(current.state.recordingHint, "录音已添加");

  uploaded = undefined;
  current.page._discardRecording = false;
  current.page._onRecorderStop({ tempFilePath: "wxfile://short.mp3", fileSize: 12, duration: 100 });
  assert.strictEqual(uploaded, undefined);
  assert.strictEqual(current.state.recordingBusy, false);
  assert.match(current.state.error, /录音过短/);

  errorHandler({ errMsg: "microphone unavailable" });
  assert.strictEqual(current.state.recording, false);
  assert.strictEqual(current.state.recordingBusy, false);
  assert.strictEqual(current.state.error, "microphone unavailable");

  current = createPage();
  const startsBeforeLateAuthorization = startCount;
  current.page.startRecording();
  const lateAuthorization = authorizeOptions;
  current.page.onHide();
  current.page.hidden = false;
  current.page.startRecording();
  const currentAuthorization = authorizeOptions;
  lateAuthorization.success();
  assert.strictEqual(startCount, startsBeforeLateAuthorization, "late authorization after page hide must not start recording");
  currentAuthorization.success();
  assert.strictEqual(startCount, startsBeforeLateAuthorization + 1, "current authorization must still start its own recording");

  current = createPage();
  let discardedUpload = false;
  current.page._upload = () => { discardedUpload = true; return Promise.resolve(); };
  current.page.startRecording();
  authorizeOptions.success();
  const stopsBeforeHide = stopCount;
  current.page.onHide();
  assert.strictEqual(stopCount, stopsBeforeHide + 1, "page hide must stop active recording");
  current.page.hidden = false;
  current.page.onHide();
  assert.strictEqual(stopCount, stopsBeforeHide + 1, "repeated cancellation must not issue another stop");
  assert.strictEqual(current.state.recordingBusy, true, "repeated cancellation must preserve the old stop barrier");
  current.page.hidden = false;
  const startsBeforeOldStop = startCount;
  current.page.startRecording();
  assert.strictEqual(startCount, startsBeforeOldStop, "a new recording must wait for the cancelled stop callback");
  assert.strictEqual(current.state.recordingBusy, true);
  stopHandler({ tempFilePath: "wxfile://discard.mp3", fileSize: 1234, duration: 2400 });
  assert.strictEqual(discardedUpload, false, "recording completed after page exit must be discarded");
  assert.strictEqual(current.state.recordingBusy, false);
  current.page.startRecording();
  authorizeOptions.success();
  assert.strictEqual(startCount, startsBeforeOldStop + 1, "recording may restart after the old stop callback settles");

  current = createPage();
  startImmediately = false;
  current.page.startRecording();
  authorizeOptions.success();
  current.page.onHide();
  current.page.hidden = false;
  const startsBeforeLateStart = startCount;
  current.page.startRecording();
  assert.strictEqual(startCount, startsBeforeLateStart, "a new recording must wait for a requested start callback");
  const stopsBeforeLateStart = stopCount;
  startHandler();
  assert.strictEqual(stopCount, stopsBeforeLateStart + 1, "a late start after page exit must be stopped");
  stopHandler({ tempFilePath: "wxfile://late-start.mp3", fileSize: 1234, duration: 2400 });
  assert.strictEqual(current.state.recordingBusy, false);
  startImmediately = true;

  console.log("work-order create native recorder focused tests passed");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
