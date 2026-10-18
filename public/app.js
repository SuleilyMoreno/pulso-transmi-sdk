let data, seriesChart, errorChart, performanceChart, map;
const $ = id => document.getElementById(id);

function pct(v)      { return v == null ? '—' : `${(v * 100).toFixed(1)}%`; }
function rate(v)     { return v == null ? '—' : `${(v * 100).toFixed(1)}%`; }
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
  const m = data.metrics || {};

  // ── KPI cards ──
  $('accuracy').textContent    = pct(m.accuracy_cumulative ?? m.accuracy);
  $('last6Accuracy').textContent = pct(m.accuracy_last6);
  $('last6Drift').textContent  = rate(m.drift_rate_last6);
  $('model').textContent       = data.model_version || '—';

  // Subtítulos con fechas
  const dr = data.data_ranges || {};
  $('accuracyDate').textContent = dr.metrics?.to
    ? `Última eval: ${dateText(dr.metrics.to)}`
    : (m.cycles_evaluated ? `${m.cycles_evaluated} ciclos evaluados` : '');
  $('last6Date').textContent   = dr.metrics?.from
    ? `Desde ${dateOnly(dr.metrics.from)}`
    : '';
  $('driftDate').textContent   = m.wape != null
    ? `WAPE: ${m.wape.toFixed(4)}`
    : '';
  $('modelDate').textContent   = dr.predictions?.to
    ? `Última pred: ${timeAgo(dr.predictions.to)}`
    : '';

  // ── Footer ──
  $('updated').textContent = `Actualizado: ${new Date(data.updated_at).toLocaleString('es-CO')}`;

  // ── Panel de fechas de Supabase ──
  renderDateRanges(dr);

  // ── Gráficos y mapa ──
  const ids = Object.keys(data.series || {});
  $('station').innerHTML = ids.map(x => `<option>${x}</option>`).join('');
  $('station').onchange = drawSeries;
  drawSeries();
  drawErrors();
  drawPerformance();
  drawMap();

  // ── Drift status ──
  const driftText = m.concept_drift == null ? 'Sin evaluación'
    : (m.concept_drift ? '⚠ Detectado' : '✓ No detectado');
  $('drift').innerHTML = `
    <span class="pill">Ciclos evaluados: <b>${m.cycles_evaluated || 0}</b></span>
    <span class="pill">Drift acumulado: <b class="${m.drift_rate_cumulative > 0 ? 'warn' : 'good'}">${rate(m.drift_rate_cumulative)}</b></span>
    <span class="pill">Drift últimos 6: <b class="${m.drift_rate_last6 > 0 ? 'warn' : 'good'}">${rate(m.drift_rate_last6)}</b></span>
    <span class="pill">Último: <b class="${m.concept_drift ? 'warn' : 'good'}">${driftText}</b></span>
    <span class="pill">Reentrenado: <b>${m.retrained ? 'Sí' : 'No'}</b></span>
    ${dr.metrics?.to ? `<span class="pill muted-pill">Última métrica: <b>${dateText(dr.metrics.to)}</b></span>` : ''}`;

  // ── Leaderboard / resumen ──
  $('leaderboard').innerHTML = `
    <div class="lb-row"><span>Accuracy acumulada</span><b>${pct(m.accuracy_cumulative ?? m.accuracy)}</b></div>
    <div class="lb-row"><span>Accuracy últimos 6 ciclos</span><b>${pct(m.accuracy_last6)}</b></div>
    <div class="lb-row"><span>WAPE</span><b>${m.wape != null ? m.wape.toFixed(4) : '—'}</b></div>
    <div class="lb-row"><span>Predicciones evaluadas</span><b>${m.matched || 0}</b></div>
    ${dr.predictions?.to ? `<div class="lb-row"><span>Última predicción enviada</span><b>${dateText(dr.predictions.to)}</b></div>` : ''}
    ${dr.observations?.to ? `<div class="lb-row"><span>Última observación real</span><b>${dateText(dr.observations.to)}</b></div>` : ''}`;
}

function renderDateRanges(dr) {
  const defs = [
    { key: 'observations', icon: '📊', label: 'Observaciones reales' },
    { key: 'predictions',  icon: '🔮', label: 'Predicciones enviadas' },
    { key: 'metrics',      icon: '📈', label: 'Métricas de drift' },
    { key: 'ingestion',    icon: '🔄', label: 'Cargas a Supabase' },
  ];

  $('dateRanges').innerHTML = defs.map(({ key, icon, label }) => {
    const r = dr[key] || {};
    const hasData = r.from && r.to;
    return `
      <div class="date-card">
        <div class="date-card-header">
          <span class="date-icon">${icon}</span>
          <span class="date-label">${label}</span>
          <span class="date-count ${hasData ? 'good' : 'warn'}">${r.count || 0} registros</span>
        </div>
        <div class="date-range">
          ${hasData
            ? `<span class="date-from"><b>Desde:</b> ${dateText(r.from)}</span>
               <span class="date-to"><b>Hasta:</b> ${dateText(r.to)}</span>`
            : `<span class="date-from" style="color:#5a7399">Sin datos disponibles en Supabase</span>`}
        </div>
      </div>`;
  }).join('');
}

