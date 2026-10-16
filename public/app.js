let data, seriesChart, errorChart, performanceChart, map;
const $ = id => document.getElementById(id);

function pct(v)      { return v == null ? '—' : `${parseFloat(v).toFixed(1)}%`; }
function dateText(v) {
  if (!v) return 'Sin datos';
  return new Date(v).toLocaleString('es-CO', { dateStyle: 'medium', timeStyle: 'short' });
}
function dateOnly(v) {
  if (!v) return '—';
  return new Date(v).toLocaleDateString('es-CO', { dateStyle: 'medium' });
}
function timeAgo(v) {
  if (!v) return '';
  const diff = Date.now() - new Date(v).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1)  return 'justo ahora';
  if (mins < 60) return `hace ${mins} min`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24)  return `hace ${hrs}h`;
  return `hace ${Math.floor(hrs / 24)}d`;
}

async function load() {
  $('refresh').disabled = true;
  try {
    const r = await fetch('/api/dashboard');
    data = await r.json();
    if (data.error) throw Error(data.error);
    render();
  } catch (e) {
    alert('Error cargando dashboard: ' + e.message);
  } finally {
    $('refresh').disabled = false;
  }
}

function render() {
  // ── Datos del estudiante (leaderboard_me) ──
  const me = data.leaderboard_me || {};
  const runs = data.runs || [];
  const lb = (data.leaderboard && data.leaderboard.data) ? data.leaderboard.data : [];
  const lbHistory = data.leaderboard_history || [];
  const lastRun = runs[0] || {};
  const generatedAt = data.generated_at;

  // ── KPI cards ──
  $('accuracy').textContent = pct(me.accuracy);
  $('accuracyDate').textContent = me.calculated_at
    ? `Calculado ${timeAgo(me.calculated_at)}`
    : '';

  $('last6Accuracy').textContent = me.rank ? `Puesto #${me.rank}` : '—';
  $('last6Date').textContent = `de ${lb.length} estudiantes`;

  $('last6Drift').textContent = me.raw_wape != null
    ? `WAPE: ${parseFloat(me.raw_wape).toFixed(4)}`
    : '—';
  $('driftDate').textContent = me.coverage != null
    ? `Cobertura: ${pct(me.coverage * 100)}`
    : '';

  // Modelo activo = último run exitoso
  const lastSucceeded = runs.find(r => r.status === 'succeeded');
  $('model').textContent = lastSucceeded
    ? (lastSucceeded.model_version || '—').split('-').slice(0,3).join('-')
    : '—';
  $('modelDate').textContent = lastSucceeded
    ? `Entrenado ${timeAgo(lastSucceeded.started_at)}`
    : '';

  // ── Footer ──
  $('updated').textContent = `Actualizado: ${generatedAt ? dateText(generatedAt) : new Date().toLocaleString('es-CO')}`;

  // ── Panel de fechas de los datos ──
  const myHistory = lbHistory.filter(h => h.student === me.display_name);
  const histDates = myHistory.map(h => h.measured_at).sort();
  const runDates  = runs.map(r => r.started_at).sort();

  const dateCards = [
    {
      icon: '🏆', label: 'Accuracy del estudiante',
      from: histDates[0], to: histDates[histDates.length - 1],
      count: myHistory.length, unit: 'registros históricos'
    },
    {
      icon: '🤖', label: 'Entrenamientos ejecutados',
      from: runDates[0], to: runDates[runDates.length - 1],
      count: runs.length, unit: 'runs'
    },
    {
      icon: '📊', label: 'Predicciones enviadas',
      from: null, to: null,
      count: data.prediction_count || 0, unit: 'predicciones totales'
    },
    {
      icon: '📈', label: 'Leaderboard competencia',
      from: lb[0]?.calculated_at || null,
      to: lb[0]?.calculated_at || null,
      count: data.leaderboard?.resolved_cycles || lb.length,
      unit: 'ciclos resueltos'
    },
  ];

  $('dateRanges').innerHTML = dateCards.map(c => `
    <div class="date-card">
      <div class="date-card-header">
        <span class="date-icon">${c.icon}</span>
        <span class="date-label">${c.label}</span>
        <span class="date-count ${c.count > 0 ? 'good' : 'warn'}">${c.count} ${c.unit}</span>
      </div>
      <div class="date-range">
        ${c.from
          ? `<span class="date-from"><b>Desde:</b> ${dateText(c.from)}</span>
             <span class="date-to"><b>Hasta:</b> ${dateText(c.to)}</span>`
          : `<span class="date-from muted">Sin rango de fechas disponible</span>`}
      </div>
    </div>`
  ).join('');

  // ── Gráfico de rendimiento histórico (leaderboard_history del estudiante) ──
  drawPerformance(myHistory);

  // ── Gráfico de runs / errores ──
  drawRuns(runs);

  // ── Mapa (estaciones si existen, si no placeholder) ──
  drawMap();

  // ── Drift / estado ──
  const conn = data.connection || {};
  $('drift').innerHTML = `
    <span class="pill">API: <b class="${conn.pulso_key_configured ? 'good' : 'warn'}">${conn.pulso_key_configured ? 'Conectada ✓' : 'Sin clave'}</b></span>
    <span class="pill">Supabase: <b class="${conn.supabase_configured ? 'good' : 'warn'}">${conn.supabase_configured ? 'Conectado ✓' : 'Sin configurar'}</b></span>
    <span class="pill">Leaderboard: <b class="${conn.leaderboard_loaded ? 'good' : 'warn'}">${conn.leaderboard_loaded ? 'Cargado ✓' : 'No disponible'}</b></span>
    <span class="pill">Predicciones evaluadas: <b>${data.evaluated_count ?? '—'}</b></span>
    <span class="pill">Predicciones enviadas: <b>${data.prediction_count ?? '—'}</b></span>`;

  // ── Leaderboard ──
  drawLeaderboard(lb, me);
}

