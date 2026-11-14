// Общая сетка редактора и настоящего островка. Координаты — целые ячейки, зазор всегда 8px.
import { PARTS, TAB_LIST, TOOL_LIST, partOn, listOn } from './parts.js';
import { el } from './shell.js';

export const COLS = 12;
export const REGIONS = [
  ['capsule', 'Капсула'],
  ['header', 'Заголовок панели'], ['now', 'Панель: «Сейчас»'],
  ['tools', 'Панель: «Инструменты»'],
];
export const GLYPHS = {
  orb:'pip', clock:'clock', battery:'battery', battery_percent:'battery', weather:'sun', privacy:'mic', caps:'keyboard', layout:'keyboard', vpn:'shield', core_status:'pip', volume_osd:'volume', load:'gauge', focus:'ring', event:'calendar', downloads:'download', ticker:'dollar', sleep:'hourglass', coreinfo:'chipic',
  music_cover:'music', music_title:'music', music_progress:'play', music_eq:'mixer', music_controls:'play', lyrics:'note', agent_steps:'tools', agent_stop:'stop', reply_text:'chat', mic_meter:'mic',
  p_player:'music', p_lyrics:'note', p_progress:'play', p_eq:'mixer', w_weather:'sun', w_clock:'clock', w_battery:'battery', w_timers:'timer', w_days:'sun', w_agenda:'calendar', w_sysmon:'gauge', w_downloads:'download', w_mixer:'mixer', w_note:'note', w_health:'eye', obs:'rec', pa_model:'chipic', pa_steps:'tools', pa_reply:'chat', pa_input:'mic', h_orb:'pip', h_edit:'edit', h_pin:'pin', h_open:'open',
  shot_area:'crop', shot_screen:'monitor', ocr:'ocr', qr:'qr', color:'pip', record:'rec', record_area:'shot', dictation:'keyboard', note:'note', timer:'timer',
};
export function entries(region) {
  const title = region==='agent'?'Панель: «Агент»':REGIONS.find(([id]) => id === region)?.[1];
  const items = PARTS.filter(([key, , group]) => group === title && key !== 'p_progress').map(([key, label]) => ({key, label, kind:'part'}));
  if (region === 'header') items.splice(1, 0, ...TAB_LIST.map(([id,label]) => ({key:'tab_'+id,label,kind:'tab',id})));
  if (region === 'tools') items.unshift(...TOOL_LIST.map(([id,label]) => ({key:id,label,kind:'tool',id})));
  return items;
}
export function shown(I, item) {
  return item.kind === 'tab' ? listOn(I,'tabs',item.id) : item.kind === 'tool' ? listOn(I,'tools',item.id) : partOn(I,item.key);
}
export function defaultSize(region, key) {
  if (region === 'header') return {w:key.startsWith('tab_')?2:1,h:1};
  if (region === 'capsule') return {w:['clock','battery_percent','weather','layout','caps'].includes(key)?2: key === 'orb' || key === 'battery' || key === 'vpn' || key === 'privacy' ? 1 : 3,h:1};
  if (region === 'music' || region === 'activity') return {w:['music_cover','music_eq','agent_stop','mic_meter'].includes(key)?2:6,h:1};
  if (region === 'tools') return key === 'obs' ? {w:6,h:3} : ['note','timer'].includes(key) ? {w:6,h:2} : {w:3,h:3};
  return {w:['p_player','w_days','w_agenda','w_mixer','w_sysmon','w_downloads','pa_input','pa_reply'].includes(key)?12:6,h:key==='p_player'?4:2};
}
export function minimumSize(region,key) {
  if(region==='tools')return key==='obs'?{w:6,h:3}:{w:1,h:2};
  if(region==='header')return {w:key.startsWith('tab_')?2:1,h:1};
  if(region==='now')return {w:['p_player','w_days','w_agenda','w_mixer','w_downloads'].includes(key)?12:6,h:key==='p_player'?4:2};
  return {w:1,h:1};
}
// Тот же flex-контейнер для редактора и настоящей капсулы. Равные плечи сохраняют точный центр.
export function arrangeCapsule(grid,I) {
  const cells=[...grid.querySelectorAll('[data-grid-key],[data-editor-key]')];
  const key=n=>n.dataset.gridKey||n.dataset.editorKey;
  const saved=I?.visual_layout?.capsule||{};
  cells.sort((a,b)=>(Number(saved[key(a)]?.x)||0)-(Number(saved[key(b)]?.x)||0));
  const center=cells.find(n=>key(n)===I?.capsule_center);
  grid.style.gridTemplateColumns='';grid.style.width='';
  for(const node of cells){node.style.gridColumn='';node.style.gridRow='';}
  if(!center){grid.replaceChildren(...cells);return;}
  const i=cells.indexOf(center);
  grid.replaceChildren(el('div',{class:'capsule-wing'},cells.slice(0,i)),center,el('div',{class:'capsule-wing right'},cells.slice(i+1)));
  // Explicit equal widths work inside a shrink-to-fit capsule too.
  const measure=()=>{
    if(!grid.isConnected)return;
    const wings=grid.querySelectorAll('.capsule-wing');
    const width=Math.max(...[...wings].map(n=>n.scrollWidth));
    wings.forEach(n=>n.style.width=width+'px');
  };
  if(grid.isConnected)measure();else requestAnimationFrame(measure);
}
const integer = (v, fallback, max) => Number.isFinite(Number(v)) ? Math.max(1,Math.min(max,Math.round(Number(v)))) : fallback;
// Сначала размещаются сохранённые блоки, затем новые. Коллизии раздвигают блоки, не перекрывая их.
export function placements(I, region, keys) {
  const saved = I?.visual_layout?.[region] || {};
  const occupied = new Set(), result = {};
  if(region==='capsule') {
    // Старые многострочные координаты превращаются в порядок в единственном ряду.
    const ordered=[...keys].sort((a,b)=>(Number(saved[a]?.y)||0)-(Number(saved[b]?.y)||0)||(Number(saved[a]?.x)||0)-(Number(saved[b]?.x)||0));
    let x=0;
    for(const key of ordered){const w=integer(saved[key]?.w,defaultSize(region,key).w,COLS);result[key]={x,y:0,w,h:1};x+=w;}
    return result;
  }
  const fits = (x,y,w,h) => {
    for(let a=x;a<x+w;a++) for(let b=y;b<y+h;b++) if(occupied.has(a+','+b)) return false;
    return true;
  };
  const ordered = [...keys.filter(k=>saved[k]), ...keys.filter(k=>!saved[k])];
  for (const key of ordered) {
    const d=defaultSize(region,key), p=saved[key]||{};
    const min=minimumSize(region,key);
    const w=Math.max(min.w,integer(p.w,d.w,COLS)),h=Math.max(min.h,integer(p.h,d.h,8));
    let x=Math.max(0,Math.min(COLS-w,Math.round(Number(p.x)||0)));
    let y=Math.max(0,Math.min(200,Math.round(Number(p.y)||0)));
    if(!saved[key] || !fits(x,y,w,h)) {
      x=0;y=0;
      while(!fits(x,y,w,h)) { x++;if(x+w>COLS){x=0;y++;} }
    }
    result[key]={x,y,w,h};
    for(let a=x;a<x+w;a++) for(let b=y;b<y+h;b++) occupied.add(a+','+b);
  }
  return result;
}
export function position(node,p) {
  node.style.gridColumn=`${p.x+1} / span ${p.w}`;
  node.style.gridRow=`${p.y+1} / span ${p.h}`;
}
export function gridRegion(I, region, nodes) {
  const live=nodes.filter(([,node])=>node!=null);
  const pos=placements(I,region,live.map(([key])=>key));
  const grid=el('div',{class:'island-grid region-'+region,'data-grid-region':region});

  if(region==='header')live.sort(([a],[b])=>(I?.visual_layout?.header?.[a]?.x??pos[a].x)-(I?.visual_layout?.header?.[b]?.x??pos[b].x));
  for(const [key,node] of live) {
    const cell=el('div',{class:'island-cell','data-grid-key':key},node);
    if(region!=='capsule')position(cell,pos[key]);
    if(region==='tools'){cell.classList.toggle('icon-only',pos[key].w<3);cell.classList.toggle('horizontal',pos[key].w>=3&&pos[key].h<3);cell.title=entries(region).find(i=>i.key===key)?.label||key;node.setAttribute('aria-label',cell.title);}
    grid.append(cell);
  }
  if(region==='capsule')arrangeCapsule(grid,I);
  return grid;
}
