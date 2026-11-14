import test from 'node:test';
import assert from 'node:assert/strict';
// shell.js only needs window/location at module load; layout calculation itself has no DOM dependency.
globalThis.window = {};
globalThis.location = {search:''};
const { placements, entries, REGIONS, COLS } = await import('../../src/island-layout.js');
function verify(pos) {
  const occupied=new Set();
  for(const p of Object.values(pos)) {
    assert.ok(p.x>=0 && p.y>=0 && p.w>=1 && p.h>=1 && p.x+p.w<=COLS);
    for(let x=p.x;x<p.x+p.w;x++)for(let y=p.y;y<p.y+p.h;y++){
      assert.ok(!occupied.has(x+','+y),'blocks overlap');occupied.add(x+','+y);
    }
  }
}
test('every catalog region packs without overlap and survives persistence',()=>{
  for(const [region] of REGIONS){
    const keys=entries(region).map(i=>i.key),pos=placements({},region,keys);
    verify(pos);assert.equal(Object.keys(pos).length,keys.length);
    assert.deepEqual(placements({visual_layout:{[region]:pos}},region,keys),pos);
  }
});
test('saved grid coordinates survive absent live data and new blocks',()=>{
  const I={visual_layout:{capsule:{clock:{x:8,y:2,w:3,h:2},battery:{x:0,y:0,w:1,h:1}}}};
  const pos=placements(I,'capsule',['clock','weather']);
  assert.deepEqual(pos.clock,I.visual_layout.capsule.clock);verify(pos);
});
test('colliding, oversized and nonnumeric persisted values normalize safely',()=>{
  const keys=entries('now').map(i=>i.key);
  for(let seed=0;seed<30;seed++) {
    const saved=Object.fromEntries(keys.map((k,i)=>[k,{x:i%3-5,y:seed%2-2,w:i%2?99:'bad',h:i%3?0:20}]));
    verify(placements({visual_layout:{now:saved}},'now',keys));
  }
});