function drawPerformance(myHistory) {
  if (performanceChart) performanceChart.destroy();
  // Ordenar cronológicamente
  const rows = [...myHistory].sort((a, b) => new Date(a.measured_at) - new Date(b.measured_at));

  if (!$('performance')) return;

  if (rows.length === 0) {
    $('performance').closest('.panel').querySelector('canvas').style.display = 'none';
    const noData = document.createElement('p');
    noData.className = 'muted small';
    noData.textContent = 'Sin historial de accuracy disponible aún.';
    $('performance').closest('.panel').appendChild(noData);
    return;
  }

  performanceChart = new Chart($('performance'), {
    type: 'line',
    data: {
      labels: rows.map(x => dateText(x.measured_at)),
      datasets: [{
        label: 'Accuracy (%)',
        data: rows.map(x => parseFloat(x.accuracy)),
        borderColor: '#65d6b2',
        backgroundColor: '#65d6b233',
        fill: true,
        tension: .3,
        pointRadius: 4,
      }]
    },
    options: {
      responsive: true,
      plugins: {
        legend: { labels: { color: '#e8eef8' } },
        tooltip: { callbacks: { label: i => `Accuracy: ${i.raw.toFixed(2)}%` } }
      },
      scales: {
        y: { min: 0, max: 100, title: { display: true, text: 'Accuracy (%)', color: '#8fa0b8' }, ticks: { color: '#8fa0b8' } },
        x: { ticks: { color: '#8fa0b8', maxRotation: 30, maxTicksLimit: 8 } }
      }
    }
  });

  // Actualizar rango de fechas bajo el gráfico
  if (rows.length) {
    const info = $('performance').closest('.panel').querySelector('.muted.small');
    if (info) info.textContent = `📅 ${dateOnly(rows[0].measured_at)} → ${dateOnly(rows[rows.length-1].measured_at)} · ${rows.length} mediciones`;
  }
}

function drawRuns(runs) {
  if (errorChart) errorChart.destroy();
  if (!$('errors')) return;

  const statusColors = { succeeded: '#65d6b2', failed: '#ff6b6b', running: '#ffd166' };
  const counts = { succeeded: 0, failed: 0, running: 0 };
  runs.forEach(r => { if (r.status in counts) counts[r.status]++; });

  errorChart = new Chart($('errors'), {
    type: 'doughnut',
    data: {
      labels: ['Exitosos', 'Fallidos', 'En curso'],
      datasets: [{
        data: [counts.succeeded, counts.failed, counts.running],
        backgroundColor: ['#65d6b2', '#ff6b6b', '#ffd166'],
        borderColor: '#111c2e',
        borderWidth: 2,
      }]
    },
    options: {
      responsive: true,
      plugins: {
        legend: { labels: { color: '#e8eef8' } },
        tooltip: { callbacks: { label: i => `${i.label}: ${i.raw} runs` } }
      }
    }
  });
}

