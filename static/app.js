// ============================================================================
// Indian numbering helpers (mirrors backend/modules/formatting.py)
// ============================================================================
function indianGrouped(n){
  n = Math.round(Math.abs(n||0));
  let s = n.toString();
  if(s.length <= 3) return s;
  let last3 = s.slice(-3), rest = s.slice(0,-3), parts=[];
  while(rest.length > 2){ parts.unshift(rest.slice(-2)); rest = rest.slice(0,-2); }
  if(rest) parts.unshift(rest);
  return parts.join(',') + ',' + last3;
}
function fmtINR(n){ return '₹' + indianGrouped(n); }
function fmtCompact(n){
  if(n===null || n===undefined) return '—';
  const sign = n<0 ? '-' : '';
  const v = Math.abs(n);
  if(v >= 1e7) return sign + '₹' + (v/1e7).toFixed(2) + ' Cr';
  if(v >= 1e5) return sign + '₹' + (v/1e5).toFixed(2) + ' L';
  return sign + fmtINR(v);
}
function fmtTick(n){
  if(n===0) return '₹0';
  const sign = n<0?'-':'';
  const v = Math.abs(n);
  const trim = x => x.toFixed(2).replace(/\.?0+$/,'');
  if(v >= 1e7) return sign + '₹' + trim(v/1e7) + ' Cr';
  if(v >= 1e5) return sign + '₹' + trim(v/1e5) + ' L';
  return sign + fmtINR(v);
}

const CHARTS_AVAILABLE = typeof Chart !== 'undefined';
if (CHARTS_AVAILABLE) {
  Chart.defaults.font.family = "-apple-system,'Segoe UI',sans-serif";
  Chart.defaults.color = '#5b6b85';
} else {
  console.error('Chart.js failed to load from the CDN — charts will be skipped. ' +
    'Check your network/firewall access to cdnjs.cloudflare.com.');
}

// ============================================================================
// State
// ============================================================================
let PORTFOLIO = null;
let charts = {donut:null, trend:null, mfBar:null, proj:null};

async function api(path, opts){
  const res = await fetch(path, opts);
  if(!res.ok){
    let msg = res.statusText;
    try{ const j = await res.json(); msg = j.detail || msg; }catch(e){}
    throw new Error(msg);
  }
  return res.json();
}

// ============================================================================
// Bootstrapping
// ============================================================================
async function init(){
  document.getElementById('uploadBtn').addEventListener('click', handleUpload);
  document.getElementById('refreshBtn').addEventListener('click', () => loadPortfolio(true));
  document.getElementById('runVerdictsBtn').addEventListener('click', runVerdicts);
  document.querySelectorAll('.tab-btn').forEach(btn=>{
    btn.addEventListener('click', ()=>{
      document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
      document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById(btn.dataset.tab).classList.add('active');
    });
  });
  ['sipSlider','retSlider','yrsSlider'].forEach(id=>document.getElementById(id).addEventListener('input', runProjection));

  await loadPortfolio(false);
}

async function handleUpload(){
  const fileInput = document.getElementById('casFile');
  const statusEl = document.getElementById('uploadStatus');
  const warningsEl = document.getElementById('uploadWarnings');
  const resultsEl = document.getElementById('uploadFileResults');
  warningsEl.innerHTML = '';
  resultsEl.innerHTML = '';
  if(!fileInput.files.length){ statusEl.textContent = 'Choose a file first.'; return; }

  const btn = document.getElementById('uploadBtn');
  btn.disabled = true;
  statusEl.innerHTML = '<span class="spinner" style="border-color:rgba(27,59,111,0.3); border-top-color:var(--navy-2);"></span>Parsing...';

  const form = new FormData();
  form.append('file', fileInput.files[0]);
  try{
    const result = await api('/api/upload', {method:'POST', body: form});
    const s = result.summary;
    statusEl.textContent = `${s.successful} of ${s.total_files} file(s) processed` +
      (s.total_holdings_added ? ` · ${s.total_holdings_added} holdings added` : '') +
      (s.total_transactions_added ? ` · ${s.total_transactions_added} trades added` : '') + '.';

    if(result.files.length > 1 || result.files.some(f=>!f.ok)){
      resultsEl.innerHTML = '<table style="margin-top:10px;"><thead><tr><th>File</th><th>Type</th><th>Result</th></tr></thead><tbody>' +
        result.files.map(f=>{
          const icon = f.ok ? '✓' : '✗';
          const detail = f.ok
            ? (f.type==='cas' ? `${f.holdings_count} holdings` : f.type==='tradebook' ? `${f.transactions_inserted} trades` : 'OK')
            : f.error;
          return `<tr><td>${f.filename}</td><td>${f.type}</td><td>${icon} ${detail}</td></tr>`;
        }).join('') + '</tbody></table>';
    }

    const allWarnings = [];
    result.files.forEach(f => { if(f.warnings) allWarnings.push(...f.warnings); });
    if(result.cost_basis && result.cost_basis.warnings) allWarnings.push(...result.cost_basis.warnings);
    if(allWarnings.length){
      warningsEl.innerHTML = allWarnings.map(w=>`<div class="callout watch" style="margin-top:10px;">${w}</div>`).join('');
    }

    document.getElementById('uploadCardTitle').textContent = 'Add more statements';
    await loadPortfolio(false);
  }catch(e){
    statusEl.textContent = 'Error: ' + e.message;
  }finally{
    btn.disabled = false;
  }
}

