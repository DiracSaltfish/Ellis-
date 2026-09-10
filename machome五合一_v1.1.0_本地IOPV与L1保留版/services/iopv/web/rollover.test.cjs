const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const text=fs.readFileSync(__dirname+'/app.js','utf8');
const source=text.match(/function rolloverDate\([^\n]+/)[0];
const context={};vm.createContext(context);vm.runInContext(source,context);
assert.equal(context.rolloverDate('2026-09-09','2026-09-09','2026-09-10'),'2026-09-10');
assert.equal(context.rolloverDate('2026-09-08','2026-09-09','2026-09-10'),'2026-09-08');
console.log('PASS live date follows midnight, selected history remains unchanged');
