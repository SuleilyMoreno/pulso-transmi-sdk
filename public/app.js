let data, seriesChart, errorChart, performanceChart, map;
const $=id=>document.getElementById(id);
function pct(v){return v==null?'—':`${(v*100).toFixed(1)}%`}
function rate(v){return v==null?'—':`${(v*100).toFixed(1)}%`}
async function load(){ $('refresh').disabled=true; try{const r=await fetch('/api/dashboard'); data=await r.json(); if(data.error) throw Error(data.error); render()}catch(e){alert(e.message)}finally{$('refresh').disabled=false}}
function render(){
 const m=data.metrics;
 $('accuracy').textContent=pct(m.accuracy_cumulative ?? m.accuracy);
 $('last6Accuracy').textContent=pct(m.accuracy_last6);
 $('last6Drift').textContent=rate(m.drift_rate_last6);
 $('model').textContent=data.model_version||'—';
 $('updated').textContent=`Actualizado: ${new Date(data.updated_at).toLocaleString()}`;
 const ids=Object.keys(data.series); $('station').innerHTML=ids.map(x=>`<option>${x}</option>`).join(''); $('station').onchange=drawSeries;
 drawSeries(); drawErrors(); drawPerformance(); drawMap();
 const driftText=m.concept_drift==null?'Sin evaluación':(m.concept_drift?'Detectado':'No detectado');
 $('drift').innerHTML=`<span class="pill">Ciclos evaluados: <b>${m.cycles_evaluated||0}</b></span><span class="pill">Drift acumulado: <b class="${m.drift_rate_cumulative?'warn':'good'}">${rate(m.drift_rate_cumulative)}</b></span><span class="pill">Drift últimos 6: <b class="${m.drift_rate_last6?'warn':'good'}">${rate(m.drift_rate_last6)}</b></span><span class="pill">Último: <b class="${m.concept_drift?'warn':'good'}">${driftText}</b></span><span class="pill">Reentrenado: <b>${m.retrained?'Sí':'No'}</b></span>`;
 $('leaderboard').innerHTML=`<div class="empty">Accuracy acumulada: <b>${pct(m.accuracy_cumulative ?? m.accuracy)}</b><br>Accuracy últimos 6 ciclos: <b>${pct(m.accuracy_last6)}</b><br>Predicciones evaluadas en el último ciclo: ${m.matched||0}</div>`;
}

function drawSeries(){const rows=data.series[$('station').value]||[];if(seriesChart)seriesChart.destroy();seriesChart=new Chart($('series'),{type:'line',data:{labels:rows.map(x=>new Date(x.observed_at).toLocaleTimeString()),datasets:[{label:'Demanda',data:rows.map(x=>x.demand),borderColor:'#65d6b2',backgroundColor:'#65d6b233',fill:true,tension:.25}]},options:{responsive:true,scales:{y:{beginAtZero:true}}}})}
function drawPerformance(){
 if(performanceChart) performanceChart.destroy();
 const rows=data.cycle_history||[];
 performanceChart=new Chart($('performance'),{data:{labels:rows.map(x=>x.cycle_id),datasets:[
  {type:'line',label:'Accuracy',data:rows.map(x=>x.accuracy*100),borderColor:'#65d6b2',backgroundColor:'#65d6b233',tension:.25,yAxisID:'y',pointRadius:4},
  {type:'line',label:'Umbral',data:rows.map(x=>x.threshold*100),borderColor:'#ffd166',borderDash:[6,5],pointRadius:0,yAxisID:'y'},
  {type:'bar',label:'Drift detectado',data:rows.map(x=>x.drift_detected?100:0),backgroundColor:'#ff8c69aa',yAxisID:'drift'}
 ]},options:{responsive:true,interaction:{mode:'index',intersect:false},scales:{y:{min:0,max:100,title:{display:true,text:'Accuracy (%)'}},drift:{min:0,max:100,display:false}},plugins:{legend:{labels:{color:'#e8eef8'}},tooltip:{callbacks:{afterBody:(items)=>{const row=rows[items[0].dataIndex];return `
Ciclo: ${row.cycle_id}
Reentrenado: ${row.retrained?'Sí':'No'}
Valores: ${row.matched_predictions}`}}}}}});
}

function drawErrors(){if(errorChart)errorChart.destroy();errorChart=new Chart($('errors'),{type:'bar',data:{labels:['≤-100','-100 a -25','-25 a 25','25 a 100','>100'],datasets:[{label:'Errores',data:[-100,-25,0,25,100].map((_,i)=>data.errors.filter(x=>[x<=-100,x>-100&&x<=-25,x>-25&&x<=25,x>25&&x<=100,x>100][i]).length),backgroundColor:'#ff8c69'}]},options:{plugins:{legend:{display:false}}}})}
function drawMap(){if(map)map.remove();map=L.map('map').setView([4.65,-74.1],11);L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{attribution:'© OpenStreetMap'}).addTo(map);data.stations.filter(s=>s.latitude&&s.longitude).forEach(s=>L.marker([s.latitude,s.longitude]).addTo(map).bindPopup(`<b>${s.station_name||s.station_id}</b><br>ID: ${s.station_id}`))}
$('refresh').onclick=load;load();
