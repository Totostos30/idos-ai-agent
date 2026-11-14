import { core } from './rpc.js';
import { el, toast } from './shell.js';
import { t } from './i18n.js';
import { iconNode } from './icons.js';
import { PARTS, TAB_LIST, partValues, RESET_VALUES, applyValues } from './parts.js';
import { COLS, REGIONS, GLYPHS, entries, shown, placements, position } from './island-layout.js';

const SAMPLE = {clock:'12:48',battery_percent:'76%',weather:'9°',layout:'RU',caps:'⇪',load:'24% · 61%',ticker:'$ 92,4',focus:'24:59',sleep:'30:00',music_title:'Группа — Название трека',lyrics:'Строка вашей любимой песни',agent_steps:'Поиск → действие → готово',reply_text:'Ответ Эйдоса',p_lyrics:'Строка вашей любимой песни',w_clock:'12:48',w_battery:'76%',w_weather:'9°',w_sysmon:'ЦП 24% · ОЗУ 61%',pa_input:'Задача для Эйдоса…',pa_model:'Эйдос · модель'};
export function islandEditor(cfg) {
  const box=el('section',{class:'box settings-island visual-editor',id:'island-editor'});
  let region='capsule', renderedRegion='capsule', selected=null, busy=false, drag=null;
  const scrollPositions={};
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
  const current=()=>entries(region).filter(item=>shown(cfg.island,item));
  const move=(key,patch,add=false)=>{
    if(busy)return;
    const item=entries(region).find(i=>i.key===key);if(!item)return;
    const keys=current().map(i=>i.key);if(!keys.includes(key))keys.push(key);
    const pos=placements(cfg.island,region,keys);
    // Переносимый блок получает приоритет, остальные занимают ближайшие свободные ячейки.
    const target={...pos[key],...patch};
    target.w=Math.max(1,Math.min(COLS,target.w));target.h=Math.max(1,Math.min(8,target.h));
    target.x=Math.max(0,Math.min(COLS-target.w,target.x));target.y=Math.max(0,Math.min(40,target.y));
    const ordered={[key]:target,...Object.fromEntries(Object.entries(pos).filter(([k])=>k!==key))};
    const I={...cfg.island,visual_layout:{...cfg.island?.visual_layout,[region]:ordered}};
    const packed=placements(I,region,[key,...keys.filter(k=>k!==key)]);
    selected=key;
    save({['island.visual_layout.'+region]:packed,...(add?enable(item,true):{})});
  };
  const sample=item=>{
    const content=el('div',{class:'editor-sample'},iconNode(GLYPHS[item.key]||'app',region==='capsule'?18:22));
    if(SAMPLE[item.key])content.append(el('span',{},t(SAMPLE[item.key])));
    else if(item.key==='p_player') content.append(el('div',{},el('b',{},t('Название трека')),el('div',{class:'mut small'},t('Исполнитель')),el('div',{class:'row'},iconNode('prev'),iconNode('play',24),iconNode('next'))));
    else if(item.key==='music_controls') content.append(iconNode('prev'),iconNode('play'),iconNode('next'));
    else if(item.key==='p_progress'||item.key==='music_progress')content.append(el('span',{class:'editor-progress'}));
    else if(region!=='capsule'&&!item.key.startsWith('h_'))content.append(el('span',{},t(item.label)));
    return content;
  };
  const pointer=(event,key,resize=false,add=false)=>{
    if(busy||event.button!==0)return;
    event.preventDefault();selected=key;
    const stage=body.querySelector('.editor-grid'), rect=stage.getBoundingClientRect();
    const cell=(rect.width+8)/COLS,row=region==='capsule'||region==='header'?44:56;
    const pos=placements(cfg.island,region,[...current().map(i=>i.key),...(add?[key]:[])])[key];
    const startX=event.clientX,startY=event.clientY;
    const ghost=el('div',{class:'editor-drag-ghost'},t(entries(region).find(i=>i.key===key).label));document.body.append(ghost);
    const marker=el('div',{class:'editor-drop-preview'});stage.append(marker);
    const update=e=>{
      ghost.style.left=e.clientX+12+'px';ghost.style.top=e.clientY+12+'px';
      const w=resize?Math.max(1,Math.min(COLS,pos.w+Math.round((e.clientX-startX)/cell))):pos.w;
      const h=resize?Math.max(1,Math.min(8,pos.h+Math.round((e.clientY-startY)/row))):pos.h;
      position(marker,{x:resize?pos.x:Math.max(0,Math.min(COLS-w,Math.floor((e.clientX-rect.left)/cell))),y:resize?pos.y:Math.max(0,Math.floor((e.clientY-rect.top)/row)),w,h});
    };update(event);
    drag={cancel:()=>{ghost.remove();marker.remove();document.removeEventListener('pointermove',update);document.removeEventListener('pointerup',finish);document.removeEventListener('keydown',escape);document.removeEventListener('pointercancel',cancel);}};
    const cancel=()=>{drag?.cancel();drag=null;};
    const escape=e=>{if(e.key==='Escape'){drag.cancel();drag=null;}};
    const finish=e=>{
      drag.cancel();drag=null;
      if(Math.hypot(e.clientX-startX,e.clientY-startY)<5&&!resize){if(add)move(key,{},true);else draw();return;}
      if(e.clientX<rect.left||e.clientX>rect.right||e.clientY<rect.top||e.clientY>rect.bottom){draw();return;}
      move(key,resize?{w:pos.w+Math.round((e.clientX-startX)/cell),h:pos.h+Math.round((e.clientY-startY)/row)}:
        {x:Math.floor((e.clientX-rect.left)/cell),y:Math.floor((e.clientY-rect.top)/row)},add);
    };
    document.addEventListener('pointermove',update);document.addEventListener('pointerup',finish,{once:true});document.addEventListener('keydown',escape);document.addEventListener('pointercancel',cancel,{once:true});
  };
  const inspect=()=>{
    const item=entries(region).find(i=>i.key===selected&&shown(cfg.island,i));
    inspector.replaceChildren();if(!item){inspector.append(el('span',{class:'mut small'},t('Выберите блок на макете')));return;}
    const p=placements(cfg.island,region,current().map(i=>i.key))[item.key];
    inspector.append(el('b',{},t(item.label)));
    for(const [key,label,max] of [['x','Колонка',COLS-p.w],['y','Строка',40],['w','Ширина',COLS],['h','Высота',8]]){
      const input=el('input',{class:'field small',type:'number',min:key==='x'||key==='y'?0:1,max,value:p[key],'aria-label':t(label)});
      input.onchange=()=>{const value=Number(input.value);if(Number.isFinite(value))move(item.key,{[key]:Math.round(value)});};
      inspector.append(el('label',{},el('span',{class:'mut small'},t(label)),input));
    }
    const last=item.kind==='tab'&&TAB_LIST.filter(([id])=>!cfg.island?.hidden_tabs?.includes(id)).length<=1;
    inspector.append(el('button',{class:'btn ghost',type:'button',disabled:last,onclick:()=>save(enable(item,false))},t('Убрать с островка')));
  };
  const draw=()=>{
    const previousScroll=body.querySelector('.editor-scroll');
    if(previousScroll)scrollPositions[renderedRegion]=[previousScroll.scrollLeft,previousScroll.scrollTop];
    renderedRegion=region;
    nav.querySelectorAll('button').forEach(b=>{const on=b.dataset.region===region;b.classList.toggle('on',on);b.setAttribute('aria-pressed',String(on));});
    const list=current(), pos=placements(cfg.island,region,list.map(i=>i.key));
    const height=Math.max(1,...Object.values(pos).map(p=>p.y+p.h));
    const grid=el('div',{class:'island-grid editor-grid region-'+region,'data-editor-region':region,style:{minHeight:(height*(region==='capsule'||region==='header'?44:56)-8)+'px'}});
    for(const item of list){
      const node=el('div',{class:'island-cell editor-cell'+(selected===item.key?' selected':''),tabindex:'0',role:'group','aria-label':t(item.label),title:t(item.label),'data-editor-key':item.key,
        onpointerdown:e=>pointer(e,item.key),onkeydown:e=>{
          if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();selected=item.key;const p=pos[item.key];const dx=e.key==='ArrowLeft'?-1:e.key==='ArrowRight'?1:0,dy=e.key==='ArrowUp'?-1:e.key==='ArrowDown'?1:0;move(item.key,e.shiftKey?{w:p.w+dx,h:p.h+dy}:{x:p.x+dx,y:p.y+dy});}
          if(e.key==='Enter'||e.key===' '){e.preventDefault();selected=item.key;draw();}
        }}, sample(item),el('span',{class:'editor-cell-name'},t(item.label)));
      const handle=el('button',{class:'editor-resize',type:'button',title:t('Потяните для изменения размера'),'aria-label':t('Изменить размер')+' · '+t(item.label),onpointerdown:e=>{e.stopPropagation();pointer(e,item.key,true);}},'◢');
      node.append(handle);position(node,pos[item.key]);grid.append(node);
    }
    const tray=el('div',{class:'editor-palette'});
    for(const item of entries(region).filter(i=>!shown(cfg.island,i)))tray.append(el('button',{class:'btn ghost',type:'button','data-palette-key':item.key,onpointerdown:e=>pointer(e,item.key,false,true),onclick:e=>{if(e.detail===0)move(item.key,{},true);}},iconNode(GLYPHS[item.key]||'app'),t(item.label)));
    const shell=el('div',{class:'editor-island '+(['capsule','music','activity'].includes(region)?'compact':'expanded')});
    if(['now','tools','agent'].includes(region))shell.append(el('div',{class:'row editor-panel-header'},
      iconNode('pip',24),...TAB_LIST.map(([id,label])=>el('span',{class:'tab'+(id===region?' on':'')},t(label))),
      el('span',{class:'grow'}),iconNode('edit',20),iconNode('home',20),iconNode('expand',20)));
    shell.append(grid);
    body.replaceChildren(el('div',{class:'editor-scroll'},shell),
      el('h4',{},t('Доступные элементы')),tray.childNodes.length?tray:el('p',{class:'mut small'},t('Все элементы уже на островке')));
    const scroll=body.querySelector('.editor-scroll'),savedScroll=scrollPositions[region]||[0,0];
    scroll.scrollLeft=savedScroll[0];scroll.scrollTop=savedScroll[1];
    inspect();
  };
  for(const [id,label] of REGIONS)nav.append(el('button',{class:'settings-mode',type:'button','data-region':id,onclick:()=>{if(!busy){region=id;selected=null;draw();}}},t(label)));
  box.append(el('h3',{},t('Настройка островка')),el('p',{class:'mut small'},t('Перетащите блок на макете или из списка. Потяните за угол, чтобы изменить размер. Стрелки — перемещение, Shift + стрелки — размер.')),nav,body,inspector,
    el('div',{class:'island-settings-footer'},status,el('button',{class:'btn ghost',type:'button',onclick:()=>save({...RESET_VALUES,'island.visual_layout':{},'island.layout':{},'island.panel_layout':{}})},t('Вернуть стандартный набор'))));
  draw();return box;
}
