"""Static, responsive HTML viewer for a run (index.html + report-data.js).

Find: search by name, org. no., municipality or industry; filter by state,
website, hiring and changes. Compare: sortable table of key figures.
Verify: every fact opens its evidence (source link, retrieval time, period,
content hash and the exact span it was read from).
"""
import json
from pathlib import Path

KEEP_EV = ("id", "source_url", "final_url", "source_class", "source_name", "retrieved_at", "content_sha256",
           "claim_span", "reporting_period", "extraction_method", "carried_from_run")


def _compact(env):
    return {
        "o": env["organisation_number"], "s": env["state"], "id": env.get("legal_identity") or {},
        "sum": (env.get("summary") or {}).get("sentences") or [{"topic": "note", "text": (env.get("summary") or {}).get("text", "")}],
        "sec": env.get("sections") or {},
        "cl": [{k: c.get(k) for k in ("id", "section", "field", "value", "availability", "confidence", "evidence_ids",
                                      "reporting_period", "effective_date", "note", "stale", "first_observed_at") if c.get(k) is not None}
               for c in env.get("claims", [])],
        "ev": [{k: e.get(k) for k in KEEP_EV if e.get(k) is not None} for e in env.get("evidence", [])],
        "ch": [{k: c.get(k) for k in ("change_type", "description", "materiality", "detected_at", "field")} for c in env.get("changes", [])],
        "er": env.get("errors") or [],
        "op": env.get("operations") or {},
    }