// ============================================================================
// Tradebook FIFO positions (populated automatically after any tradebook upload)
// ============================================================================
async function loadTradebookPositions(){
  const data = await api('/api/tradebook/positions');
  document.getElementById('tradebookPositionsBody').innerHTML = data.positions.map(p=>{
    const pnlCls = p.realized_pnl > 0 ? 'gain' : (p.realized_pnl < 0 ? 'loss' : '');
    return `<tr><td>${p.symbol}</td><td class="num">${p.quantity}</td>
      <td class="num">${p.avg_cost!=null ? indianGrouped(p.avg_cost) : '—'}</td>
      <td class="num ${pnlCls}">${p.realized_pnl ? (p.realized_pnl>=0?'+':'')+indianGrouped(p.realized_pnl) : '—'}</td></tr>`;
  }).join('');
}

// ============================================================================
// Load + render portfolio
// ============================================================================
async function loadPortfolio(refresh){
  const refreshBtn = document.getElementById('refreshBtn');
  if(refresh){ refreshBtn.disabled = true; refreshBtn.innerHTML = '<span class="spinner"></span>Refreshing...'; }

  const data = await api(refresh ? '/api/portfolio/refresh' : '/api/portfolio', refresh ? {method:'POST'} : undefined);
  PORTFOLIO = data;

  if(refresh){ refreshBtn.disabled = false; refreshBtn.textContent = '↻ Refresh live prices'; }

  const hasHoldings = data.holdings && data.holdings.length > 0;
  document.getElementById('uploadCard').style.marginTop = hasHoldings ? '0' : '-28px';
  document.getElementById('uploadCardTitle').textContent = hasHoldings ? 'Add more statements' : 'Upload your statements';
  document.getElementById('kpiRow').style.display = hasHoldings ? 'grid' : 'none';
  document.getElementById('tabs').style.display = hasHoldings ? 'flex' : 'none';
  document.getElementById('headerActions').style.display = hasHoldings ? 'block' : 'none';

  if(!hasHoldings) return;

  renderKPIs(data);
  renderDonut(data.asset_class);
  renderRupeeBuckets(data.holdings, data.total_value);
  renderEquityTable(data.holdings);
  renderMF(data.holdings);
  renderBatches();
  loadTrendAndNps(data);
  loadObservations();
  loadTradebookPositions();
  runProjection();
}

function renderKPIs(data){
  document.getElementById('kpiValue').textContent = fmtCompact(data.total_value);
  document.getElementById('kpiGain').textContent = fmtCompact(data.total_gain);
  const gainPct = data.total_cost ? (data.total_gain/data.total_cost*100) : null;
  document.getElementById('kpiGainSub').textContent = gainPct!==null ? `${gainPct>=0?'+':''}${gainPct.toFixed(1)}% vs cost (equities have no CAS cost basis, see note)` : 'Cost basis unavailable for some holdings';

  const equityMf = data.holdings.filter(h=>['stock','mutual_fund'].includes(h.asset_type))
                                  .reduce((s,h)=>s+(h.value_inr||0),0);
  document.getElementById('kpiEquityMf').textContent = fmtCompact(equityMf);
  document.getElementById('kpiEquityMfSub').textContent = data.total_value ? `${(equityMf/data.total_value*100).toFixed(1)}% of portfolio` : '';

  const nps = data.holdings.filter(h=>h.asset_type==='nps');
  const npsValue = nps.reduce((s,h)=>s+(h.value_inr||0),0);
  document.getElementById('kpiValueSub').textContent = npsValue
    ? `Includes ${fmtCompact(npsValue)} NPS`
    : '';
}

