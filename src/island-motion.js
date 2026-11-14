// Timer events use epoch milliseconds; persisted snapshots use epoch seconds.
export function timerSeconds(timer, now=Date.now()) {
  const end=Number(timer.ends_at??timer.ends);
  if(Number.isFinite(end)&&end>0)return Math.max(0,(end>100_000_000_000?end/1000:end)-now/1000);
  return Math.max(0,Number(timer.seconds)||0);
}
export function ringPath(bands=[],playing=false,ring=0,phase=0) {
  const points=[],energy=bands.length?bands.reduce((sum,v)=>sum+(Number(v)||0),0)/bands.length:0;
  for(let i=0;i<120;i++){
    const angle=i/120*Math.PI*2,at=i/120*(bands.length||1),index=Math.floor(at);
    const level=playing?Math.max(0,Math.min(1,(bands[index%bands.length]||0)*(1-at%1)+(bands[(index+1)%bands.length]||0)*(at%1))):0;
    const motion=playing?(Math.sin(angle*5+phase*2.3+ring)*2.4+Math.sin(angle*9-phase*3.1)*1.5)*(0.65+energy*.5):0;
    const radius=17+ring*5+level*5+motion;
    points.push((32+Math.cos(angle)*radius).toFixed(2)+','+(32+Math.sin(angle)*radius).toFixed(2));
  }
  return 'M'+points.join(' L')+' Z';
}
