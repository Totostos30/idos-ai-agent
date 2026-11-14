// Timer events use epoch milliseconds; persisted snapshots use epoch seconds.
export function timerSeconds(timer, now=Date.now()) {
  const end=Number(timer.ends_at??timer.ends);
  if(Number.isFinite(end)&&end>0)return Math.max(0,(end>100_000_000_000?end/1000:end)-now/1000);
  return Math.max(0,Number(timer.seconds)||0);
}
// Periodic low-frequency harmonics keep the seam smooth; broad audio samples
// avoid needle-like spikes when adjacent FFT bins differ sharply.
export function ringPath(bands=[],playing=false,ring=0,phase=0) {
  const count=120,points=[];
  const sample=i=>Math.max(0,Math.min(1,Number(bands[(i%bands.length+bands.length)%bands.length])||0));
  const energy=bands.length?bands.reduce((sum,_,i)=>sum+sample(i),0)/bands.length:0;
  for(let i=0;i<count;i++){
    const angle=i/count*Math.PI*2,at=i/count*(bands.length||1),index=Math.floor(at),mix=at-index;
    const smooth=j=>(sample(j-2)+2*sample(j-1)+3*sample(j)+2*sample(j+1)+sample(j+2))/9;
    const level=playing?smooth(index)*(1-mix)+smooth(index+1)*mix:0;
    const motion=playing?(Math.sin(angle*3+phase*1.6+ring*.8)*2.1+Math.sin(angle*5-phase*1.2+ring)*1.1)*(0.8+energy*.4):0;
    const radius=17+ring*5+level*4+motion;
    points.push([32+Math.cos(angle)*radius,32+Math.sin(angle)*radius]);
  }
  // Closed Catmull-Rom curve: continuous tangents instead of a polygon.
  const point=p=>p.map(v=>v.toFixed(2)).join(',');
  let path='M'+point(points[0]);
  for(let i=0;i<count;i++){
    const a=points[(i+count-1)%count],b=points[i],c=points[(i+1)%count],d=points[(i+2)%count];
    path+=' C'+point(b.map((v,k)=>v+(c[k]-a[k])/6))+' '+point(c.map((v,k)=>v-(d[k]-b[k])/6))+' '+point(c);
  }
  return path+' Z';
}