async function loadTrendAndNps(data){
  const trendData = await api('/api/trend');
  if(trendData.points && trendData.points.length){
    renderTrend(trendData.points);
    const first = trendData.points[0].value, last = trendData.points[trendData.points.length-1].value;
    const growth = ((last-first)/first*100);
    document.getElementById('kpiGrowth').textContent = `${growth>=0?'+':''}${growth.toFixed(1)}%`;
    document.getElementById('kpiGrowth').style.color = growth>=0 ? 'var(--green)' : 'var(--red)';
    document.getElementById('kpiGrowthSub').textContent = `${fmtCompact(first)} → ${fmtCompact(last)} (from CAS trend table)`;
    document.getElementById('trendSub').textContent = `${trendData.points.length}-month history, straight from the NSDL CAS`;
  } else {
    document.getElementById('trendSub').textContent = 'No trend history found in this CAS (older/thinner statements may omit it).';
    document.getElementById('kpiGrowth').textContent = '—';
    document.getElementById('kpiGrowthSub').textContent = 'No trend data in this CAS';
  }
}

async function loadObservations(){
  const data = await api('/api/observations');
  if(!data.equity){ return; }
  const eq = data.equity;
  document.getElementById('obsStrip').innerHTML = `
    <div class="stat-chip"><div class="n">${eq.position_count}</div><div class="l">distinct equity positions</div></div>
    <div class="stat-chip"><div class="n">${eq.top5_pct}%</div><div class="l">of equity value in your top 5 names</div></div>
    <div class="stat-chip"><div class="n">${eq.small_positions_count}</div><div class="l">positions worth &lt;₹20,000 each</div></div>
    <div class="stat-chip"><div class="n">${eq.small_positions_pct_of_value}%</div><div class="l">of equity value held in those small positions</div></div>
  `;
  const topName = eq.top_holding ? eq.top_holding.name : '—';
  const topPct = eq.top_holding ? eq.top_holding.pct : 0;
  const secondName = eq.second_holding ? eq.second_holding.name : null;
  const secondPct = eq.second_holding ? eq.second_holding.pct : 0;
  document.getElementById('obsProse').innerHTML =
    `${eq.small_positions_pct_of_count}% of your stock count (${eq.small_positions_count} of ${eq.position_count} positions) ` +
    `accounts for just ${eq.small_positions_pct_of_value}% of equity value — a long tail of small positions. ` +
    `Your top holding is <b>${topName}</b> (${fmtCompact(eq.top_holding.value)}, ${topPct}% of equity)` +
    (secondName ? `, ahead of <b>${secondName}</b> (${secondPct}%).` : '.');

  document.getElementById('sectorBody').innerHTML = eq.sector_tally.map(s=>{
    const names = s.names.length>4 ? s.names.slice(0,4).join(', ')+`, +${s.names.length-4} more` : s.names.join(', ');
    return `<tr><td><span class="pill" style="background:#eef2fb;color:var(--blue);">${s.sector}</span></td><td>${names}</td><td class="num">${indianGrouped(s.value)}</td></tr>`;
  }).join('');

  const overlapEl = document.getElementById('overlapList');
  if(data.mf_overlap.length){
    overlapEl.innerHTML = '<ul style="padding-left:18px; margin:4px 0;">' + data.mf_overlap.map(o=>
      `<li><b>${o.category}:</b> ${o.funds.join('; ')}</li>`
    ).join('') + '</ul>';
  } else {
    overlapEl.innerHTML = '<p style="color:var(--ink-faint);">No obvious category overlap detected across your mutual fund folios.</p>';
  }
}

// ============================================================================
// Charts
// ============================================================================
function renderDonut(assetClass){
  const palette = {'stock':'#2e5fa8','mutual_fund':'#1a9169','nps':'#6a4c93','gold':'#c98a2c','unlisted_equity':'#8a97ac','other':'#b23a48'};
  const total = assetClass.reduce((s,a)=>s+a.value,0);
  document.getElementById('allocSub').textContent = `${fmtCompact(total)} across all uploaded and added holdings`;
  if(!CHARTS_AVAILABLE) return;
  const ctx = document.getElementById('donutChart');
  if(charts.donut) charts.donut.destroy();
  charts.donut = new Chart(ctx, {
    type:'doughnut',
    data:{
      labels: assetClass.map(a=>a.asset_type.replace('_',' ')),
      datasets:[{data: assetClass.map(a=>a.value), backgroundColor: assetClass.map(a=>palette[a.asset_type]||'#8a97ac'), borderWidth:2, borderColor:'#fff'}]
    },
    options:{
      responsive:true, maintainAspectRatio:false, cutout:'58%',
      plugins:{
        legend:{position:'bottom', labels:{boxWidth:11, padding:14, font:{size:11}}},
        tooltip:{callbacks:{label:c=>` ${c.label}: ${fmtCompact(c.raw)} (${(c.raw/total*100).toFixed(1)}%)`}}
      }
    }
  });
}