function drawSeries() {
  const rows = (data.series || {})[$('station').value] || [];
  if (seriesChart) seriesChart.destroy();

  // Rango de fechas sobre el gráfico
  if ($('seriesRange')) {
    $('seriesRange').textContent = rows.length
      ? `📅 ${dateText(rows[0].observed_at)} → ${dateText(rows[rows.length - 1].observed_at)} · ${rows.length} observaciones`
      : 'Sin observaciones para esta estación';
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
      plugins: { legend: { labels: { color: '#e8eef8' } } },
      scales: {
        y: { beginAtZero: true, ticks: { color: '#8fa0b8' } },
        x: { ticks: { color: '#8fa0b8', maxRotation: 30, maxTicksLimit: 8 } }
      }
    }
  });
}

function drawPerformance() {
  if (performanceChart) performanceChart.destroy();
  const rows = data.cycle_history || [];
  const isTrue = value => value === true || value === 1 || ['true', '1', 'yes', 'si', 'sí'].includes(String(value).toLowerCase());

  performanceChart = new Chart($('performance'), {
    data: {
      labels: rows.map(x => x.evaluated_at ? dateText(x.evaluated_at) : x.cycle_id),
      datasets: [
        {
          type: 'line', label: 'Accuracy',
          data: rows.map(x => x.accuracy * 100),
          borderColor: '#65d6b2', backgroundColor: '#65d6b233',
          tension: .25, yAxisID: 'y', pointRadius: rows.length > 30 ? 0 : 4,
        },
        {
          type: 'line', label: 'Umbral',
          data: rows.map(x => (x.threshold || 0.8) * 100),
          borderColor: '#ffd166', borderDash: [6, 5], pointRadius: 0, yAxisID: 'y',
        },
        {
          type: 'line', label: 'Drift detectado',
          data: rows.map(x => isTrue(x.drift_detected) && x.accuracy != null ? x.accuracy * 100 : null),
          borderColor: '#ff8c69', backgroundColor: '#ff8c69',
          pointRadius: 5, pointHoverRadius: 7, showLine: false, yAxisID: 'y',
        }
      ]
    },
    options: {
      responsive: true,
      interaction: { mode: 'index', intersect: false },
      scales: {
        y: { min: 0, max: 100, title: { display: true, text: 'Accuracy (%)', color: '#8fa0b8' }, ticks: { color: '#8fa0b8' } },
        x: { ticks: { color: '#8fa0b8', maxRotation: 30, maxTicksLimit: 8 } },
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
                `Drift detectado: ${isTrue(row.drift_detected) ? 'Sí' : 'No'}`,
                `Reentrenado: ${isTrue(row.retrained) ? 'Sí' : 'No'}`,
                `Predicciones: ${row.matched_predictions}`,
              ];
            }
          }
        }
      }
    }
  });

  // Rango de fechas bajo el gráfico
  const perfInfo = document.querySelector('#performance').closest('.panel').querySelector('.muted.small');
  if (perfInfo && rows.length) {
    const from = rows[0]?.evaluated_at;
    const to   = rows[rows.length - 1]?.evaluated_at;
    perfInfo.textContent = `📅 ${dateOnly(from)} → ${dateOnly(to)} · ${rows.length} ciclos · Accuracy y drift desde drift_metrics. El área sombreada marca el umbral de reentrenamiento.`;
  }
}

function drawErrors() {
  if (errorChart) errorChart.destroy();
  const errors = data.errors || [];
  errorChart = new Chart($('errors'), {
    type: 'bar',
    data: {
      labels: ['≤ −100', '−100 a −25', '−25 a 25', '25 a 100', '> 100'],
      datasets: [{
        label: 'Errores',
        data: [-100, -25, 0, 25, 100].map((_, i) =>
          errors.filter(x => [
            x <= -100,
            x > -100 && x <= -25,
            x > -25 && x <= 25,
            x > 25 && x <= 100,
            x > 100
          ][i]).length
        ),
        backgroundColor: ['#ff6b6b', '#ffa94d', '#65d6b2', '#ffa94d', '#ff6b6b']
      }]
    },
    options: {
      plugins: { legend: { display: false } },
      scales: {
        y: { ticks: { color: '#8fa0b8' } },
        x: { ticks: { color: '#8fa0b8' } }
      }
    }
  });
}

function drawMap() {
  if (map) map.remove();
  map = L.map('map').setView([4.65, -74.1], 11);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '© OpenStreetMap' }).addTo(map);
  (data.stations || []).filter(s => s.latitude && s.longitude).forEach(s =>
    L.marker([s.latitude, s.longitude]).addTo(map)
      .bindPopup(`<b>${s.station_name || s.station_id}</b><br>ID: ${s.station_id}`)
  );
}

$('refresh').onclick = load;
load();
