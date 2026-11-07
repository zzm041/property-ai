const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
let response, refreshToken = null, token = null, clears = 0, responses = [];
const moduleStub = {exports:{}};
vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../services/api.js'),'utf8'), {
  module:moduleStub,
  require(name) {
    if (name === '../config/env') return {BASE_URL:'http://synthetic.invalid'};
    if (name === './auth') return {getToken:()=>token,getRefreshToken:()=>refreshToken,
      setToken:value=>{token=value;},setRefreshToken:value=>{refreshToken=value;},clearSession:()=>{clears++;}};
    return require('../utils/error');
  },
  wx:{request(options){options.success(responses.length ? responses.shift() : response);},reLaunch(){}},
});
(async()=>{
  for(const [statusCode,code] of [[403,'IAM.FORBIDDEN'],[400,'EMP.INVALID_INPUT'],[409,'EMP.CONFLICT'],[401,'AUTH.MISSING_TOKEN']]){
    response={statusCode,data:{error:{code,message:'private backend text'}}};
    await assert.rejects(moduleStub.exports.updateEmployee(11,{max_capacity:1}),error=>{
      assert.equal(error.statusCode,statusCode);assert.equal(error.code,code);
      assert(!error.message.includes('private backend text'));return true;
    });
  }
  for(const [statusCode,code] of [[409,'EMP.CONFLICT'],[400,'EMP.INVALID_INPUT'],[403,'IAM.FORBIDDEN']]){
    refreshToken='SYN-REFRESH';token='SYN-OLD';const before=clears;
    responses=[{statusCode:401,data:{error:{code:'AUTH.TOKEN_EXPIRED'}}},
      {statusCode:200,data:{data:{access_token:'SYN-NEW'}}},
      {statusCode,data:{error:{code}}}];
    await assert.rejects(moduleStub.exports.updateEmployee(11,{}),error=>{
      assert.equal(error.statusCode,statusCode);assert.equal(error.code,code);return true;
    });
    assert.equal(clears,before);assert.equal(token,'SYN-NEW');assert.equal(responses.length,0);
  }
  refreshToken='SYN-REFRESH';token='SYN-OLD';const before=clears;
  responses=[{statusCode:401,data:{error:{code:'AUTH.TOKEN_EXPIRED'}}},
    {statusCode:401,data:{error:{code:'AUTH.SESSION_REVOKED'}}}];
  await assert.rejects(moduleStub.exports.updateEmployee(11,{}),error=>error.statusCode===401);
  assert.equal(clears,before+1);assert.equal(responses.length,0);
  console.log('API error metadata contracts passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