function renderTrend(points){
  if(!CHARTS_AVAILABLE) return;
  const ctx = document.getElementById('trendChart');
  if(charts.trend) charts.trend.destroy();
  charts.trend = new Chart(ctx, {
    type:'line',
    data:{
      labels: points.map(p=>p.label),
      datasets:[{data: points.map(p=>p.value), borderColor:'#1b3b6f', backgroundColor:'rgba(27,59,111,0.08)',
                 fill:true, tension:0.35, pointRadius:3, pointBackgroundColor:'#1b3b6f', borderWidth:2.5}]
    },
    options:{
      responsive:true, maintainAspectRatio:false,
      plugins:{legend:{display:false}, tooltip:{callbacks:{label:c=>` ${fmtCompact(c.raw)}`}}},
      scales:{ y:{ ticks:{ callback:v=>fmtTick(v) } } }
    }
  });
}

function renderRupeeBuckets(holdings, total){
  const labels = {cas:'CAS upload (equities + MF + bonds)', manual_us:'US market trades', unlisted:'Unlisted shares', gold:'Gold', manual_other:'Other assets'};
  // Break NPS out of the generic "cas" source bucket since it's a materially
  // different kind of holding (retirement account, not a tradeable position).
  const buckets = {};
  holdings.forEach(h=>{
    const key = h.asset_type === 'nps' ? 'nps' : h.source;
    buckets[key] = (buckets[key]||0) + (h.value_inr||0);
  });
  const rows = Object.entries(buckets).map(([key,value])=>({
    label: key==='nps' ? 'NPS (Tier I)' : (labels[key]||key), value
  })).sort((a,b)=>b.value-a.value);
  document.getElementById('rupeeBucketBody').innerHTML = rows
    .map(b=>`<tr><td>${b.label}</td><td class="num">${indianGrouped(b.value)}</td><td class="num">${total?(b.value/total*100).toFixed(1):0}%</td></tr>`)
    .join('');
}

function renderEquityTable(holdings){
  const stocks = holdings.filter(h=>h.asset_type==='stock' && (h.value_inr||0) > 0).sort((a,b)=>(b.value_inr||0)-(a.value_inr||0));
  const total = stocks.reduce((s,h)=>s+(h.value_inr||0),0);
  const withCost = stocks.filter(h=>h.avg_cost!=null).length;
  document.getElementById('equitySub').textContent = `${stocks.length} positions with a live balance, totalling ${fmtCompact(total)}. ` +
    (withCost ? `${withCost} have real cost basis from an uploaded tradebook.` : `NSDL/CDSL CAS statements don't track purchase cost for demat equities — upload a tradebook (see the Tradebook tab) to get real cost/gain figures.`);
  document.getElementById('equityBody').innerHTML = stocks.map(h=>{
    const pct = total ? (h.value_inr/total*100).toFixed(2) : '0.00';
    let costCell = '<td class="num">—</td><td class="num">—</td>';
    if(h.avg_cost != null && h.cost_inr != null){
      const gain = h.value_inr - h.cost_inr;
      const gainPct = h.cost_inr ? (gain/h.cost_inr*100).toFixed(1) : '0.0';
      const cls = gain>=0?'gain':'loss';
      costCell = `<td class="num">${indianGrouped(h.cost_inr)}</td><td class="num ${cls}">${gain>=0?'+':''}${indianGrouped(gain)} (${gain>=0?'+':''}${gainPct}%)</td>`;
    }
    return `<tr><td>${h.name||'—'}</td><td>${h.symbol||'—'}</td><td class="num">${h.quantity!=null?h.quantity.toLocaleString('en-IN'):'—'}</td>
      <td class="num">${h.current_price!=null?h.current_price.toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2}):'—'}</td>
      <td class="num">${indianGrouped(h.value_inr)}</td><td class="num">${pct}%</td>${costCell}</tr>`;
  }).join('');
}

