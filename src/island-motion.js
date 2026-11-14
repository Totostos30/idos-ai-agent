// Timer events use epoch milliseconds; persisted snapshots use epoch seconds.
export function timerSeconds(timer, now=Date.now()) {
  const end=Number(timer.ends_at??timer.ends);
  if(Number.isFinite(end)&&end>0)return Math.max(0,(end>100_000_000_000?end/1000:end)-now/1000);
  return Math.max(0,Number(timer.seconds)||0);
}
// Horizontal visualizers share the same live audio and animation clock.
export function visualizerPath(bands=[],playing=false,layer=0,phase=0,style='waves') {
 const level=x=>{const i=Math.min(bands.length-1,Math.floor(x*bands.length));return Math.max(0,Math.min(1,Math.pow(Math.max(0,Number(bands[i])||0),.65)*1.25));};
 const points=[];
 if(style==='spectrum'||style==='waveform'){
  const count=style==='waveform'?64:28;
  for(let i=0;i<count;i++){const x=4+i*120/(count-1),v=playing?(level(i/count)*19+3+Math.sin(phase*2+i*.8)*2):1;points.push(`M${x.toFixed(2)},${(24-v).toFixed(2)} V${(24+v).toFixed(2)}`);}
  return points.join(' ');
 }
 for(let i=0;i<=128;i++){
  const x=i/128,envelope=Math.sin(x*Math.PI),amplitude=playing?(5+level(x)*10):0;
  const y=24+envelope*amplitude*(Math.sin(x*Math.PI*(4+layer)+phase*(1.5+layer*.2)+layer)*.7+Math.sin(x*Math.PI*8-phase+layer)*.3);
  points.push(`${i?'L':'M'}${i},${y.toFixed(2)}`);
 }
 return points.join(' ');
}