def write_html(path, envelopes, rep):
    path = Path(path)
    data = {"report": {k: rep.get(k) for k in ("run_id", "completed_at", "emitted_envelopes", "states", "operations", "validation")},
            "rows": [_compact(e) for e in envelopes]}
    (path.parent / "report-data.js").write_text("window.SIGNALPOST=" + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";",
                                                encoding="utf-8")
    path.write_text(PAGE, encoding="utf-8")


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Signalpost company profiles</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#14181f;--mut:#5b6472;--line:#e3e6eb;--acc:#1d4ed8;--ok:#15803d;--warn:#b45309;--bad:#b91c1c;--chip:#eef2ff}
@media (prefers-color-scheme:dark){:root{--bg:#0f1216;--card:#171b21;--ink:#e8ebf0;--mut:#9aa3b2;--line:#2a3039;--acc:#7aa2ff;--ok:#4ade80;--warn:#fbbf24;--bad:#f87171;--chip:#1e2633}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
header{position:sticky;top:0;z-index:5;background:var(--card);border-bottom:1px solid var(--line);padding:12px 16px}
h1{font-size:17px;margin:0 0 8px}.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
input,select,button{font:inherit;color:inherit;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:7px 10px}
input[type=search]{flex:1 1 220px;min-width:0}button{cursor:pointer}.stats{color:var(--mut);font-size:13px;margin-top:6px}
main{display:grid;grid-template-columns:minmax(300px,420px) 1fr;gap:16px;padding:16px;max-width:1500px;margin:auto}
@media (max-width:860px){main{grid-template-columns:1fr}#detail{display:none}#detail.open{display:block}#list.hide{display:none}}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px}
#list{overflow:auto;max-height:calc(100vh - 130px)}@media (max-width:860px){#list{max-height:none}}
.row{padding:10px 14px;border-bottom:1px solid var(--line);cursor:pointer}.row:hover,.row.sel{background:var(--chip)}
.row b{display:block}.meta{color:var(--mut);font-size:13px}.chips{display:flex;flex-wrap:wrap;gap:4px;margin-top:4px}
.chip{font-size:11.5px;padding:1px 7px;border-radius:99px;background:var(--chip);color:var(--ink);white-space:nowrap}
.s-available{color:var(--ok)}.s-not_available,.s-not_applicable{color:var(--mut)}.s-blocked,.s-ambiguous{color:var(--warn)}.s-failed{color:var(--bad)}
#detail{padding:18px 20px;min-width:0}#detail h2{margin:0;font-size:20px}.back{display:none;margin-bottom:10px}@media (max-width:860px){.back{display:inline-block}}
.summary p{margin:.4em 0}.sec{margin-top:18px}.sec h3{font-size:14px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut);margin:0 0 6px;display:flex;gap:8px;align-items:baseline}
table{width:100%;border-collapse:collapse}td{padding:6px 8px;border-top:1px solid var(--line);vertical-align:top;overflow-wrap:anywhere}
td.f{width:34%;color:var(--mut);font-size:13.5px}.ev{color:var(--acc);cursor:pointer;font-size:12.5px;white-space:nowrap}
.evbox{font-size:12.5px;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:8px;margin-top:6px;overflow-wrap:anywhere}
.evbox code{font-size:12px}.reason{color:var(--mut);font-size:13.5px}.stale{color:var(--warn);font-size:12px}
.tbl{overflow:auto}.cmp th{position:sticky;top:0;background:var(--card);text-align:left;font-size:12.5px;color:var(--mut);padding:6px 8px;cursor:pointer;white-space:nowrap}
.cmp td{font-size:13.5px}a{color:var(--acc)}.tabs{display:flex;gap:6px}.tabs button.on{background:var(--acc);color:#fff;border-color:var(--acc)}
</style></head><body>
<header><h1>Signalpost company profiles</h1>
<div class="bar"><input type="search" id="q" placeholder="Search name, org. no., municipality, industry…" aria-label="Search">
<select id="fstate" aria-label="Filter"><option value="">All companies</option><option value="web">Verified website</option><option value="jobs">Hiring</option>
<option value="news">Dated activity</option><option value="chg">Changed since last run</option><option value="fail">Failed / not found</option></select>
<select id="sort" aria-label="Sort"><option value="name">Sort: name</option><option value="rev">Sort: revenue</option><option value="emp">Sort: employees</option><option value="claims">Sort: facts found</option></select>
<span class="tabs"><button id="tlist" class="on">Profiles</button><button id="tcmp">Compare</button></span></div>
<div class="stats" id="stats"></div></header>
<main id="m"><section id="list" class="card"></section><section id="detail" class="card"><p class="reason">Select a company to see its profile and evidence.</p></section></main>
<script src="report-data.js"></script>
<script>
const D=window.SIGNALPOST,R=D.rows,$=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const SEC={identity:'Legal identity & brand',financials:'Annual accounts',leadership:'Leadership',locations:'Workplaces & locations',group:'Group',website:'Official website',social_profiles:'Social profiles',contact:'Contact',jobs:'Hiring',activity:'Dated activity'};
const fmtN=n=>n==null?'':Math.abs(n)>=1e6?(n/1e6).toFixed(1)+' m':Math.abs(n)>=1e3?(n/1e3).toFixed(0)+' k':String(n);
function val(c){const v=c.value;if(v==null)return '<span class="reason">'+esc(c.note||'not available')+'</span>';
 if(typeof v!=='object')return /^https?:/.test(v)?`<a href="${esc(v)}" target="_blank" rel="noopener">${esc(v)}</a>`:esc(v);
 if('amount' in v)return `${esc(v.currency||'')} ${Number(v.amount).toLocaleString('nb-NO')} <span class="meta">(${esc(v.scope)} ${esc(v.fiscal_year)})</span>`;
 if(v.url&&v.title)return `<a href="${esc(v.url)}" target="_blank" rel="noopener">${esc(v.title)}</a> <span class="meta">${esc([v.date,v.published&&('published '+v.published),v.location,v.source].filter(Boolean).join(' · '))}</span>`;
 if(v.platform)return `${esc(v.platform)}: <a href="${esc(v.url)}" target="_blank" rel="noopener">${esc(v.handle||v.url)}</a>`;
 if(v.url)return `<a href="${esc(v.url)}" target="_blank" rel="noopener">${esc(v.url)}</a>`+(v.identity_proof?` <span class="meta">(${esc(v.identity_proof.replaceAll('_',' '))})</span>`:'');
 if(v.text)return esc(v.text);if(v.name)return esc(v.name)+(v.role?` <span class="meta">${esc(v.role)}</span>`:'')+(v.orgnr?` <span class="meta">${esc(v.orgnr)}</span>`:'')+(v.address?` <span class="meta">${esc(v.address)}</span>`:'');
 if(v.description)return esc(v.description)+(v.code?` <span class="meta">${esc(v.code)}</span>`:'');
 if('fiscal_year' in v)return `${esc(v.scope)} accounts ${esc(v.fiscal_year)} · ${esc(v.currency)}`+(v.audited===false?' · unaudited':'');
 return '<code>'+esc(JSON.stringify(v))+'</code>'}
const g=(r,f)=>r.cl.find(c=>c.field===f&&c.availability==='available');
function rev(r){const c=r.cl.filter(c=>c.field==='revenue'&&c.value.scope==='company').sort((a,b)=>b.value.fiscal_year-a.value.fiscal_year)[0];return c?c.value.amount:null}
function flags(r){return{web:!!g(r,'official_website'),jobs:r.cl.some(c=>c.field==='job_posting'),news:r.cl.some(c=>c.field==='company_news'),chg:r.ch.length>0}}
function filtered(){const q=$('#q').value.trim().toLowerCase(),f=$('#fstate').value,s=$('#sort').value;
 let a=R.filter(r=>{const i=r.id;const hay=[r.o,i.legal_name,i.public_brand,i.municipality&&i.municipality.name,i.industry&&i.industry.description].join(' ').toLowerCase();
  if(q&&!hay.includes(q))return false;const fl=flags(r);if(f==='fail')return r.s!=='available';if(f&&!fl[f])return false;return true});
 const k={name:r=>(r.id.legal_name||'~'),rev:r=>-(rev(r)??-1e18),emp:r=>-(r.id.registered_employees??-1),claims:r=>-r.cl.length};
 a.sort((x,y)=>{const a1=k[s](x),b1=k[s](y);return a1<b1?-1:a1>b1?1:0});return a}
function list(){const a=filtered();$('#stats').textContent=`${a.length} of ${R.length} companies · run ${D.report.run_id} · ${D.report.operations.requests} requests · $${D.report.operations.third_party_cost_usd} third-party cost · validation ${D.report.validation.passed?'passed':'FAILED'}`;
 $('#list').innerHTML=a.slice(0,600).map(r=>{const i=r.id,fl=flags(r),rv=rev(r);return `<div class="row" data-o="${r.o}" tabindex="0"><b>${esc(i.legal_name||r.o)}</b>
 <span class="meta">${r.o} · ${esc(i.municipality?i.municipality.name:'')} · ${esc(i.industry?i.industry.description:'')}</span>
 <div class="chips"><span class="chip s-${r.s}">${r.s.replace('_',' ')}</span>${rv!=null?`<span class="chip">rev ${fmtN(rv)}</span>`:''}${i.registered_employees!=null?`<span class="chip">${i.registered_employees} empl.</span>`:''}${fl.web?'<span class="chip">website ✓</span>':''}${fl.jobs?'<span class="chip">hiring</span>':''}${fl.news?'<span class="chip">activity</span>':''}${fl.chg?'<span class="chip">changed</span>':''}</div></div>`}).join('')+(a.length>600?'<p class="reason" style="padding:10px">Showing first 600 — refine the search.</p>':'');
 document.querySelectorAll('.row').forEach(el=>{el.onclick=()=>show(el.dataset.o);el.onkeydown=e=>{if(e.key==='Enter')show(el.dataset.o)}})}
function evhtml(r,ids){return ids.map(id=>{const e=r.ev.find(x=>x.id===id);if(!e)return'';return `<div class="evbox"><b>${esc(e.source_name||e.source_class)}</b><br>
 <a href="${esc(e.final_url||e.source_url)}" target="_blank" rel="noopener">${esc(e.source_url)}</a><br>retrieved ${esc(e.retrieved_at)}${e.reporting_period?` · period ${esc(e.reporting_period.start)} – ${esc(e.reporting_period.end)}`:''}${e.carried_from_run?` · carried from ${esc(e.carried_from_run)}`:''}<br>
 span: <code>${esc(e.claim_span)}</code><br>method: ${esc(e.extraction_method)} · sha256 <code>${esc((e.content_sha256||'').slice(0,16))}…</code></div>`}).join('')}
function show(o){const r=R.find(x=>x.o===o);document.querySelectorAll('.row').forEach(e=>e.classList.toggle('sel',e.dataset.o===o));
 const i=r.id;let h=`<button class="back" onclick="back()">← All companies</button><h2>${esc(i.legal_name||o)}</h2><div class="meta">Org. no. ${o} · <span class="s-${r.s}">${r.s.replace('_',' ')}</span> · ${r.cl.filter(c=>c.availability==='available').length} supported facts · ${r.op.requests||0} requests</div>
 <div class="summary sec"><h3>Summary</h3>${r.sum.map(s=>`<p>${esc(s.text)}</p>`).join('')}</div>`;
 if(r.ch.length)h+=`<div class="sec"><h3>Changes since previous run</h3><table>${r.ch.map(c=>`<tr><td class="f">${esc(c.change_type)} <span class="meta">${esc(c.materiality)}</span></td><td>${esc(c.description)} <span class="meta">${esc(c.detected_at)}</span></td></tr>`).join('')}</table></div>`;
 for(const k in SEC){const st=r.sec[k]||{state:'not_available'},cs=r.cl.filter(c=>c.section===k);
  h+=`<div class="sec"><h3>${SEC[k]} <span class="s-${st.state}">${st.state.replace('_',' ')}</span></h3>`;
  if(st.reason&&st.state!=='available')h+=`<p class="reason">${esc(st.reason)}</p>`;
  if(cs.length)h+=`<table>${cs.map(c=>`<tr><td class="f">${esc(c.field.replaceAll('_',' '))}${c.reporting_period?`<br><span class="meta">${esc(c.reporting_period.end)}</span>`:''}</td><td>${val(c)}${c.stale?' <span class="stale">stale – kept from previous run</span>':''}
  ${c.evidence_ids&&c.evidence_ids.length?` <span class="ev" data-c="${c.id}">evidence ▸</span><div id="ev-${c.id}" hidden>${evhtml(r,c.evidence_ids)}</div>`:''}</td></tr>`).join('')}</table>`;
  h+='</div>'}
 if(r.er.length)h+=`<div class="sec"><h3>Errors</h3>${r.er.map(e=>`<p class="reason">${esc(e.source)}: ${esc(e.error)}</p>`).join('')}</div>`;
 const d=$('#detail');d.innerHTML=h;d.classList.add('open');$('#list').classList.add('hide');d.querySelectorAll('.ev').forEach(el=>el.onclick=()=>{const b=document.getElementById('ev-'+el.dataset.c);b.hidden=!b.hidden;el.textContent=b.hidden?'evidence ▸':'evidence ▾'});
 if(innerWidth<=860)scrollTo(0,0);history.replaceState(null,'','#'+o)}
function back(){$('#detail').classList.remove('open');$('#list').classList.remove('hide')}
function compare(){const a=filtered();const cols=[['Company',r=>esc(r.id.legal_name)],['Org. no.',r=>r.o],['Municipality',r=>esc(r.id.municipality?r.id.municipality.name:'')],['Industry',r=>esc(r.id.industry?r.id.industry.code:'')],['Employees',r=>r.id.registered_employees??'–'],['Revenue',r=>fmtN(rev(r))||'–'],['Net result',r=>{const c=r.cl.filter(c=>c.field==='net_profit'&&c.value.scope==='company').sort((a,b)=>b.value.fiscal_year-a.value.fiscal_year)[0];return c?fmtN(c.value.amount):'–'}],['Website',r=>{const c=g(r,'official_website');return c?`<a href="${esc(c.value.url)}" target="_blank" rel="noopener">${esc(c.value.domain)}</a>`:'–'}],['Jobs',r=>r.cl.filter(c=>c.field==='job_posting').length||'–'],['Facts',r=>r.cl.filter(c=>c.availability==='available').length]];
 $('#m').innerHTML=`<section class="card tbl" style="grid-column:1/-1"><table class="cmp"><tr>${cols.map(c=>`<th>${c[0]}</th>`).join('')}</tr>${a.slice(0,1500).map(r=>`<tr data-o="${r.o}">${cols.map(c=>`<td>${c[1](r)}</td>`).join('')}</tr>`).join('')}</table></section>`}
function mode(m){$('#tlist').classList.toggle('on',m==='list');$('#tcmp').classList.toggle('on',m==='cmp');
 if(m==='cmp')compare();else{$('#m').innerHTML='<section id="list" class="card"></section><section id="detail" class="card"><p class="reason">Select a company to see its profile and evidence.</p></section>';list()}}
let cur='list';$('#tlist').onclick=()=>mode(cur='list');$('#tcmp').onclick=()=>mode(cur='cmp');
['#q','#fstate','#sort'].forEach(s=>$(s).addEventListener('input',()=>cur==='cmp'?compare():list()));
list();const fromHash=()=>{const o=location.hash.slice(1);if(o&&R.find(r=>r.o===o)){if(cur!=='list')mode(cur='list');show(o)}};
addEventListener('hashchange',fromHash);fromHash();
</script></body></html>"""