function renderMF(holdings){
  const withCost = holdings.filter(h=>h.asset_type==='mutual_fund' && h.avg_cost!=null).sort((a,b)=>(b.value_inr||0)-(a.value_inr||0));
  const cost = withCost.reduce((s,h)=>s+h.cost_inr,0), value = withCost.reduce((s,h)=>s+h.value_inr,0);
  const gainPct = cost ? ((value-cost)/cost*100).toFixed(1) : '0.0';
  document.getElementById('mfSub').textContent = withCost.length
    ? `Cost ${fmtINR(cost)} → Value ${fmtINR(value)} → Unrealised gain ${fmtINR(value-cost)} (+${gainPct}%)`
    : 'No mutual fund folios with cost-basis data found yet.';

  document.getElementById('mfBody').innerHTML = withCost.map(h=>{
    const gain = h.value_inr - h.cost_inr, pct = h.cost_inr ? (gain/h.cost_inr*100).toFixed(1) : '0.0';
    const cls = gain>=0?'gain':'loss';
    return `<tr><td>${h.name}</td><td class="num">${indianGrouped(h.cost_inr)}</td><td class="num">${indianGrouped(h.value_inr)}</td>
      <td class="num ${cls}">${gain>=0?'+':''}${indianGrouped(gain)}</td><td class="num ${cls}">${gain>=0?'+':''}${pct}%</td></tr>`;
  }).join('');

  if(!CHARTS_AVAILABLE) return;
  const ctx = document.getElementById('mfBarChart');
  if(charts.mfBar) charts.mfBar.destroy();
  charts.mfBar = new Chart(ctx, {
    type:'bar',
    data:{
      labels: withCost.map(h=>h.name.length>28?h.name.slice(0,26)+'…':h.name),
      datasets:[
        {label:'Cost', data: withCost.map(h=>h.cost_inr), backgroundColor:'#c9d6ea'},
        {label:'Current Value', data: withCost.map(h=>h.value_inr), backgroundColor:'#1b3b6f'},
      ]
    },
    options:{
      indexAxis:'y', responsive:true, maintainAspectRatio:false,
      plugins:{legend:{position:'bottom'}, tooltip:{callbacks:{label:c=>` ${c.dataset.label}: ${fmtCompact(c.raw)}`}}},
      scales:{ x:{ ticks:{ callback:v=>fmtTick(v) } } }
    }
  });
}

// ============================================================================
// Verdicts
// ============================================================================
async function runVerdicts(){
  const btn = document.getElementById('runVerdictsBtn');
  const listEl = document.getElementById('verdictsList');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Scoring holdings live...';
  try{
    const data = await api('/api/portfolio/verdicts');
    const badgeClass = v => ({'Strong Buy':'buy','Buy':'buy','Hold':'hold','Trim':'trim','Exit':'exit'}[v] || 'na');
    listEl.innerHTML = data.verdicts.map(v=>`
      <div class="verdict-item">
        <span class="verdict-badge ${badgeClass(v.verdict)}">${v.verdict}</span>
        <b>${v.name||v.symbol||'Unnamed'}</b> ${v.composite_score!=null?`(score ${v.composite_score}/100)`:''}
        <div style="margin-top:6px; color:var(--ink-soft); white-space:pre-line;">${v.reasoning}</div>
      </div>
    `).join('');
  }catch(e){
    listEl.innerHTML = `<div class="callout watch">Error: ${e.message}</div>`;
  }finally{
    btn.disabled = false;
    btn.textContent = 'Run verdicts on all holdings';
  }
}

