// TravelBuddy demo SPA
let TOKEN = localStorage.getItem("tb_token") || null;
let TRIP_ID = localStorage.getItem("tb_trip_id") || null;

const qs = (s) => document.querySelector(s);
const qsa = (s) => Array.from(document.querySelectorAll(s));

function headers(json=true){
  const h = {};
  if (TOKEN) h["Authorization"] = "Bearer " + TOKEN;
  if (json) h["Content-Type"] = "application/json";
  return h;
}

function setStatus(){
  qs("#status").textContent = TOKEN ? "Logged in" : "Logged out";
  qs("#logout").classList.toggle("hidden", !TOKEN);
}

function show(tab){
  qsa(".tab").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
  ["auth","plan","itinerary","summary","vlog","community"].forEach(id => {
    qs("#"+id).classList.toggle("hidden", id !== tab);
  });
}

qsa(".tab").forEach(b => b.addEventListener("click", () => {
  if (!TOKEN && b.dataset.tab !== "auth") return alert("Please login first");
  show(b.dataset.tab);
  if (b.dataset.tab === "itinerary") refreshItinerary();
  if (b.dataset.tab === "summary") renderSummary();
}));

qs("#logout").addEventListener("click", () => {
  TOKEN = null; TRIP_ID = null;
  localStorage.removeItem("tb_token");
  localStorage.removeItem("tb_trip_id");
  location.reload();
});

qs("#register").addEventListener("click", async () => {
  qs("#r_msg").textContent="";
  const payload = {name:qs("#r_name").value, email:qs("#r_email").value, password:qs("#r_pass").value};
  const r = await fetch("/api/auth/register", {method:"POST", headers: headers(true), body: JSON.stringify(payload)});
  const d = await r.json();
  if (!r.ok) return qs("#r_msg").textContent = d.error || "Register failed";
  TOKEN = d.token; localStorage.setItem("tb_token", TOKEN); setStatus(); show("plan");
});

qs("#login").addEventListener("click", async () => {
  qs("#l_msg").textContent="";
  const payload = {email:qs("#l_email").value, password:qs("#l_pass").value};
  const r = await fetch("/api/auth/login", {method:"POST", headers: headers(true), body: JSON.stringify(payload)});
  const d = await r.json();
  if (!r.ok) return qs("#l_msg").textContent = d.error || "Login failed";
  TOKEN = d.token; localStorage.setItem("tb_token", TOKEN); setStatus(); show("plan");
});

let map, pickMode=null, startLL=null, destLL=null, startM=null, destM=null, routeLine=null, poiLayer=null;
function initMap(){
  map = L.map("map").setView([-33.8688, 151.2093], 10);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {maxZoom:19, attribution:"© OpenStreetMap"}).addTo(map);
  poiLayer = L.layerGroup().addTo(map);
  map.on("click", (e) => {
    if (!pickMode) return;
    if (pickMode === "start"){
      startLL = e.latlng;
      if (startM) startM.remove();
      startM = L.marker(startLL).addTo(map).bindPopup("Start").openPopup();
      pickMode = null;
    } else if (pickMode === "dest"){
      destLL = e.latlng;
      if (destM) destM.remove();
      destM = L.marker(destLL).addTo(map).bindPopup("Destination").openPopup();
      pickMode = null;
    }
    qs("#coords").textContent = `Start: ${startLL ? startLL.lat.toFixed(5)+","+startLL.lng.toFixed(5) : "not set"}  |  Dest: ${destLL ? destLL.lat.toFixed(5)+","+destLL.lng.toFixed(5) : "not set"}`;
  });
}
qs("#pick_start").addEventListener("click", ()=> pickMode="start");
qs("#pick_dest").addEventListener("click", ()=> pickMode="dest");

qs("#create_trip").addEventListener("click", async () => {
  const payload = {
    name: qs("#t_name").value,
    start: qs("#t_start").value,
    dest: qs("#t_dest").value,
    start_lat: startLL ? startLL.lat : null,
    start_lon: startLL ? startLL.lng : null,
    dest_lat: destLL ? destLL.lat : null,
    dest_lon: destLL ? destLL.lng : null,
    preferences: {vehicle_type: qs("#vehicle").value, passengers: parseInt(qs("#passengers").value||"1",10)}
  };
  const r = await fetch("/api/trips", {method:"POST", headers: headers(true), body: JSON.stringify(payload)});
  const d = await r.json();
  if (!r.ok) return qs("#trip_msg").textContent = d.error || "Create trip failed";
  TRIP_ID = d.trip_id; localStorage.setItem("tb_trip_id", TRIP_ID);
  qs("#trip_msg").textContent = "Trip created: " + TRIP_ID;
});

