let data, seriesChart, errorChart, performanceChart, map;
const $ = id => document.getElementById(id);

function pct(v) { return v == null ? '—' : `${(v * 100).toFixed(1)}%`; }
function rate(v) { return v == null ? '—' : `${(v * 100).toFixed(1)}%`; }

function dateText(v) {
  if (!v) return 'Sin datos';
  return new Date(v).toLocaleString('es-CO', { dateStyle: 'medium', timeStyle: 'short' });
}

function dateOnly(v) {
  if (!v) return 'Sin datos';
  return new Date(v).toLocaleDateString('es-CO', { dateStyle: 'medium' });
}

function timeAgo(v) {
  if (!v) return '';
  const diff = Date.now() - new Date(v).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `hace ${mins} min`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `hace ${hrs}h`;
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
    alert(e.message);
  } finally {
    $('refresh').disabled = false;
  }
}

function render() {
  const m = data.metrics;
  const dr = data.data_ranges || {};

  // ── KPI cards ──
  $('accuracy').textContent = pct(m.accuracy_cumulative ?? m.accuracy);
  $('accuracyDate').textContent = dr.metrics?.to
    ? `Última eval: ${dateText(dr.metrics.to)}`
    : '';

  $('last6Accuracy').textContent = pct(m.accuracy_last6);
  $('last6Date').textContent = `${m.cycles_evaluated || 0} ciclos evaluados`;

  $('last6Drift').textContent = rate(m.drift_rate_last6);
  $('driftDate').textContent = dr.metrics?.from
    ? `Desde ${dateOnly(dr.metrics.from)}`
    : '';

  $('model').textContent = data.model_version || '—';
  $('modelDate').textContent = dr.predictions?.to
    ? `Última predicción: ${timeAgo(dr.predictions.to)}`
    : '';

  // ── Footer timestamp ──
  $('updated').textContent = `Actualizado: ${new Date(data.updated_at).toLocaleString('es-CO')}`;

  // ── Sección de fechas de Supabase ──
  const labels = {
    observations: { icon: '📊', label: 'Observaciones reales', field: 'observed_at' },
    predictions:  { icon: '🔮', label: 'Predicciones (target)', field: 'target_at' },
    metrics:      { icon: '📈', label: 'Métricas evaluadas', field: 'evaluated_at' },
    ingestion:    { icon: '🔄', label: 'Cargas a Supabase', field: 'started_at' },
  };

  $('dateRanges').innerHTML = Object.entries(labels).map(([key, meta]) => {
    const r = dr[key] || {};
    const hasData = r.from && r.to;
    return `
      <div class="date-card">
        <div class="date-card-header">
          <span class="date-icon">${meta.icon}</span>
          <span class="date-label">${meta.label}</span>
          <span class="date-count ${hasData ? 'good' : 'warn'}">${r.count || 0} registros</span>
        </div>
        ${hasData
          ? `<div class="date-range">
               <span class="date-from"><b>Desde:</b> ${dateText(r.from)}</span>
               <span class="date-to"><b>Hasta:</b> ${dateText(r.to)}</span>
             </div>`
          : `<div class="date-range warn">Sin datos en Supabase</div>`}
      </div>`;
  }).join('');

  // ── Gráfico de series ──
  const ids = Object.keys(data.series);
  $('station').innerHTML = ids.map(x => `<option>${x}</option>`).join('');
  $('station').onchange = drawSeries;
  drawSeries();

  // ── Resto de gráficos ──
  drawErrors();
  drawPerformance();
  drawMap();

  // ── Drift status pills ──
  const driftText = m.concept_drift == null ? 'Sin evaluación' : (m.concept_drift ? 'Detectado' : 'No detectado');
  $('drift').innerHTML = `
    <span class="pill">Ciclos evaluados: <b>${m.cycles_evaluated || 0}</b></span>
    <span class="pill">Drift acumulado: <b class="${m.drift_rate_cumulative ? 'warn' : 'good'}">${rate(m.drift_rate_cumulative)}</b></span>
    <span class="pill">Drift últimos 6: <b class="${m.drift_rate_last6 ? 'warn' : 'good'}">${rate(m.drift_rate_last6)}</b></span>
    <span class="pill">Último: <b class="${m.concept_drift ? 'warn' : 'good'}">${driftText}</b></span>
    <span class="pill">Reentrenado: <b>${m.retrained ? 'Sí' : 'No'}</b></span>
    ${dr.metrics?.to ? `<span class="pill muted-pill">Última métrica: <b>${dateText(dr.metrics.to)}</b></span>` : ''}`;

  // ── Leaderboard / resumen ──
  $('leaderboard').innerHTML = `
    <div class="lb-row"><span>Accuracy acumulada</span><b>${pct(m.accuracy_cumulative ?? m.accuracy)}</b></div>
    <div class="lb-row"><span>Accuracy últimos 6 ciclos</span><b>${pct(m.accuracy_last6)}</b></div>
    <div class="lb-row"><span>WAPE último ciclo</span><b>${m.wape != null ? m.wape.toFixed(4) : '—'}</b></div>
    <div class="lb-row"><span>Predicciones evaluadas (último ciclo)</span><b>${m.matched || 0}</b></div>
    ${dr.predictions?.to ? `<div class="lb-row muted"><span>Última predicción enviada</span><b>${dateText(dr.predictions.to)}</b></div>` : ''}`;
}

