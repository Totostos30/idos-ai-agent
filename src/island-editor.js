import { renderEditorRegion } from './island.js';
import { resizePanel } from './island-panel.js';
import { core } from './rpc.js';
import { el, toast } from './shell.js';
import { t } from './i18n.js';
import { iconNode } from './icons.js';
import { PARTS, TAB_LIST, partValues, RESET_VALUES, applyValues } from './parts.js';
import { COLS, REGIONS, GLYPHS, entries, shown, placements, position, arrangeCapsule, minimumSize } from './island-layout.js';

export function islandEditor(cfg) {
  const box=el('section',{class:'box settings-island visual-editor',id:'island-editor'});
  let region='capsule', renderedRegion='capsule', selected=null, busy=false, drag=null;
  const scrollPositions={};
  let visibleKeys=null;
  let liveData={};
  const refresh=async()=>{
    const methods={status:'status.state',weather:'weather.state',sysmon:'sysmon.state',calendar:'calendar.state',downloads:'downloads.state',ticker:'ticker.state',mixer:'mixer.state',notes:'notes.list',media:'media.state',obs:'obs.state',agent:'agent.status'};
    const results=await Promise.all(Object.entries(methods).map(async([key,method])=>[key,await core.try(method,{},8000)]));
    liveData=Object.fromEntries(results.filter(([,value])=>value&&!value.error));
    liveData.agent={state:'idle',steps:[],lastReply:'',...liveData.agent};
    const timers=await core.try('island.timers',{},8000);if(Array.isArray(timers))liveData.timers=new Map(timers.map(item=>[item.id,{state:"running",...item}]));
    const router=await core.try('router.status',{},8000);liveData.router=router?.active_label||router?.active||'';
    const indicators=await core.try('status.indicators',{},8000);if(indicators&&!indicators.error){liveData.kbd=indicators.keyboard||{};liveData.privacy=indicators.privacy||{};}
    if(box.isConnected&&!drag&&!busy)draw();
    const track=liveData.media?.track_id;
    if(liveData.media?.active)core.try('media.lyrics',{},30000).then(data=>{
      if(!data||data.error||track!==liveData.media?.track_id||!box.isConnected)return;
      liveData.lyricsData=data;if(!drag&&!busy)draw();
    });
  };
  const body=el('div'), inspector=el('div',{class:'island-inspector'}), status=el('span',{'aria-live':'polite',class:'mut small'});
  const nav=el('div',{class:'settings-modes',role:'group','aria-label':t('Вид островка')});
  const save=async values=>{
    if(busy)return;
    busy=true;box.setAttribute('aria-busy','true');status.textContent=t('Сохранение…');
    const restoreFocus=box.contains(document.activeElement);
    const r=await core.try('settings.update',{values});
    if(r&&!r.error){applyValues(cfg,values);status.textContent=t('Сохранено');}
    else {status.textContent=t('Не удалось сохранить');toast(r?.error||t('Не удалось сохранить'),'error');}
    busy=false;box.removeAttribute('aria-busy');draw();
    if(restoreFocus&&selected)[...box.querySelectorAll('[data-editor-key]')].find(n=>n.dataset.editorKey===selected)?.focus({preventScroll:true});
  };
  const enable=(item,show)=>{
    if(item.kind==='part')return partValues(item.key,show);
    const kind=item.kind==='tab'?'tabs':'tools', hidden=new Set(cfg.island?.['hidden_'+kind]||[]);
    if(show)hidden.delete(item.id);else hidden.add(item.id);
    return {['island.hidden_'+kind]:[...hidden]};
  };
  const current=()=>entries(region).filter(item=>shown(cfg.island,item)&&(!visibleKeys||visibleKeys.has(item.key)));
  const move=(key,patch,add=false)=>{
    if(busy)return;
    const item=entries(region).find(i=>i.key===key);if(!item)return;
    const keys=current().map(i=>i.key);if(!keys.includes(key))keys.push(key);
    const pos=placements(cfg.island,region,keys);
    // Переносимый блок получает приоритет, остальные занимают ближайшие свободные ячейки.
    const target={...pos[key],...patch};
    target.w=Math.max(1,Math.min(COLS,target.w));target.h=Math.max(1,Math.min(8,target.h));
    target.x=Math.max(0,Math.min(region==='capsule'?200:COLS-target.w,target.x));target.y=region==='capsule'?0:Math.max(0,Math.min(40,target.y));
    if(region==='capsule')target.h=1;
    if(region==='capsule')for(const [k,p]of Object.entries(pos))if(k!==key&&p.x>=target.x)p.x+=target.w;
    const ordered={[key]:target,...Object.fromEntries(Object.entries(pos).filter(([k])=>k!==key))};
    const I={...cfg.island,visual_layout:{...cfg.island?.visual_layout,[region]:ordered}};
    const packed=placements(I,region,[key,...keys.filter(k=>k!==key)]);
    selected=key;
    save({['island.visual_layout.'+region]:{...cfg.island?.visual_layout?.[region],...packed},...(add?enable(item,true):{})});
  };
  const capsulePointer=(event,key,add)=>{
    if(busy||event.button!==0)return;
    event.preventDefault();selected=key;
    const stage=body.querySelector('.editor-grid'),source=stage.querySelector(`[data-editor-key="${key}"]`);
    let preview=source;
    if(!preview){const candidate=structuredClone(cfg);applyValues(candidate,enable(entries('capsule').find(i=>i.key===key),true));preview=renderEditorRegion(candidate,'capsule',liveData).querySelector(`[data-grid-key="${key}"]`);}
    const ghost=(preview||event.currentTarget).cloneNode(true);
    ghost.removeAttribute('data-editor-key');ghost.className='capsule-drag-ghost island-cell';ghost.inert=true;
    const startX=event.clientX,startY=event.clientY;
    const stageRect=stage.getBoundingClientRect();
    let moved=false,inside=true;
    const siblings=()=>[...stage.querySelectorAll('[data-editor-key]')].filter(n=>n!==marker);
    const marker=source||el('div',{class:'island-cell capsule-placeholder','data-editor-key':key},...Array.from(ghost.childNodes,n=>n.cloneNode(true)));
    const update=e=>{
      if(!moved&&Math.hypot(e.clientX-startX,e.clientY-startY)<5)return;
      if(!moved){moved=true;document.body.append(ghost);marker.classList.add('drag-placeholder');}
      ghost.style.left=e.clientX+12+'px';ghost.style.top=e.clientY+12+'px';
      const r=stage.getBoundingClientRect();inside=e.clientX>=Math.min(r.left,stageRect.left)-16&&e.clientX<=Math.max(r.right,stageRect.right)+16&&e.clientY>=r.top-32&&e.clientY<=r.bottom+32;
      marker.style.opacity=inside?'.25':'0';ghost.classList.toggle('discard',!inside);
      if(!inside){
        if(marker.isConnected){marker.remove();const nodes=siblings();arrangeCapsule(stage,{...cfg.island,visual_layout:{capsule:Object.fromEntries(nodes.map((n,i)=>[n.dataset.editorKey,{x:i}]))}});}
        return;
      }
      const before=siblings().find(n=>e.clientX<n.getBoundingClientRect().left+n.getBoundingClientRect().width/2);
      const old=new Map(siblings().map(n=>[n,n.getBoundingClientRect().left]));
      const nodes=siblings();
      nodes.splice(before?nodes.indexOf(before):nodes.length,0,marker);
      const order=Object.fromEntries(nodes.map((n,i)=>[n.dataset.editorKey,{x:i}]));
      // Rebuild both wings during the gesture, so insertion next to the pinned block is exact.
      stage.replaceChildren(...nodes);
      arrangeCapsule(stage,{...cfg.island,visual_layout:{capsule:order}});
      for(const [n,x] of old){const dx=x-n.getBoundingClientRect().left;if(dx)n.animate([{transform:`translateX(${dx}px)`},{transform:'none'}],{duration:160,easing:'ease-out'});}
    };
    const cleanup=()=>{ghost.remove();document.removeEventListener('pointermove',update);document.removeEventListener('pointerup',finish);document.removeEventListener('pointercancel',cancel);document.removeEventListener('keydown',escape);drag=null;};
    const cancel=()=>{cleanup();draw();};
    const escape=e=>{if(e.key==='Escape')cancel();};
    const finish=()=>{
      const keys=[...stage.querySelectorAll('[data-editor-key]')].map(n=>n.dataset.editorKey);
      cleanup();
      if(!moved){if(add)move(key,{},true);else draw();return;}
      if(!inside){if(add){draw();return;}save(enable(entries('capsule').find(i=>i.key===key),false));return;}
      const order=Object.fromEntries(keys.map((k,i)=>[k,{x:i,y:0,w:1,h:1}]));
      save({'island.visual_layout.capsule':{...cfg.island?.visual_layout?.capsule,...order},...(add?enable(entries('capsule').find(i=>i.key===key),true):{})});
    };
    drag={cancel};document.addEventListener('pointermove',update);document.addEventListener('pointerup',finish,{once:true});document.addEventListener('pointercancel',cancel,{once:true});document.addEventListener('keydown',escape);
  };
  const pointer=(event,key,resize=false,add=false)=>{
    if(region==='capsule'){capsulePointer(event,key,add);return;}
    if(busy||event.button!==0)return;
    event.preventDefault();selected=key;
    const stage=body.querySelector('.editor-grid'), rect=stage.getBoundingClientRect();
    const cell=region==='capsule'?36:(rect.width+8)/COLS,row=region==='tools'?32:region==='capsule'||region==='header'?44:56;
    const pos=placements(cfg.island,region,[...current().map(i=>i.key),...(add?[key]:[])])[key];
    const startX=event.clientX,startY=event.clientY;
    const ghost=el('div',{class:'editor-drag-ghost'},t(entries(region).find(i=>i.key===key).label));document.body.append(ghost);
    const marker=el('div',{class:'editor-drop-preview'});stage.append(marker);
    const update=e=>{
      ghost.style.left=e.clientX+12+'px';ghost.style.top=e.clientY+12+'px';
      const activeRect=stage.getBoundingClientRect();
      const inside=e.clientX>=activeRect.left&&e.clientX<=activeRect.right&&e.clientY>=activeRect.top&&e.clientY<=activeRect.bottom;
      marker.hidden=!inside;if(!inside)return;
      const w=resize?Math.max(1,Math.min(COLS,pos.w+Math.round((e.clientX-startX)/cell))):pos.w;
      const h=resize?Math.max(1,Math.min(8,pos.h+Math.round((e.clientY-startY)/row))):pos.h;
      const target={x:resize?pos.x:Math.max(0,Math.min(COLS-w,Math.floor((e.clientX-stage.getBoundingClientRect().left)/cell))),y:resize?pos.y:Math.max(0,Math.floor((e.clientY-stage.getBoundingClientRect().top)/row)),w,h};
      const keys=[key,...current().map(i=>i.key).filter(k=>k!==key)];
      const original=placements(cfg.island,region,keys);
      const layout={[key]:target,...Object.fromEntries(Object.entries(original).filter(([k])=>k!==key))};
      const packed=placements({visual_layout:{[region]:layout}},region,keys);
      position(marker,packed[key]);
      for(const node of stage.querySelectorAll('[data-editor-key]')){
        const p=packed[node.dataset.editorKey];if(!p)continue;
        if(node.dataset.editorKey===key){node.style.opacity='.25';continue;}
        const before=node.getBoundingClientRect();position(node,p);const after=node.getBoundingClientRect();
        const dx=before.left-after.left,dy=before.top-after.top;
        if(dx||dy)node.animate([{transform:`translate(${dx}px,${dy}px)`},{transform:'none'}],{duration:160,easing:'ease-out'});
      }
    };update(event);
    drag={cancel:()=>{ghost.remove();marker.remove();document.removeEventListener('pointermove',update);document.removeEventListener('pointerup',finish);document.removeEventListener('keydown',escape);document.removeEventListener('pointercancel',cancel);}};
    const cancel=()=>{drag?.cancel();drag=null;draw();};
    const escape=e=>{if(e.key==='Escape'){drag.cancel();drag=null;draw();}};
    const finish=e=>{
      drag.cancel();drag=null;
      if(Math.hypot(e.clientX-startX,e.clientY-startY)<5&&!resize){if(add)move(key,{},true);else draw();return;}
      const dropRect=stage.getBoundingClientRect();
      if(e.clientX<dropRect.left||e.clientX>dropRect.right||e.clientY<dropRect.top||e.clientY>dropRect.bottom){draw();return;}
      move(key,resize?{w:pos.w+Math.round((e.clientX-startX)/cell),h:pos.h+Math.round((e.clientY-startY)/row)}:
        {x:Math.floor((e.clientX-stage.getBoundingClientRect().left)/cell),y:Math.floor((e.clientY-stage.getBoundingClientRect().top)/row)},add);
    };
    document.addEventListener('pointermove',update);document.addEventListener('pointerup',finish,{once:true});document.addEventListener('keydown',escape);document.addEventListener('pointercancel',cancel,{once:true});
  };
  const inspect=()=>{
    const item=entries(region).find(i=>i.key===selected&&shown(cfg.island,i));
    inspector.replaceChildren();if(!item){inspector.append(el('span',{class:'mut small'},t('Выберите блок на макете')));return;}
    const p=placements(cfg.island,region,current().map(i=>i.key))[item.key];
    inspector.append(el('b',{},t(item.label)));
    for(const [key,label,max] of (region==='capsule'?[]:[['x','Колонка',COLS-p.w],['y','Строка',40],['w','Ширина',COLS],['h','Высота',8]])){
      const input=el('input',{class:'field small',type:'number',min:key==='x'||key==='y'?0:minimumSize(region,item.key)[key],max,value:p[key],'aria-label':t(label)});
      input.onchange=()=>{const value=Number(input.value);if(Number.isFinite(value))move(item.key,{[key]:Math.round(value)});};
      inspector.append(el('label',{},el('span',{class:'mut small'},t(label)),input));
    }
    if(region==='capsule'&&item.key==='battery'){
      const select=el('select',{class:'field small','aria-label':t('Вид питания')},[['icon','Вертикальный значок'],['horizontal','Горизонтальный значок'],['combined','Значок и процент'],['text','Текст питания']].map(([value,label])=>el('option',{value,selected:(cfg.island?.battery_display||'icon')===value},t(label))));
      select.onchange=()=>save({'island.battery_display':select.value});
      inspector.append(el('label',{},t('Вид питания'),select));
    }
    if(region==='capsule')inspector.append(el('button',{class:'btn ghost',type:'button','aria-pressed':String(cfg.island?.capsule_center===item.key),onclick:()=>save({'island.capsule_center':cfg.island?.capsule_center===item.key?'':item.key})},t(cfg.island?.capsule_center===item.key?'Открепить от центра':'Зафиксировать по центру')));
    const last=item.kind==='tab'&&TAB_LIST.filter(([id])=>!cfg.island?.hidden_tabs?.includes(id)).length<=1;
    inspector.append(el('button',{class:'btn ghost',type:'button',disabled:last,onclick:()=>save(enable(item,false))},t('Убрать с островка')));
  };
  const draw=()=>{
    const previousScroll=body.querySelector('.editor-scroll');
    if(previousScroll)scrollPositions[renderedRegion]=[previousScroll.scrollLeft,previousScroll.scrollTop];
    renderedRegion=region;
    nav.querySelectorAll('button').forEach(b=>{const on=b.dataset.region===region;b.classList.toggle('on',on);b.setAttribute('aria-pressed',String(on));});
    const live=renderEditorRegion(cfg,region,liveData);
    const liveNodes=new Map([...(live?.querySelectorAll('[data-grid-key]')||[])].map(n=>[n.dataset.gridKey,n]));
    visibleKeys=new Set(liveNodes.keys());
    const list=current(), pos=placements(cfg.island,region,list.map(i=>i.key));
    const height=region==='capsule'?1:Math.max(1,...Object.values(pos).map(p=>p.y+p.h));
    const grid=el('div',{class:'island-grid editor-grid region-'+region,'data-editor-region':region,style:{minHeight:(height*(region==='tools'?32:region==='capsule'||region==='header'?44:56)-8)+'px'}});

    for(const item of list){
      const node=el('div',{class:'island-cell editor-cell'+(selected===item.key?' selected':''),tabindex:'0',role:'group','aria-label':t(item.label),title:t(item.label),'data-editor-key':item.key,
        onpointerdown:e=>pointer(e,item.key),onkeydown:e=>{
          if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();selected=item.key;const p=pos[item.key];if(region==='capsule'&&(e.shiftKey||e.key==='ArrowUp'||e.key==='ArrowDown'))return;const dx=e.key==='ArrowLeft'?-1:e.key==='ArrowRight'?1:0,dy=e.key==='ArrowUp'?-1:e.key==='ArrowDown'?1:0;move(item.key,e.shiftKey?{w:p.w+dx,h:p.h+dy}:{x:p.x+dx,y:p.y+dy});}
          if(e.key==='Enter'||e.key===' '){e.preventDefault();selected=item.key;draw();}
        }}, ...[...liveNodes.get(item.key).childNodes].map(n=>{if(n instanceof HTMLElement)n.inert=true;return n;}));
      const handle=el('button',{class:'editor-resize',type:'button',title:t('Потяните для изменения размера'),'aria-label':t('Изменить размер')+' · '+t(item.label),onpointerdown:e=>{e.stopPropagation();pointer(e,item.key,true);}},'◢');
      if(region!=='capsule'){node.append(handle);position(node,pos[item.key]);}
      if(region==='tools'){node.classList.toggle('icon-only',pos[item.key].w<3);node.classList.toggle('horizontal',pos[item.key].w>=3&&pos[item.key].h<3);}
      grid.append(node);
    }
    const tray=el('div',{class:'editor-palette'});
    for(const item of entries(region).filter(i=>!liveNodes.has(i.key)))tray.append(el('button',{class:'btn ghost',type:'button','data-palette-key':item.key,onpointerdown:e=>pointer(e,item.key,false,true),onclick:e=>{if(e.detail===0)move(item.key,{},true);}},iconNode(GLYPHS[item.key]||'app'),t(item.label)));
    const shell=el('div',{class:'editor-island '+(['capsule','music','activity'].includes(region)?'compact':'expanded')});
    if(['now','tools','agent'].includes(region)) {
      const header=renderEditorRegion(cfg,'header',{...liveData,previewTab:region});
      header.inert=true;shell.append(header);
    }
    if(region==='capsule')arrangeCapsule(grid,cfg.island);
    if(region==='header')grid.replaceChildren(...[...grid.children].sort((a,b)=>pos[a.dataset.editorKey].x-pos[b.dataset.editorKey].x));
    shell.append(grid);
    if(!['capsule','header'].includes(region))resizePanel(shell,cfg.island?.panel_size?.[region],{start:()=>{drag={};},finish:size=>{drag=null;save({['island.panel_size.'+region]:size});},cancel:()=>{drag=null;draw();}});
    body.replaceChildren(el('div',{class:'editor-scroll'},shell),
      el('h4',{},t('Доступные элементы')),el('p',{class:'mut small'},t('Макет повторяет островок. Нажмите блок в самом островке, чтобы открыть подробности или управление.')),tray.childNodes.length?tray:el('p',{class:'mut small'},t('Все элементы уже на островке')));
    const scroll=body.querySelector('.editor-scroll'),savedScroll=scrollPositions[region]||[0,0];
    scroll.scrollLeft=savedScroll[0];scroll.scrollTop=savedScroll[1];
    inspect();
  };
  for(const [id,label] of REGIONS)nav.append(el('button',{class:'settings-mode',type:'button','data-region':id,onclick:()=>{if(!busy){region=id;visibleKeys=null;selected=null;draw();}}},t(label)));
  box.append(el('h3',{},t('Настройка островка')),el('p',{class:'mut small'},t('Перетащите блок на макете или из списка. Потяните за угол, чтобы изменить размер. Стрелки — перемещение, Shift + стрелки — размер.')),nav,body,inspector,
    el('div',{class:'island-settings-footer'},status,el('button',{class:'btn ghost',type:'button',onclick:()=>save({...RESET_VALUES,'island.visual_layout':{},'island.layout':{},'island.panel_layout':{},'island.capsule_center':'','island.battery_display':'icon'})},t('Вернуть стандартный набор'))));
  draw();refresh();
  const refreshEvents=['status.state','weather.state','sysmon.state','calendar.changed','downloads.changed','ticker.changed','mixer.changed','notes.changed','media.state','router.status','status.keyboard','status.privacy','media.lyrics'];
  const fields={ 'status.state':'status','weather.state':'weather','sysmon.state':'sysmon','calendar.changed':'calendar','downloads.changed':'downloads','ticker.changed':'ticker','mixer.changed':'mixer','notes.changed':'notes','media.state':'media','status.keyboard':'kbd','status.privacy':'privacy','media.lyrics':'lyricsData' };
  const off=refreshEvents.map(event=>core.on(event,data=>{if(!box.isConnected)return;if(event==='router.status')liveData.router=data.active_label||data.active||'';else liveData[fields[event]]=data;if(!drag&&!busy)draw();}));
  let mounted=false;
  const observer=new MutationObserver(()=>{if(box.isConnected)mounted=true;else if(mounted){off.forEach(fn=>fn());observer.disconnect();}});
  observer.observe(document.body,{childList:true,subtree:true});
  return box;
}
