// Timer events use epoch milliseconds; persisted snapshots use epoch seconds.
export function timerSeconds(timer, now=Date.now()) {
  const end=Number(timer.ends_at??timer.ends);
  if(Number.isFinite(end)&&end>0)return Math.max(0,(end>100_000_000_000?end/1000:end)-now/1000);
  return Math.max(0,Number(timer.seconds)||0);
}
// Audio arrives around 20 times per second; interpolate its envelope at frame rate.
export function smoothBands(previous=[], target=[], elapsed=1/60) {
 const dt=Math.max(0,Math.min(.1,Number(elapsed)||0));
 return target.map((value,i)=>{
  const next=Math.max(0,Math.min(1,Number(value)||0));
  const old=Number.isFinite(previous[i])?Math.max(0,Math.min(1,previous[i])):next;
  const time=next>old ? .07 : .14;
  return old+(next-old)*(1-Math.exp(-dt/time));
 });
}
// Horizontal visualizers share the same live audio and animation clock.
export function visualizerPath(bands=[],playing=false,layer=0,phase=0,style='waves') {
 const level=x=>{
  if(!bands.length)return 0;
  const at=Math.max(0,Math.min(1,x))*(bands.length-1),i=Math.floor(at),f=at-i;
  const sample=n=>Math.max(0,Math.min(1,Number(bands[n])||0));
  const mix=f*f*(3-2*f);
  return Math.pow(sample(i)+(sample(Math.min(i+1,bands.length-1))-sample(i))*mix,.45);
 };
 const points=[];
 if(style==='spectrum'||style==='waveform'){
  const count=style==='waveform'?64:28;
  for(let i=0;i<count;i++){const x=i*128/(count-1),v=playing?(level(i/count)*19+3+Math.sin(phase*2+i*.8)*2):1;points.push(`M${x.toFixed(2)},${(24-v).toFixed(2)} V${(24+v).toFixed(2)}`);}
  return points.join(' ');
 }
 for(let i=0;i<=128;i++){
  const x=i/128,envelope=Math.sin(x*Math.PI),amplitude=playing?(5+level(x)*14):0;
  const y=24+envelope*amplitude*(Math.sin(x*Math.PI*(4+layer)+phase*(1.5+layer*.2)+layer)*.7+Math.sin(x*Math.PI*8-phase+layer)*.3);
  points.push(`${i?'L':'M'}${i},${y.toFixed(2)}`);
 }
 return points.join(' ');
}