qs("#list_trips").addEventListener("click", async () => {
  const r = await fetch("/api/trips", {headers: headers(false)});
  const d = await r.json();
  if (!r.ok) return;
  qs("#trip_list").innerHTML = d.map(t => `<div>• <a href="#" data-id="${t.id}" class="tsel">${t.name}</a> <span class="muted">(${t.start}→${t.dest})</span></div>`).join("");
  qsa(".tsel").forEach(a => a.addEventListener("click", (e) => {
    e.preventDefault();
    TRIP_ID = a.dataset.id; localStorage.setItem("tb_trip_id", TRIP_ID);
    qs("#trip_msg").textContent = "Selected: " + TRIP_ID;
  }));
});

qs("#gen_route").addEventListener("click", async () => {
  if (!TRIP_ID) return alert("Create/select trip first");
  if (!startLL || !destLL) return alert("Pick Start and Dest on map first");
  const payload = {start_lat:startLL.lat, start_lon:startLL.lng, dest_lat:destLL.lat, dest_lon:destLL.lng};
  const r = await fetch(`/api/trips/${TRIP_ID}/route`, {method:"POST", headers: headers(true), body: JSON.stringify(payload)});
  const d = await r.json();
  if (!r.ok) return qs("#route_msg").textContent = d.error || "Route failed";
  if (routeLine) routeLine.remove();
  routeLine = L.geoJSON(d.geometry, {style:{weight:5}}).addTo(map);
  map.fitBounds(routeLine.getBounds(), {padding:[20,20]});
  qs("#route_msg").textContent = `Route: ${(d.distance_m/1000).toFixed(1)} km · ${(d.duration_s/60).toFixed(0)} min`;
});

let POIS = [];
function activeFilters(){ return new Set(qsa(".filt").filter(x=>x.checked).map(x=>x.value)); }
function renderPOIs(){
  const filt = activeFilters();
  const items = POIS.filter(p => filt.has(p.type)).slice(0, 80);
  qs("#pois").innerHTML = items.map(p => `
    <div class="poi">
      <div><div class="name">${p.name}</div><div class="meta">${p.type}</div></div>
      <div><button class="secondary add" data-id="${p.id}">Add</button></div>
    </div>
  `).join("") || "<div class='muted'>No POIs found.</div>";
  qsa(".add").forEach(b => b.addEventListener("click", ()=> addStop(b.dataset.id)));
  poiLayer.clearLayers();
  items.forEach(p => { if (!p.lat||!p.lon) return; L.circleMarker([p.lat,p.lon],{radius:7}).addTo(poiLayer).bindPopup(`<b>${p.name}</b><br/>${p.type}`); });
}
qsa(".filt").forEach(cb => cb.addEventListener("change", renderPOIs));

qs("#find_pois").addEventListener("click", async () => {
  if (!TRIP_ID) return alert("Create/select trip first");
  qs("#pois").innerHTML = "<div class='muted'>Loading POIs…</div>";
  const r = await fetch(`/api/trips/${TRIP_ID}/pois`, {method:"POST", headers: headers(true), body: JSON.stringify({pad:0.04})});
  const d = await r.json();
  if (!r.ok) return qs("#pois").innerHTML = `<div class='muted'>${d.error||"Failed"}</div>`;
  POIS = d.pois || [];
  renderPOIs();
});

async function loadTrip(){ const r = await fetch(`/api/trips/${TRIP_ID}`, {headers: headers(false)}); return await r.json(); }
async function saveItinerary(items){ await fetch(`/api/trips/${TRIP_ID}/itinerary`, {method:"POST", headers: headers(true), body: JSON.stringify({itinerary: items})}); }
async function addStop(poiId){
  const trip = await loadTrip();
  const itin = trip.itinerary || [];
  const p = POIS.find(x=>x.id===poiId);
  if (!p) return;
  if (itin.find(x=>x.id===p.id)) return;
  itin.push(p);
  await saveItinerary(itin);
  alert("Added to itinerary");
}

