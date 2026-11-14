import test from 'node:test';
import assert from 'node:assert/strict';
import {exportDesign,importDesign} from '../../src/island-design.js';
test('design exchange round trips layout and filters non-design settings',()=>{
 const source={parts:{clock:true},visual_layout:{capsule:{clock:{x:0}}},waves:{visualizer:'spectrum'},panel_size:{now:{w:500,h:400}},health:{enabled:true},hide_for_apps:['private.exe']};
 const json=exportDesign(source),values=importDesign(json);
 assert.deepEqual(values['island.visual_layout'],source.visual_layout);
 assert.deepEqual(values['island.waves'],source.waves);
 assert.deepEqual(values['island.panel_size'],source.panel_size);
 assert.ok(!json.includes('private.exe'));assert.ok(!json.includes('health'));
 assert.equal(values['island.battery_display'],'icon');
});
test('design imports reject unsupported and unsafe files before applying settings',()=>{
 for(const text of ['{}','null',JSON.stringify({type:'idos-island-design',schemaVersion:2,island:{}}),'{"type":"idos-island-design","schemaVersion":1,"island":{"waves":{"__proto__":{"x":true}}}}',JSON.stringify({type:'idos-island-design',schemaVersion:1,island:{parts:[]}}),JSON.stringify({type:'idos-island-design',schemaVersion:1,island:{password:'secret'}}),' '.repeat(262145)])assert.throws(()=>importDesign(text));
});