// ============================================================================
// Projection
// ============================================================================
async function runProjection(){
  if(!PORTFOLIO) return;
  const sip = +document.getElementById('sipSlider').value;
  const ret = +document.getElementById('retSlider').value;
  const years = +document.getElementById('yrsSlider').value;
  document.getElementById('sipVal').textContent = sip.toLocaleString('en-IN');
  document.getElementById('retVal').textContent = ret.toFixed(1);
  document.getElementById('yrsVal').textContent = years;

  const data = await api('/api/projection', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({current_value: PORTFOLIO.total_value, sip_amount: sip, annual_return_pct: ret, years})
  });

  document.getElementById('projInvested').textContent = fmtCompact(data.final.invested);
  document.getElementById('projCorpus').textContent = fmtCompact(data.final.corpus);
  document.getElementById('projGains').textContent = fmtCompact(data.final.gains);

  if(!CHARTS_AVAILABLE) return;
  const ctx = document.getElementById('projChart');
  if(charts.proj) charts.proj.destroy();
  charts.proj = new Chart(ctx, {
    type:'line',
    data:{
      labels:[0, ...data.yearly.map(y=>y.year)],
      datasets:[
        {label:'Total Invested', data:[PORTFOLIO.total_value, ...data.yearly.map(y=>y.invested)], borderColor:'#8fbfe0', borderDash:[4,4], pointRadius:0, borderWidth:2},
        {label:'Projected Corpus', data:[PORTFOLIO.total_value, ...data.yearly.map(y=>y.corpus)], borderColor:'#1b3b6f', backgroundColor:'rgba(46,134,171,0.12)', fill:'-1', pointRadius:0, borderWidth:3},
      ]
    },
    options:{
      responsive:true, maintainAspectRatio:false,
      plugins:{legend:{position:'bottom'}, tooltip:{callbacks:{label:c=>` ${c.dataset.label}: ${fmtCompact(c.raw)}`}}},
      scales:{ x:{title:{display:true,text:'Years'}}, y:{ ticks:{ callback:v=>fmtTick(v) } } }
    }
  });
}

// ============================================================================
// Batch management
// ============================================================================
async function renderBatches(){
  const batches = await api('/api/batches');
  document.getElementById('batchBody').innerHTML = batches.map(b=>`
    <tr><td>${b.label||b.batch_id}</td><td>${b.source}</td><td>${new Date(b.created_at).toLocaleString('en-IN')}</td>
    <td><button class="btn-danger" onclick="deleteBatch('${b.batch_id}')">Delete</button></td></tr>
  `).join('');
}
async function deleteBatch(id){
  await api(`/api/batches/${id}`, {method:'DELETE'});
  await loadPortfolio(false);
}

// ============================================================================
// Add-holdings forms
// ============================================================================
function setStatus(id, msg, ok){
  const el = document.getElementById(id);
  el.textContent = msg;
  el.className = 'form-status ' + (ok ? 'ok' : 'err');
}
async function addUS(){
  try{
    const body = { symbol: document.getElementById('usSymbol').value.trim(),
                   name: document.getElementById('usName').value.trim(),
                   quantity: +document.getElementById('usQty').value,
                   avg_cost: +document.getElementById('usCost').value };
    if(!body.symbol || !body.quantity) throw new Error('Enter a ticker and quantity.');
    await api('/api/manual-holdings/us', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
    setStatus('usStatus', 'Added.', true);
    await loadPortfolio(false);
  }catch(e){ setStatus('usStatus', e.message, false); }
}
async function addUnlisted(){
  try{
    const body = { name: document.getElementById('ulName').value.trim(),
                   quantity: +document.getElementById('ulQty').value,
                   avg_cost: +document.getElementById('ulCost').value,
                   estimated_current_price: document.getElementById('ulCurrent').value ? +document.getElementById('ulCurrent').value : null };
    if(!body.name || !body.quantity) throw new Error('Enter a company name and share count.');
    await api('/api/manual-holdings/unlisted', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
    setStatus('ulStatus', 'Added.', true);
    await loadPortfolio(false);
  }catch(e){ setStatus('ulStatus', e.message, false); }
}
async function addGold(){
  try{
    const body = { grams: +document.getElementById('goldGrams').value,
                   avg_cost_per_gram: +document.getElementById('goldCost').value,
                   current_price_per_gram: document.getElementById('goldCurrent').value ? +document.getElementById('goldCurrent').value : null };
    if(!body.grams) throw new Error('Enter a positive gram quantity.');
    await api('/api/manual-holdings/gold', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
    setStatus('goldStatus', 'Added.', true);
    await loadPortfolio(false);
  }catch(e){ setStatus('goldStatus', e.message, false); }
}
async function addOther(){
  try{
    const body = { name: document.getElementById('otherName').value.trim(),
                   asset_type: document.getElementById('otherType').value.trim() || 'other',
                   current_value: document.getElementById('otherValue').value ? +document.getElementById('otherValue').value : null,
                   quantity: 1, avg_cost: document.getElementById('otherValue').value ? +document.getElementById('otherValue').value : 0 };
    if(!body.name) throw new Error('Enter an asset name.');
    await api('/api/manual-holdings/other', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
    setStatus('otherStatus', 'Added.', true);
    await loadPortfolio(false);
  }catch(e){ setStatus('otherStatus', e.message, false); }
}

init();
