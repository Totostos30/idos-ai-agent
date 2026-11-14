import test from 'node:test';
import assert from 'node:assert/strict';
import {Fluid} from '../../src/fluid.js';

test('hiding the island clears old waves even while music is playing',()=>{
 const originalWindow=globalThis.window,originalFrame=globalThis.requestAnimationFrame;
 globalThis.window={matchMedia:()=>({matches:false})};
 globalThis.requestAnimationFrame=()=>{};
 try {
  const cleared=[];
  const fluid=Object.assign(Object.create(Fluid.prototype),{
   get:()=>({hidden:true,playing:true,edge:true,pill:true,liquid:true}),
   cv:{style:{opacity:'1'},width:600,height:64},ctx:{clearRect:(...args)=>cleared.push(args)},
   musicVisibility:1,last:0,frame:0,spikes:new Float32Array(32).fill(1),
   beads:[{}],reach:1,reachX:200,sag:1,drawWideDrops:()=>{},
  });
  fluid.loop(100);
  assert.equal(fluid.cv.style.opacity,'0');
  assert.deepEqual(cleared,[[0,0,600,64]]);
  assert.equal(fluid.musicVisibility,0);
  assert.ok(fluid.spikes.every(v=>v===0));
  assert.equal(fluid.beads.length,0);
  assert.equal(fluid.reach,0);
  assert.equal(fluid.last,100);
 } finally {globalThis.window=originalWindow;globalThis.requestAnimationFrame=originalFrame;}
});
