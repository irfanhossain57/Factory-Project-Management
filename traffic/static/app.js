'use strict';
const $ = id => document.getElementById(id);
const directions = ['NORTH','SOUTH','EAST','WEST'];
let state = null, lastEvent = null, refreshing = false;
const sequence = () => {const n=Math.max(Date.now()*100,Number(localStorage.getItem('trafficSequence')||0)+1);localStorage.setItem('trafficSequence',String(n));return n;};
const uid = () => crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
function el(tag, text, className) {const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(className)n.className=className;return n;}
function message(text, error=false) {$('message').textContent=text;$('message').className=error?'error':'';}
async function api(path, body) {const r=await fetch(path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(5000)});const data=await r.json();if(!r.ok)throw Error(data.error||`HTTP ${r.status}`);return data;}
async function action(fn) {try{await fn();message('Request accepted. Displaying backend state.');await refresh();}catch(e){message(e.message,true);}}
const jid = () => encodeURIComponent($('junction').value);
const cmd = command => api(`/api/junctions/${jid()}/commands`,command);
for(const id of ['direction','manualDirection','deviceDirection']) for(const d of directions)$(id).append(el('option',d));
for(const d of directions){const n=el('div',undefined,`signal ${d}`);n.id=`signal-${d}`;$('intersection').append(n);}
function render(s, history){
 state=s;$('mode').textContent=s.mode;$('phase').textContent=`${s.phase||'NONE'} / ${s.stage}`;$('controller').textContent=s.controller_status;$('total').textContent=Object.values(s.queues||{}).reduce((a,b)=>a+b,0);document.querySelector('.center').textContent=s.junction_id;
 $('alerts').replaceChildren(...(s.alerts||[]).map(a=>el('div',a,'alert')));
 for(const d of directions){const n=$(`signal-${d}`);n.replaceChildren();const title=el('div',d);const actual=el('div');actual.append(el('span',undefined,`lamp ${s.actual_signals[d]}`),document.createTextNode(s.actual_signals[d]));n.append(title,actual,el('small',`Requested: ${s.desired_signals[d]}`),el('small',`Queue: ${s.queues[d]}`));}
 $('timer').textContent=s.deadline?`Current stage deadline: ${new Date(s.deadline*1000).toLocaleTimeString()}. Scheduling remains server-controlled.`:'Waiting for controller confirmation or recovery.';
 $('emergencies').textContent=(s.emergencies||[]).length?`EMERGENCY: ${s.emergencies.map(v=>v.direction+' / '+v.vehicle_id).join(', ')}`:'No active emergency priority';
 $('vehicles').replaceChildren();for(const v of Object.values(s.vehicles||{})){const tr=el('tr');for(const val of [v.vehicle_id,v.direction,v.vehicle_type,`${Math.max(0,Math.floor(Date.now()/1000-v.arrived_at))}s`])tr.append(el('td',val));const td=el('td');const b=el('button','Clear');b.onclick=()=>action(()=>sendVehicle('VEHICLE_CLEARED',v));td.append(b);tr.append(td);$('vehicles').append(tr);}if(!Object.keys(s.vehicles||{}).length){const tr=el('tr');const td=el('td','No vehicles waiting');td.colSpan=5;tr.append(td);$('vehicles').append(tr);}
 $('pending').replaceChildren();for(const c of Object.values(s.pending_commands||{})){const n=el('div',undefined,'command');n.append(el('div',`${c.direction} → ${c.requested_state} · epoch ${c.epoch}`),el('small',c.command_id));for(const status of ['ACK','NACK']){const b=el('button',status);b.onclick=()=>action(()=>ack(c,status));n.append(b);}$('pending').append(n);}if(!Object.keys(s.pending_commands||{}).length)$('pending').textContent='No pending commands.';
 $('history').replaceChildren(...history.map(h=>{const n=el('details',undefined,'event');const title=el('summary');title.append(el('time',new Date(h.timestamp*1000).toLocaleTimeString()),document.createTextNode(h.event_type));n.append(title,el('pre',JSON.stringify(h,null,2)));return n;}));
}
async function refresh(){if(refreshing)return;refreshing=true;try{const selected=$('junction').value;const all=await api('/api/junctions');const ids=all.map(s=>s.junction_id);if(ids.join('|')!==Array.from($('junction').options).map(o=>o.value).join('|')){$('junction').replaceChildren(...ids.map(i=>el('option',i)));if(ids.includes(selected))$('junction').value=selected;}const [s,h]=await Promise.all([api(`/api/junctions/${jid()}/status`),api(`/api/junctions/${jid()}/history?limit=60`)]);render(s,h);if($('message').textContent==='Connecting…')message('Connected. Confirm all RED, then select Recover to start.');}catch(e){message(`Refresh failed: ${e.message}. Displayed data may be stale.`,true);}finally{refreshing=false;}}
async function sendVehicle(type,v){const event={event_id:uid(),junction_id:$('junction').value,direction:v.direction,event_type:type,vehicle_id:v.vehicle_id,sequence_no:sequence(),timestamp:new Date().toISOString()};if(type==='VEHICLE_ARRIVED')event.vehicle_type=v.vehicle_type;lastEvent=event;return api('/api/sensor-events',event);}
const ack=(c,status)=>api('/api/controller-events',{junction_id:c.junction_id,command_id:c.command_id,status,actual_state:c.requested_state});
$('vehicleForm').onsubmit=e=>{e.preventDefault();action(()=>sendVehicle('VEHICLE_ARRIVED',{direction:$('direction').value,vehicle_type:$('vehicleType').value,vehicle_id:$('vehicleId').value.trim()||`VH-${uid().slice(0,8)}`}));};
$('duplicate').onclick=()=>action(()=>{if(!lastEvent)throw Error('Submit a vehicle event first');return api('/api/sensor-events',lastEvent);});
$('manual').onclick=()=>action(()=>cmd({command:'MANUAL_GREEN_REQUEST',direction:$('manualDirection').value}));
for(const [id,name] of [['automatic','RETURN_TO_AUTOMATIC'],['recover','RECOVER'],['reconcile','RECONCILE']])$(id).onclick=()=>action(()=>cmd({command:name}));
$('ackAll').onclick=()=>action(async()=>{const fresh=await api(`/api/junctions/${jid()}/status`);for(const c of Object.values(fresh.pending_commands))await ack(c,'ACK');});
$('device').onclick=()=>action(()=>api('/api/device-events',{event_id:uid(),junction_id:$('junction').value,device_type:$('deviceType').value,direction:$('deviceDirection').value,status:$('deviceStatus').value,timestamp:new Date().toISOString()}));
$('junction').onchange=refresh;refresh();setInterval(refresh,1000);
