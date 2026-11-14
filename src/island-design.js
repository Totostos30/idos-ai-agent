// Only presentation settings travel with a shared design.
const OBJECTS=['parts','visual_layout','panel_size','layout','panel_layout','waves'];
const STRINGS=['capsule_center','battery_display','character','music_pill'];
const NUMBERS=['scale','opacity_collapsed','opacity_open'];
const BOOLS=['show_battery','show_weather','lyrics','eq_edge'];
const LISTS=['hidden_tabs','hidden_tools'];
const KEYS=[...OBJECTS,...STRINGS,...NUMBERS,...BOOLS,...LISTS];
function validate(value,depth=0){
 if(depth>10)throw Error('Слишком сложная структура дизайна');
 if(value===null||typeof value==='boolean'||typeof value==='string')return;
 if(typeof value==='number'&&Number.isFinite(value))return;
 if(typeof value!=='object')throw Error('Некорректное значение дизайна');
 for(const [key,item] of Object.entries(value)){
  if(['__proto__','constructor','prototype'].includes(key))throw Error('Недопустимое поле дизайна');
  validate(item,depth+1);
 }
}
export function exportDesign(island={}){
 const data={};for(const key of KEYS)if(island[key]!==undefined)data[key]=island[key];
 return JSON.stringify({type:'idos-island-design',schemaVersion:1,island:data},null,2);
}
export function importDesign(text){
 if(text.length>262144)throw Error('Файл дизайна слишком большой');
 const data=JSON.parse(text);validate(data);
 if(data?.type!=='idos-island-design'||data.schemaVersion!==1||!data.island||Array.isArray(data.island))throw Error('Неизвестный формат дизайна');
 const values={};
 for(const key of Object.keys(data.island)){
  const value=data.island[key];
  if(!KEYS.includes(key))throw Error('Неизвестная настройка: '+key);
  const valid=OBJECTS.includes(key)?value&&typeof value==='object'&&!Array.isArray(value):LISTS.includes(key)?Array.isArray(value)&&value.every(v=>typeof v==='string'):STRINGS.includes(key)?typeof value==='string':NUMBERS.includes(key)?typeof value==='number':typeof value==='boolean';
  if(!valid)throw Error('Некорректная настройка: '+key);
 }
 // Replace the whole design, so missing fields do not inherit another person's layout.
 for(const key of KEYS)values['island.'+key]=data.island[key]??(OBJECTS.includes(key)?{}:LISTS.includes(key)?[]:STRINGS.includes(key)?({battery_display:'icon',character:'liquid',music_pill:'wide'}[key]||''):NUMBERS.includes(key)?1:true);
 return values;
}