function drawMap() {
  if (map) map.remove();
  map = L.map('map').setView([4.65, -74.1], 11);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '© OpenStreetMap' }).addTo(map);

  // Estaciones fijas TransMilenio (coordenadas conocidas)
  const stations = [
    { id: '02300', name: 'Portal Norte',      lat: 4.7564, lng: -74.0447 },
    { id: '03000', name: 'Toberin',            lat: 4.7411, lng: -74.0503 },
    { id: '05000', name: 'Calle 100',          lat: 4.6854, lng: -74.0476 },
    { id: '05100', name: 'Calle 72',           lat: 4.6583, lng: -74.0579 },
    { id: '06000', name: 'Calle 45',           lat: 4.6356, lng: -74.0659 },
    { id: '06111', name: 'Av. El Dorado',      lat: 4.6280, lng: -74.0810 },
    { id: '07105', name: 'NQS-Calle 30',       lat: 4.6214, lng: -74.0996 },
    { id: '07107', name: 'NQS-Calle 30S',      lat: 4.6121, lng: -74.1011 },
    { id: '07111', name: 'General Santander',  lat: 4.6063, lng: -74.1058 },
    { id: '09000', name: 'Portal Sur',         lat: 4.5598, lng: -74.1460 },
    { id: '09122', name: 'Alquería',           lat: 4.5875, lng: -74.1282 },
    { id: '10009', name: 'Portal 80',          lat: 4.7047, lng: -74.1142 },
  ];

  stations.forEach(s =>
    L.marker([s.lat, s.lng]).addTo(map)
      .bindPopup(`<b>${s.name}</b><br>ID: ${s.id}`)
  );
}

function drawLeaderboard(lb, me) {
  const lbEl = $('leaderboard');
  if (!lbEl) return;

  if (!lb || lb.length === 0) {
    lbEl.innerHTML = '<div class="empty">Sin datos de leaderboard.</div>';
    return;
  }

  // Mostrar top 10 + tu posición
  const top10 = lb.slice(0, 10);
  const myRow = me.rank ? lb.find(r => r.display_name === me.display_name) : null;

  let html = `<table class="lb-table">
    <thead><tr><th>#</th><th>Estudiante</th><th>Accuracy</th><th>WAPE</th></tr></thead>
    <tbody>`;

  top10.forEach(r => {
    const isMe = r.display_name === me.display_name;
    html += `<tr class="${isMe ? 'lb-me' : ''}">
      <td>${r.rank}</td>
      <td>${r.display_name}</td>
      <td>${pct(r.accuracy)}</td>
      <td>${r.raw_wape != null ? parseFloat(r.raw_wape).toFixed(4) : '—'}</td>
    </tr>`;
  });

  // Si yo estoy fuera del top 10, mostrar separador y mi fila
  if (me.rank && me.rank > 10 && myRow) {
    html += `<tr class="lb-sep"><td colspan="4">···</td></tr>
    <tr class="lb-me">
      <td>${me.rank}</td>
      <td>${me.display_name} 👈</td>
      <td>${pct(me.accuracy)}</td>
      <td>${me.raw_wape != null ? parseFloat(me.raw_wape).toFixed(4) : '—'}</td>
    </tr>`;
  }

  html += `</tbody></table>
    <p class="muted small" style="margin-top:10px">
      Ciclos resueltos: <b>${data.leaderboard?.resolved_cycles ?? '—'}</b> ·
      Actualizado: ${me.calculated_at ? dateText(me.calculated_at) : '—'}
    </p>`;

  lbEl.innerHTML = html;
}

// Reemplazar el panel "series" con historial de runs ya que no hay series por estación
function initStationPanel() {
  const sel = $('station');
  if (!sel) return;
  // Ocultar select de estaciones (no viene en el nuevo API)
  sel.style.display = 'none';
  if ($('seriesRange')) $('seriesRange').textContent = 'Historial de accuracy acumulada del estudiante ↑';
  if (!seriesChart && data) {
    const me = data.leaderboard_me || {};
    const lbHistory = data.leaderboard_history || [];
    const myHistory = lbHistory.filter(h => h.student === me.display_name)
      .sort((a, b) => new Date(a.measured_at) - new Date(b.measured_at));

    if (seriesChart) seriesChart.destroy();
    seriesChart = new Chart($('series'), {
      type: 'line',
      data: {
        labels: myHistory.map(x => dateText(x.measured_at)),
        datasets: [{
          label: 'Accuracy histórica (%)',
          data: myHistory.map(x => parseFloat(x.accuracy)),
          borderColor: '#65d6b2',
          backgroundColor: '#65d6b233',
          fill: true,
          tension: .3,
          pointRadius: myHistory.length > 50 ? 0 : 4,
        }]
      },
      options: {
        responsive: true,
        plugins: { legend: { labels: { color: '#e8eef8' } } },
        scales: {
          y: { min: 0, max: 100, ticks: { color: '#8fa0b8' } },
          x: { ticks: { color: '#8fa0b8', maxRotation: 30, maxTicksLimit: 8 } }
        }
      }
    });
  }
}

$('refresh').onclick = load;
load().then(() => initStationPanel());
