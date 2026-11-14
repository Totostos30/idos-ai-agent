import test from 'node:test';
import assert from 'node:assert/strict';
import {timerSeconds,ringPath} from '../../src/island-motion.js';
test('timer events and restored snapshots show the same seconds remaining',()=>{
 const now=1700000000000;
 assert.equal(timerSeconds({ends_at:now+90000},now),90);
 assert.equal(timerSeconds({ends:(now+90000)/1000},now),90);
 assert.equal(timerSeconds({ends_at:now-1000},now),0);
 assert.equal(timerSeconds({seconds:30},now),30);
});
test('rings stay active with constant audio, stop on pause, and respond to level',()=>{
 const bands=Array(12).fill(.5);
 assert.notEqual(ringPath(bands,true,0,1),ringPath(bands,true,0,2));
 assert.equal(ringPath(bands,false,0,1),ringPath(bands,false,0,2));
 assert.notEqual(ringPath(bands,true,0,1),ringPath(Array(12).fill(1),true,0,1));
 assert.ok(!ringPath([],true,1,2).includes('NaN'));
});
test('alternating audio bins produce bounded smooth closed curves',()=>{
 const path=ringPath(Array.from({length:32},(_,i)=>i%2),true,1,12.5);
 assert.equal((path.match(/ C/g)||[]).length,120);
 assert.ok(path.endsWith(' Z'));
 const numbers=path.match(/-?\d+\.\d+/g).map(Number);
 assert.ok(numbers.every(n=>Number.isFinite(n)&&n>0&&n<64));
});
