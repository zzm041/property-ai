const assert = require("assert");
const path = require("path");

let definition;
let startHandler;
let stopHandler;
let errorHandler;
let authorizeOptions;
let startOptions;
let stopped = false;

const recorder = {
  onStart(handler) { startHandler = handler; },
  onStop(handler) { stopHandler = handler; },
  onError(handler) { errorHandler = handler; },
  offStart() {},
  offStop() {},
  offError() {},
  start(options) { startOptions = options; startHandler(); },
  stop() { stopped = true; },
};

global.Page = (value) => { definition = value; };
global.wx = {
  getRecorderManager() { return recorder; },
  authorize(options) { authorizeOptions = options; options.success(); },
};

require(path.resolve(__dirname, "../pages/work-orders/create.js"));

const state = { loading: false, houseLoading: false, recording: false };
const page = {
  ...definition,
  data: state,
  unloaded: false,
  setData(value) { Object.assign(state, value); },
};
let uploaded;
page._upload = (...args) => { uploaded = args; args[4](); };

page._bindRecorder();
page.startRecording();
assert.strictEqual(authorizeOptions.scope, "scope.record");
assert.deepStrictEqual(startOptions, {
  duration: 60000,
  sampleRate: 16000,
  numberOfChannels: 1,
  encodeBitRate: 48000,
  format: "mp3",
});
assert.strictEqual(state.recording, true);

page.stopRecording();
assert.strictEqual(stopped, true);
stopHandler({ tempFilePath: "wxfile://record.mp3", fileSize: 1234, duration: 2400 });
assert.deepStrictEqual(uploaded.slice(0, 4), [
  "wxfile://record.mp3",
  "audio/mpeg",
  1234,
  "repair-recording.mp3",
]);
assert.strictEqual(state.recording, false);
assert.strictEqual(state.recordingHint, "录音已添加");

uploaded = undefined;
stopHandler({ tempFilePath: "wxfile://short.mp3", fileSize: 12, duration: 100 });
assert.strictEqual(uploaded, undefined);
assert.match(state.error, /录音过短/);

errorHandler({ errMsg: "microphone unavailable" });
assert.strictEqual(state.recording, false);
assert.strictEqual(state.error, "microphone unavailable");

console.log("work-order create native recorder focused tests passed");
