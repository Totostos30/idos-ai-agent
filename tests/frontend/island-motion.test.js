import test from 'node:test';
import assert from 'node:assert/strict';
import {timerSeconds,visualizerPath,smoothBands} from '../../src/island-motion.js';
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

test('audio envelope follows levels smoothly and consistently across frame rates',()=>{
 const raised=smoothBands([0],[1],.05)[0];
 const twice=smoothBands(smoothBands([0],[1],.025),[1],.025)[0];
 assert.ok(raised>0 && raised<1);
 assert.ok(Math.abs(raised-twice)<1e-9);
 const dropped=smoothBands([1],[0],.05)[0];
 assert.ok(dropped>0 && dropped<1);
 assert.ok(dropped>1-raised); // release is gentler than attack
 assert.deepEqual(smoothBands([0,0,0],[NaN,-1,3],.05).slice(0,2),[0,0]);
});
test('visualizers respond to quiet levels and keep finite bounded coordinates',()=>{
 for(const style of ['waves','spectrum','waveform']){
  assert.notEqual(visualizerPath([0,0],true,0,1,style),visualizerPath([.03,.03],true,0,1,style));
  assert.notEqual(visualizerPath([.03,.03],true,0,1,style),visualizerPath([.08,.08],true,0,1,style));
  const path=visualizerPath([NaN,-10,Infinity],true,0,1,style);
  assert.ok(!/NaN|Infinity/.test(path));
 }
});
