const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const file = path.resolve(__dirname, '../services/api.js');
const urls = [];
let token = 'old';
const apiModule = { exports: {} };
vm.runInNewContext(fs.readFileSync(file, 'utf8'), {
  module: apiModule,
  require: (name) => name === '../config/env' ? { BASE_URL: 'https://synthetic.invalid' }
    : name === './auth' ? { getToken: () => token, getRefreshToken: () => '' }
    : { isUnauthorized: () => false, messageFromError: () => 'synthetic' },
  wx: {
    request: ({ url, success }) => { urls.push(url); success({ statusCode: 200, data: { data: {} } }); },
    downloadFile: ({ url, success }) => {
      urls.push(url);
      if (token === 'old') { token = 'new'; success({ statusCode: 401 }); }
      else success({ statusCode: 200, tempFilePath: '/synthetic' });
    },
  },
}, { filename: file });
(async () => {
  await apiModule.exports.workOrder(41);
  await apiModule.exports.workOrder(41, 'property');
  await apiModule.exports.downloadFile({ fileId: 22, audience: 'property' });
  assert.strictEqual(urls[0], 'https://synthetic.invalid/work-orders/41');
  assert.strictEqual(urls[1], 'https://synthetic.invalid/work-orders/41?audience=property');
  assert.strictEqual(urls[2], 'https://synthetic.invalid/files/22/content?audience=property');
  assert.strictEqual(urls[3], urls[2]);
  console.log('audience request and authentication retry contracts passed');
})().catch((error) => { console.error(error); process.exitCode = 1; });
