const fmt = v => (v === null || v === undefined || v === "" ? "—" : v);

async function main(){
  const res = await fetch('data/latest.json', {cache:'no-store'});
  if(!res.ok) throw new Error(`latest.json: HTTP ${res.status}`);
  const data = await res.json();
  const status = document.getElementById('status');
  status.textContent = `スナップショット ${data.snapshot}｜取得成功 ${data.success ?? 0}/${data.requested ?? 0}局｜取得時刻 ${data.retrievedAt ?? '—'}`;

  const root = document.getElementById('stations');
  root.innerHTML = '';
  for(const s of data.stations || []){
    const hour = s.hourRain?.data;
    const cum = s.cumulativeRain?.data;
    const el = document.createElement('article');
    el.className = 'card';
    el.innerHTML = `
      <h2>${s.staName ?? '名称不明'}${s.fallbackSteps ? `<span class="badge">${s.fallbackSteps*10}分フォールバック</span>` : ''}</h2>
      <div class="meta">${fmt(s.cityName)}｜${fmt(s.riverName)}</div>
      <div class="values">
        <div class="value"><div class="label">60分雨量</div><div class="num">${fmt(hour)} <small>mm</small></div></div>
        <div class="value"><div class="label">累加雨量</div><div class="num">${fmt(cum)} <small>mm</small></div></div>
      </div>
      <div class="time">観測時刻: ${fmt(s.obsTime)}</div>
      <a class="source" href="${s.sourceUrl}" target="_blank" rel="noopener">出典JSON</a>`;
    root.appendChild(el);
  }

  for(const u of data.unavailable || []){
    const el = document.createElement('article');
    el.className = 'card unavailable';
    el.innerHTML = `<h2>${u.staName ?? `staId=${u.staId}`}</h2><div class="meta">取得未確認</div><p>指定時刻付近では観測JSONを確認できませんでした。</p>`;
    root.appendChild(el);
  }
}

main().catch(err=>{
  document.getElementById('status').textContent = `表示データの読み込みに失敗しました: ${err.message}`;
});