async function refreshItinerary(){
  if (!TRIP_ID) return qs("#itin").innerHTML = "<div class='muted'>Select a trip first.</div>";
  const trip = await loadTrip();
  const itin = trip.itinerary || [];
  qs("#itin").innerHTML = itin.map((s,i)=>`
    <div class="item">
      <div><b>${i+1}. ${s.name}</b><div class="muted small">${s.type}</div></div>
      <div class="rowwrap">
        <button class="secondary up" data-i="${i}">↑</button>
        <button class="secondary dn" data-i="${i}">↓</button>
        <button class="secondary rm" data-i="${i}">Remove</button>
      </div>
    </div>
  `).join("") || "<div class='muted'>No stops yet.</div>";
  qsa(".up").forEach(b=>b.addEventListener("click", async ()=>{ const i=+b.dataset.i; if(i<=0)return; [itin[i-1],itin[i]]=[itin[i],itin[i-1]]; await saveItinerary(itin); refreshItinerary(); }));
  qsa(".dn").forEach(b=>b.addEventListener("click", async ()=>{ const i=+b.dataset.i; if(i>=itin.length-1)return; [itin[i+1],itin[i]]=[itin[i],itin[i+1]]; await saveItinerary(itin); refreshItinerary(); }));
  qsa(".rm").forEach(b=>b.addEventListener("click", async ()=>{ const i=+b.dataset.i; itin.splice(i,1); await saveItinerary(itin); refreshItinerary(); }));
}
qs("#refresh_itin").addEventListener("click", refreshItinerary);

qs("#estimate").addEventListener("click", async ()=>{
  if (!TRIP_ID) return;
  const buffer = parseInt(qs("#buffer").value||"10",10);
  const trip = await loadTrip();
  const prefs = trip.preferences || {};
  const payload = {vehicle_type: prefs.vehicle_type || "petrol", passengers: prefs.passengers || 1, buffer_pct: buffer};
  const r = await fetch(`/api/trips/${TRIP_ID}/estimate`, {method:"POST", headers: headers(true), body: JSON.stringify(payload)});
  const d = await r.json();
  if (!r.ok) return qs("#est").innerHTML = `<div class='muted'>${d.error||"Estimate failed"}</div>`;
  qs("#est").innerHTML = `<b>Total:</b> ${d.currency} ${d.total}<div class="muted small">Distance: ${d.distance_km}km · Fuel/Charging: ${d.fuel_or_charging} · Food: ${d.food}</div>`;
});

async function renderSummary(){
  if (!TRIP_ID) return;
  const trip = await loadTrip();
  const itin = trip.itinerary || [];
  const r = await fetch(`/api/trips/${TRIP_ID}/estimate`, {method:"POST", headers: headers(true), body: JSON.stringify({buffer_pct:10})});
  const est = await r.json();
  qs("#bullets").innerHTML = [
    `<div class="b"><b>Depart</b><div class="muted small">${trip.start}</div></div>`,
    ...itin.map((s,i)=>`<div class="b"><b>Stop ${i+1}: ${s.name}</b><div class="muted small">${s.type}</div></div>`),
    `<div class="b"><b>Arrive</b><div class="muted small">${trip.dest}</div></div>`
  ].join("");
  const story = `You’re set for a smooth drive from <b>${trip.start}</b> to <b>${trip.dest}</b> ✨<br/><br/>Estimated spend: <b>${est.currency} ${est.total}</b>. Capture a few moments—this will be a trip worth remembering.`;
  qs("#story").innerHTML = story;
  const plain = `TravelBuddy: ${trip.name}\n${trip.start} → ${trip.dest}\nStops: ${itin.map(s=>s.name).join(", ")}\nEst: ${est.currency} ${est.total}`;
  qs("#wa").href = `https://wa.me/?text=${encodeURIComponent(plain)}`;
  qs("#email").href = `mailto:?subject=${encodeURIComponent("Trip Plan: "+trip.name)}&body=${encodeURIComponent(plain)}`;
}

