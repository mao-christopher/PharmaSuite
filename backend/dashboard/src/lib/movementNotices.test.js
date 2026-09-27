import test from 'node:test';
import assert from 'node:assert/strict';
import { movementNotices } from './movementNotices.js';

const row = { event_id: 'pickup', event_type: 'pickup', media_time_ms: 14600, session_id: 'one', medication_key: 'IBUPROFEN_200MG', state: 'HELD', nearest_region_id: 'shelf_ibuprofen_200mg' };
const state = { recording: { source: 'upload' }, media_time_ms: 14599, activity: [row], alerts: {}, layout: { medications: [{medication_key:'IBUPROFEN_200MG',name:'Ibuprofen',strength:'200 mg'}], regions: [] } };

test('notices appear on the video clock and replay without changing input state', () => {
  assert.deepEqual(movementNotices(state), []);
  const at = {...state, media_time_ms:14600, activity:[row,row]};
  const before = JSON.stringify(at), notice = movementNotices(at);
  assert.equal(notice.length,1); assert.equal(notice[0].title,'Wristband detected pickup');
  assert.equal(notice[0].medication,'Ibuprofen 200 mg'); assert.equal(notice[0].simulated,true);
  assert.equal(JSON.stringify(at),before); assert.deepEqual(movementNotices(state),[]);
  assert.deepEqual(movementNotices(at),notice);
});

test('put-down and uncertainty report the persisted decision without guessing identity', () => {
  const put={...row,event_id:'release',event_type:'release',state:'AT_COUNTER'};
  let notice=movementNotices({...state,media_time_ms:20000,activity:[put]})[0];
  assert.equal(notice.title,'Wristband detected put down'); assert.match(notice.message,/counter/);
  notice=movementNotices({...state,media_time_ms:20000,activity:[{...row,medication_key:'UNKNOWN',state:'NEEDS_CONFIRMATION'}],alerts:{a:{alert_id:'a',status:'open',alert_type:'uncertainty',metadata:{session_id:'one',reason:'which_bottle',bottle_options:[{medication_key:'IBUPROFEN_200MG'}]}}}})[0];
  assert.match(notice.message,/before inventory changes/);assert.equal(notice.alert.alert_id,'a');
});