function drawSeries() {
  const rows = data.series[$('station').value] || [];
  if (seriesChart) seriesChart.destroy();

  // Mostrar rango de fechas del gráfico
  if ($('seriesRange')) {
    if (rows.length) {
      const from = dateText(rows[0].observed_at);
      const to = dateText(rows[rows.length - 1].observed_at);
      $('seriesRange').textContent = `📅 ${from} → ${to} · ${rows.length} observaciones`;
    } else {
      $('seriesRange').textContent = 'Sin datos para esta estación';
    }
  }

  seriesChart = new Chart($('series'), {
    type: 'line',
    data: {
      labels: rows.map(x => dateText(x.observed_at)),
      datasets: [{
        label: 'Demanda',
        data: rows.map(x => x.demand),
        borderColor: '#65d6b2',
        backgroundColor: '#65d6b233',
        fill: true,
        tension: .25,
        pointRadius: rows.length > 50 ? 0 : 3,
      }]
    },
    options: {
      responsive: true,
      plugins: {
        legend: { labels: { color: '#e8eef8' } },
        tooltip: {
          callbacks: {
            title: items => items[0].label,
            label: item => `Demanda: ${item.raw}`
          }
        }
      },
      scales: { y: { beginAtZero: true, ticks: { color: '#8fa0b8' } }, x: { ticks: { color: '#8fa0b8', maxRotation: 30, maxTicksLimit: 8 } } }
    }
  });
}

function drawPerformance() {
  if (performanceChart) performanceChart.destroy();
  const rows = data.cycle_history || [];
  performanceChart = new Chart($('performance'), {
    data: {
      labels: rows.map(x => {
        const label = x.cycle_id || '';
        // Si tiene fecha, mostrarla corta
        return x.evaluated_at ? dateText(x.evaluated_at) : label;
      }),
      datasets: [
        { type: 'line', label: 'Accuracy', data: rows.map(x => x.accuracy * 100), borderColor: '#65d6b2', backgroundColor: '#65d6b233', tension: .25, yAxisID: 'y', pointRadius: 4 },
        { type: 'line', label: 'Umbral', data: rows.map(x => x.threshold * 100), borderColor: '#ffd166', borderDash: [6, 5], pointRadius: 0, yAxisID: 'y' },
        { type: 'bar', label: 'Drift detectado', data: rows.map(x => x.drift_detected ? 100 : 0), backgroundColor: '#ff8c69aa', yAxisID: 'drift' }
      ]
    },
    options: {
      responsive: true,
      interaction: { mode: 'index', intersect: false },
      scales: {
        y: { min: 0, max: 100, title: { display: true, text: 'Accuracy (%)', color: '#8fa0b8' }, ticks: { color: '#8fa0b8' } },
        x: { ticks: { color: '#8fa0b8', maxRotation: 30, maxTicksLimit: 8 } },
        drift: { min: 0, max: 100, display: false }
      },
      plugins: {
        legend: { labels: { color: '#e8eef8' } },
        tooltip: {
          callbacks: {
            afterBody: (items) => {
              const row = rows[items[0].dataIndex];
              return [
                `Ciclo: ${row.cycle_id}`,
                `Fecha: ${row.evaluated_at ? dateText(row.evaluated_at) : '—'}`,
                `Reentrenado: ${row.retrained ? 'Sí' : 'No'}`,
                `Valores: ${row.matched_predictions}`
              ];
            }
          }
        }
      }
    }
  });
}

function drawErrors() {
  if (errorChart) errorChart.destroy();
  errorChart = new Chart($('errors'), {
    type: 'bar',
    data: {
      labels: ['≤ −100', '−100 a −25', '−25 a 25', '25 a 100', '> 100'],
      datasets: [{
        label: 'Errores',
        data: [-100, -25, 0, 25, 100].map((_, i) =>
          data.errors.filter(x => [x <= -100, x > -100 && x <= -25, x > -25 && x <= 25, x > 25 && x <= 100, x > 100][i]).length
        ),
        backgroundColor: ['#ff6b6b', '#ffa94d', '#65d6b2', '#ffa94d', '#ff6b6b']
      }]
    },
    options: {
      plugins: { legend: { display: false } },
      scales: { y: { ticks: { color: '#8fa0b8' } }, x: { ticks: { color: '#8fa0b8' } } }
    }
  });
}

function drawMap() {
  if (map) map.remove();
  map = L.map('map').setView([4.65, -74.1], 11);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '© OpenStreetMap' }).addTo(map);
  data.stations.filter(s => s.latitude && s.longitude).forEach(s =>
    L.marker([s.latitude, s.longitude]).addTo(map)
      .bindPopup(`<b>${s.station_name || s.station_id}</b><br>ID: ${s.station_id}`)
  );
}

$('refresh').onclick = load;
load();
