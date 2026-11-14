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
