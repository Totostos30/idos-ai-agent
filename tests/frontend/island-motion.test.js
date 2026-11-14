import test from 'node:test';
import assert from 'node:assert/strict';
import {timerSeconds,visualizerPath} from '../../src/island-motion.js';
test('timer events and restored snapshots show the same seconds remaining',()=>{
 const now=1700000000000;
 assert.equal(timerSeconds({ends_at:now+90000},now),90);
 assert.equal(timerSeconds({ends:(now+90000)/1000},now),90);
 assert.equal(timerSeconds({ends_at:now-1000},now),0);
 assert.equal(timerSeconds({seconds:30},now),30);
});
test('horizontal visualizers animate with constant audio and stop on pause',()=>{
 for(const style of ['waves','spectrum','waveform']){
  const bands=Array(16).fill(.5);
  assert.notEqual(visualizerPath(bands,true,0,1,style),visualizerPath(bands,true,0,2,style));
  assert.equal(visualizerPath(bands,false,0,1,style),visualizerPath(bands,false,0,2,style));
  assert.notEqual(visualizerPath(bands,true,0,1,style),visualizerPath(Array(16).fill(1),true,0,1,style));
  assert.ok(!visualizerPath([],true,0,2,style).includes('NaN'));
 }
});
