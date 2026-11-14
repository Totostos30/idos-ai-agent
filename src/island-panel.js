import { t } from './i18n.js';
import { el } from './shell.js';

export function panelSize(size={}, viewport={width:innerWidth,height:innerHeight}) {
  const clamp=(v,d,min,max)=>Math.max(Math.min(min,max),Math.min(max,Number.isFinite(Number(v))&&Number(v)>0?Number(v):d));
  return {w:clamp(size.w,560,360,Math.max(240,viewport.width-32)),h:clamp(size.h,460,240,Math.max(180,viewport.height-32))};
}

// Shared by the live panel and its settings preview; no writes until the gesture finishes.
export function resizePanel(node, saved, {start=()=>{},finish=()=>{},cancel=()=>{},change=()=>{}}={}) {
  const size=panelSize(saved);
  node.style.width=size.w+'px';node.style.height=size.h+'px';
  const handle=el('button',{class:'island-panel-resize',type:'button',title:t('Изменить размер островка'),'aria-label':t('Изменить размер островка')});
  handle.addEventListener('pointerdown',event=>{
    if(event.button!==0)return;
    event.preventDefault();event.stopPropagation();
    const rect=node.getBoundingClientRect(),scale=rect.width/node.offsetWidth||1;
    const centered=node.classList.contains('island')||parseFloat(getComputedStyle(node).marginLeft)>0;
    const horizontalScale=centered?2:1;
    handle.setPointerCapture?.(event.pointerId);
    const origin={x:event.clientX,y:event.clientY,w:node.offsetWidth,h:node.offsetHeight};
    let next={w:origin.w,h:origin.h};start();node.classList.add('resizing');
    const move=e=>{next=panelSize({w:origin.w+(e.clientX-origin.x)*horizontalScale/scale,h:origin.h+(e.clientY-origin.y)/scale});node.style.width=next.w+'px';node.style.height=next.h+'px';change(next);};
    const cleanup=()=>{document.removeEventListener('pointermove',move);document.removeEventListener('pointerup',up);document.removeEventListener('pointercancel',abort);document.removeEventListener('keydown',escape);node.classList.remove('resizing');};
    const up=()=>{cleanup();finish(next);};
    const abort=()=>{cleanup();node.style.width=origin.w+'px';node.style.height=origin.h+'px';cancel();};
    const escape=e=>{if(e.key==='Escape')abort();};
    document.addEventListener('pointermove',move);document.addEventListener('pointerup',up,{once:true});document.addEventListener('pointercancel',abort,{once:true});document.addEventListener('keydown',escape);
  });
  node.append(handle);
}
