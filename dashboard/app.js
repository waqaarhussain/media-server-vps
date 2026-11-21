const ports={radarr:7878,prowlarr:9696,qbittorrent:8080,files:8082};
const host=location.hostname.includes(':')?`[${location.hostname}]`:location.hostname;
document.querySelectorAll('[data-app]').forEach(link=>{const app=link.dataset.app;link.href=`${location.protocol}//${host}:${ports[app]}${app==='prowlarr'?'/prowlarr':''}`;link.target='_blank';link.rel='noopener';});
document.getElementById('host').textContent=location.hostname;
document.getElementById('ftp-host').textContent=location.hostname;
const dialog=document.getElementById('ftp-dialog');
document.getElementById('ftp-open').addEventListener('click',()=>dialog.showModal());
document.getElementById('ftp-close').addEventListener('click',()=>dialog.close());
const bytes=n=>new Intl.NumberFormat('en-GB',{maximumFractionDigits:1}).format(n/1e12)+' TB';
function clock(){document.getElementById('clock').textContent=new Date().toLocaleString('en-GB',{weekday:'short',hour:'2-digit',minute:'2-digit'});}
clock();setInterval(clock,30000);
async function refresh(){try{const response=await fetch('/api/status',{cache:'no-store'});if(!response.ok)throw Error('unavailable');const data=await response.json();
document.getElementById('cpu').textContent=Math.round(data.cpu)+'%';document.getElementById('ram').textContent=Math.round(data.memory)+'%';
document.getElementById('cpu-bar').style.width=data.cpu+'%';document.getElementById('ram-bar').style.width=data.memory+'%';
document.getElementById('free').textContent=bytes(data.disk.free)+' free';document.getElementById('capacity').textContent=bytes(data.disk.used)+' used of '+bytes(data.disk.total);
document.getElementById('disk-bar').style.width=(data.disk.used/data.disk.total*100)+'%';document.getElementById('pool').textContent=data.pool;
const hours=Math.floor(data.uptime/3600);document.getElementById('uptime').textContent=`Up ${Math.floor(hours/24)}d ${hours%24}h`;
Object.entries(data.services).forEach(([name,up])=>{const target=document.querySelector(`[data-status="${name}"]`);if(target){target.className='status '+(up?'up':'down');target.textContent=up?'Listening':'Not listening';}});
document.getElementById('live-note').textContent='Updated just now';
}catch(error){document.getElementById('live-note').textContent='Status unavailable';document.querySelectorAll('[data-status]').forEach(el=>{el.className='status';el.textContent='Unknown';});}}
refresh();setInterval(refresh,10000);