qs("#publish").addEventListener("click", async ()=>{
  if (!TRIP_ID) return;
  const r = await fetch(`/api/trips/${TRIP_ID}/publish`, {method:"POST", headers: headers(true), body: JSON.stringify({})});
  qs("#pub_msg").textContent = r.ok ? "Published ✅" : "Publish failed";
});

qs("#load_prompts").addEventListener("click", async ()=>{
  const tpl = qs("#tpl").value;
  const r = await fetch(`/api/vlog/prompts?template=${encodeURIComponent(tpl)}`, {headers: headers(false)});
  const d = await r.json();
  qs("#prompts").innerHTML = (d.prompts||[]).map(p=>`<div class="p"><b>${p.phase}</b><div class="muted small">${p.prompt}</div></div>`).join("");
});

qs("#upload").addEventListener("click", async ()=>{
  if (!TRIP_ID) return alert("Create/select trip first");
  const f = qs("#file").files[0];
  if (!f) return alert("Choose a photo");
  const fd = new FormData();
  fd.append("day", qs("#day").value);
  fd.append("phase", qs("#phase").value);
  fd.append("file", f);
  const r = await fetch(`/api/vlog/${TRIP_ID}/upload`, {method:"POST", headers: headers(false), body: fd});
  qs("#up_msg").textContent = r.ok ? "Uploaded ✅" : "Upload failed";
});

qs("#daily").addEventListener("click", async ()=>{
  if (!TRIP_ID) return;
  const day = parseInt(qs("#day").value||"1",10);
  const r = await fetch(`/api/vlog/${TRIP_ID}/daily`, {method:"POST", headers: headers(true), body: JSON.stringify({day, seconds_per_image: 3})});
  const d = await r.json();
  if (!r.ok) return qs("#video").innerHTML = `<div class='muted'>${d.error||"Failed"}</div>`;
  qs("#video").innerHTML = `<video controls src="${d.video}"></video>`;
});

qs("#final").addEventListener("click", async ()=>{
  if (!TRIP_ID) return;
  const r = await fetch(`/api/vlog/${TRIP_ID}/final`, {method:"POST", headers: headers(true), body: JSON.stringify({seconds_per_image: 2})});
  const d = await r.json();
  if (!r.ok) return qs("#video").innerHTML = `<div class='muted'>${d.error||"Failed"}</div>`;
  qs("#video").innerHTML = `<video controls src="${d.video}"></video>`;
});

qs("#feed_btn").addEventListener("click", async ()=>{
  const r = await fetch("/api/feed", {headers: headers(false)});
  const d = await r.json();
  qs("#feed").innerHTML = (d||[]).map(t=>`<div class="f"><b>${t.name}</b><div class="muted small">${t.start} → ${t.dest}</div><div class="muted small">Trip ID: ${t.id}</div></div>`).join("") || "<div class='muted'>No public trips yet.</div>";
});

qs("#g_create").addEventListener("click", async ()=>{
  const payload = {name: qs("#g_name").value, visibility: qs("#g_vis").value};
  const r = await fetch("/api/groups", {method:"POST", headers: headers(true), body: JSON.stringify(payload)});
  const d = await r.json();
  alert(r.ok ? `Created group: ${d.id}` : (d.error||"Failed"));
});

qs("#g_list").addEventListener("click", async ()=>{
  const r = await fetch("/api/groups", {headers: headers(false)});
  const d = await r.json();
  qs("#groups").innerHTML = (d||[]).map(g=>`<div class="g"><b>${g.name}</b> <span class="muted small">(${g.visibility})</span><div class="muted small">ID: ${g.id}</div></div>`).join("") || "<div class='muted'>No groups yet.</div>";
});

qs("#post").addEventListener("click", async ()=>{
  const gid = qs("#pgid").value.trim();
  const payload = {trip_id: qs("#ptid").value.trim(), message: qs("#pmsg").value};
  const r = await fetch(`/api/groups/${gid}/post`, {method:"POST", headers: headers(true), body: JSON.stringify(payload)});
  qs("#post_msg").textContent = r.ok ? "Posted ✅" : "Post failed";
});

window.addEventListener("load", ()=>{
  initMap();
  if (TOKEN) show("plan");
  setStatus();
});
