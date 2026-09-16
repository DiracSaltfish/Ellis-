const assert=require('node:assert/strict');
const r=require('./chart-range.js');
for(const [now,want] of [
 ['2026-09-14T00:00:00+08:00','2026-09-11'],['2026-09-14T08:59:59+08:00','2026-09-11'],
 ['2026-09-14T09:00:00+08:00','2026-09-14'],['2026-09-15T00:00:00+08:00','2026-09-14'],
 ['2026-09-19T13:00:00+08:00','2026-09-18'],['2026-09-20T13:00:00+08:00','2026-09-18'],
 ['2026-10-08T08:00:00+08:00','2026-09-30'],['2026-10-08T09:00:00+08:00','2026-10-08'],
 ['2026-09-25T15:00:00+08:00','2026-09-24']]) assert.equal(r.defaultDate(new Date(now)),want,now);
assert.deepEqual(r.dates('2026-09-15',3),['2026-09-11','2026-09-14','2026-09-15']);
assert.deepEqual(r.dates('2026-09-15',5),['2026-09-09','2026-09-10','2026-09-11','2026-09-14','2026-09-15']);
assert.deepEqual(r.dates('',5),[]);
const p=(date,minute)=>({trade_date:date,minute});
assert(r.contiguous(p('2026-09-14','12:00'),p('2026-09-14','13:00')));
assert(!r.contiguous(p('2026-09-14','09:30'),p('2026-09-14','09:32')));
assert(!r.contiguous(p('2026-09-14','09:30'),p('2026-09-15','09:31')));
assert.equal(r.position(p('2026-09-15','09:30'),['2026-09-14','2026-09-15']),338);
console.log('PASS Beijing 09:00 boundary, weekends, holidays, 3/5 trading days, disconnected dates/gaps');
