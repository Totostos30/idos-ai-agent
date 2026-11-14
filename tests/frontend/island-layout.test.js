import test from 'node:test';
import assert from 'node:assert/strict';
// shell.js only needs window/location at module load; layout calculation itself has no DOM dependency.
globalThis.window = {};
globalThis.location = {search:''};
const { placements, entries, REGIONS, COLS } = await import('../../src/island-layout.js');
function verify(pos, capsule=false) {
  const occupied=new Set();
  for(const p of Object.values(pos)) {
    assert.ok(p.x>=0 && p.y>=0 && p.w>=1 && p.h>=1 && (capsule || p.x+p.w<=COLS));
    for(let x=p.x;x<p.x+p.w;x++)for(let y=p.y;y<p.y+p.h;y++){
      assert.ok(!occupied.has(x+','+y),'blocks overlap');occupied.add(x+','+y);
    }
  }
}
test('every catalog region packs without overlap and survives persistence',()=>{
  for(const [region] of REGIONS){
    const keys=entries(region).map(i=>i.key),pos=placements({},region,keys);
    verify(pos,region==='capsule');assert.equal(Object.keys(pos).length,keys.length);
    assert.deepEqual(placements({visual_layout:{[region]:pos}},region,keys),pos);
  }
});
test('saved grid coordinates survive absent live data and new blocks',()=>{
  const I={visual_layout:{capsule:{clock:{x:8,y:2,w:3,h:2},battery:{x:0,y:0,w:1,h:1}}}};
  const pos=placements(I,'capsule',['clock','weather']);
  assert.equal(pos.clock.y,0);assert.equal(pos.clock.h,1);verify(pos,true);
});
test('colliding, oversized and nonnumeric persisted values normalize safely',()=>{
  const keys=entries('now').map(i=>i.key);
  for(let seed=0;seed<30;seed++) {
    const saved=Object.fromEntries(keys.map((k,i)=>[k,{x:i%3-5,y:seed%2-2,w:i%2?99:'bad',h:i%3?0:20}]));
    verify(placements({visual_layout:{now:saved}},'now',keys));
  }
});

test('capsule remains one row even with all blocks and old multiline layouts',()=>{
 const keys=entries('capsule').map(i=>i.key),I={visual_layout:{capsule:Object.fromEntries(keys.map((k,i)=>[k,{x:i%12,y:i,w:3,h:8}]))}};
 const pos=placements(I,'capsule',keys);verify(pos,true);
 for(const p of Object.values(pos)){assert.equal(p.y,0);assert.equal(p.h,1);}
});

test('widgets enforce readable minima while tools allow icon buttons',()=>{
 const region='now',keys=['p_player','w_clock','w_mixer'];
 const pos=placements({visual_layout:{now:Object.fromEntries(keys.map(k=>[k,{w:1,h:1}]))}},region,keys);
 assert.equal(pos.p_player.w,12);assert.ok(pos.w_clock.w>=6);assert.equal(pos.w_mixer.w,12);verify(pos);
 const tools=placements({visual_layout:{tools:{shot_area:{w:1,h:1}}}},'tools',['shot_area']);
 assert.equal(tools.shot_area.w,1);assert.equal(tools.shot_area.h,2);
});


test('track progress is part of the player, not a separately placeable panel',()=>{
 assert.ok(entries('now').some(i=>i.key==='p_player'));
 assert.ok(!entries('now').some(i=>i.key==='p_progress'));
});
test('expanded panel sizes clamp corrupt data and respect small viewports',async()=>{
 const {panelSize}=await import('../../src/island-panel.js');
 assert.deepEqual(panelSize({w:600,h:500},{width:1000,height:800}),{w:600,h:500});
 assert.deepEqual(panelSize({w:-1,h:'bad'},{width:1000,height:800}),{w:560,h:460});
 assert.deepEqual(panelSize({w:9999,h:9999},{width:800,height:600}),{w:768,h:568});
 assert.deepEqual(panelSize({w:100,h:100},{width:320,height:240}),{w:288,h:208});
});
