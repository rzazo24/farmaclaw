"use strict";
const $ = (id) => document.getElementById(id);

const EJEMPLOS = {
  libre: "¿Qué dice la ficha técnica del clopidogrel sobre el omeprazol?",
  interaccion: "omeprazol y clopidogrel",
  dosis: "ibuprofeno en niño de 15 kg",
  lactancia: "paracetamol",
  embarazo: "ácido valproico",
  suministro: "Somazina o CN 651877",
};

let viendo = null;      // consulta que se muestra
let dentroAhora = false;

async function api(ruta, datos) {
  const opciones = datos === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Farmaclaw": "1" },
    body: JSON.stringify(datos),
  };
  const r = await fetch(ruta, { credentials: "same-origin", ...opciones });
  let cuerpo = {};
  try { cuerpo = await r.json(); } catch (e) { /* respuesta sin JSON */ }
  if (r.status === 401 && ruta !== "/api/entrar") mostrar(false);
  if (!r.ok) throw new Error(cuerpo.detail || `Error ${r.status}`);
  return cuerpo;
}

function mostrar(dentro) {
  $("acceso").hidden = dentro;
  $("consultas").hidden = !dentro;
  $("barra").hidden = !dentro;
  $("lateral").hidden = !dentro;
  document.querySelectorAll(".solo-dentro").forEach((e) => { e.hidden = !dentro; });
  if (dentro) cargarRecientes();
  else {
    $("contrasena").value = ""; $("codigo").value = ""; limpiar();
    $("recientes").replaceChildren(); $("n-recientes").textContent = "0"; $("sin-recientes").hidden = false;
    $("aviso-inactividad").hidden = true;
  }
  dentroAhora = dentro;
  actividad();
}

// ── Tipo de consulta (tarjetas y etiquetas del lateral, sincronizadas) ──
function comando() { return document.querySelector('input[name="comando"]:checked').value; }
function elegir(tipo) {
  const radio = document.querySelector(`input[name="comando"][value="${tipo}"]`);
  if (radio) radio.checked = true;
  $("texto").placeholder = EJEMPLOS[tipo] || "";
  document.querySelectorAll("#chips button").forEach((b) => b.classList.toggle("activo", b.dataset.tipo === tipo));
}
document.querySelectorAll('input[name="comando"]').forEach((r) => r.addEventListener("change", () => {
  elegir(r.value); $("texto").focus();
}));
document.querySelectorAll("#chips button").forEach((b) => b.addEventListener("click", () => {
  elegir(b.dataset.tipo); irAConsultar();
}));
elegir("libre");

// ── Tema claro/oscuro: alterna y lo recuerda en este navegador ──
document.querySelectorAll(".boton-tema").forEach((b) => b.addEventListener("click", () => {
  const raiz = document.documentElement;
  const oscuro = raiz.dataset.theme ? raiz.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  raiz.dataset.theme = oscuro ? "light" : "dark";
  try { localStorage.setItem("farmaclaw-tema", raiz.dataset.theme); } catch (e) { /* sin almacenamiento */ }
}));

// ── Acceso ──
$("acceso").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("error-acceso").textContent = "";
  try {
    await api("/api/entrar", { contrasena: $("contrasena").value, codigo: $("codigo").value });
    mostrar(true);
    $("texto").focus();
  } catch (err) {
    $("error-acceso").textContent = err.message;
    $("codigo").value = "";
  }
});

async function salir() {
  try { await api("/api/salir", {}); } finally { mostrar(false); }
}
$("menu-salir").addEventListener("click", salir);
$("salir-movil").addEventListener("click", salir);

// ── Navegación ──
function limpiar() {
  viendo = null;
  $("respuesta").innerHTML = "";
  $("pasos").innerHTML = "";
  $("progreso").hidden = true;
  $("titulo-respuesta").hidden = true;
  $("error-consulta").textContent = "";
  marcarReciente();
}
function irAConsultar() { window.scrollTo({ top: 0, behavior: "smooth" }); $("texto").focus({ preventScroll: true }); }
function nueva() { limpiar(); $("texto").value = ""; irAConsultar(); }
$("nueva").addEventListener("click", nueva);
$("menu-nueva").addEventListener("click", nueva);
$("ir-consultar").addEventListener("click", irAConsultar);
$("menu-consultar").addEventListener("click", irAConsultar);

document.addEventListener("keydown", (e) => {
  if (!(e.ctrlKey || e.metaKey) || $("consultas").hidden) return;
  if (e.key.toLowerCase() === "k") { e.preventDefault(); irAConsultar(); }
  if (e.key === "Enter" && document.activeElement === $("texto")) { e.preventDefault(); $("formulario").requestSubmit(); }
});

// ── Ayuda ──
document.querySelectorAll(".abrir-ayuda").forEach((b) => b.addEventListener("click", () => $("ayuda").showModal()));
$("cerrar-ayuda").addEventListener("click", () => $("ayuda").close());
$("ayuda").addEventListener("click", (e) => { if (e.target === $("ayuda")) $("ayuda").close(); });  // clic fuera

// ── Consultas ──
$("formulario").addEventListener("submit", async (e) => {
  e.preventDefault();
  limpiar();
  $("enviar").disabled = true;
  try {
    const { id } = await api("/api/consulta", { comando: comando(), texto: $("texto").value });
    cargarRecientes();
    await ver(id);
  } catch (err) {
    $("error-consulta").textContent = err.message;
  } finally {
    $("enviar").disabled = false;
  }
});

// Muestra una consulta (en curso o terminada) y sigue su progreso hasta que acabe.
async function ver(id) {
  viendo = id;
  marcarReciente();
  $("error-consulta").textContent = "";
  $("respuesta").innerHTML = "";
  $("progreso").hidden = false;
  $("estado").classList.remove("hecho");
  for (;;) {
    const t = await api(`/api/consulta/${encodeURIComponent(id)}`);
    if (viendo !== id) return;  // el usuario ha pasado a otra
    $("pasos").replaceChildren(...t.pasos.map((p) => { const li = document.createElement("li"); li.textContent = p; return li; }));
    if (t.estado === "en_curso") {
      $("estado").textContent = `Buscando en las fuentes · ${t.segundos} s`;
      await new Promise((r) => setTimeout(r, 2000));
      continue;
    }
    $("estado").textContent = `Fuentes consultadas · ${t.duracion} s`;
    $("estado").classList.add("hecho");
    $("titulo-respuesta").textContent = t.titulo || "Respuesta";
    $("titulo-respuesta").hidden = false;
    if (t.error) $("error-consulta").textContent = t.error;
    // El HTML lo genera el servidor con markdown-it sin HTML crudo (escapado), así que es seguro insertarlo.
    $("respuesta").innerHTML = t.html || "";
    $("titulo-respuesta").scrollIntoView({ behavior: "smooth", block: "start" });
    cargarRecientes();
    return;
  }
}

// ── Recientes (lateral del PC) ──
async function cargarRecientes() {
  let lista = [];
  try { lista = await api("/api/consultas"); } catch (e) { return; }
  $("n-recientes").textContent = lista.length;
  $("sin-recientes").hidden = lista.length > 0;
  $("recientes").replaceChildren(...lista.map((c) => {
    const li = document.createElement("li");
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = c.titulo || "Consulta";
    b.title = c.titulo || "";
    b.dataset.id = c.id;
    if (c.estado === "en_curso") b.classList.add("en-curso");
    b.addEventListener("click", () => ver(c.id));
    li.append(b);
    return li;
  }));
  marcarReciente();
}
function marcarReciente() {
  document.querySelectorAll("#recientes button").forEach((b) => b.classList.toggle("activo", b.dataset.id === viendo));
}

// ── Cierre por inactividad ──
// 6 h (una jornada) sin ratón, teclado ni toques → se cierra la sesión y se borra la pantalla (aviso 1 min antes).
// Mientras hay actividad se avisa al servidor cada 5 min para que no caduque por su cuenta.
const INACTIVIDAD_MS = 6 * 3600 * 1000;   // una jornada de trabajo
const AVISO_MS = 60 * 1000;
const PING_MS = 5 * 60 * 1000;
let ultimaActividad = Date.now();
let ultimoPing = Date.now();

function actividad() {
  ultimaActividad = Date.now();
  if (!$("aviso-inactividad").hidden) $("aviso-inactividad").hidden = true;
  if (dentroAhora && Date.now() - ultimoPing > PING_MS) {
    ultimoPing = Date.now();
    api("/api/sesion").then((s) => { if (!s.activa) mostrar(false); }).catch(() => {});
  }
}
let ultimoMovimiento = 0;
["pointerdown", "keydown", "wheel", "touchstart", "scroll"].forEach((ev) =>
  window.addEventListener(ev, actividad, { passive: true, capture: true }));
window.addEventListener("pointermove", () => {          // el ratón se mueve mucho: como mucho 1 vez por segundo
  const ahora = Date.now();
  if (ahora - ultimoMovimiento > 1000) { ultimoMovimiento = ahora; actividad(); }
}, { passive: true });
$("seguir").addEventListener("click", actividad);

async function comprobarInactividad() {
  if (!dentroAhora) return;
  if ($("enviar").disabled) { ultimaActividad = Date.now(); return; }  // consulta en marcha: nunca se cierra
  const quieto = Date.now() - ultimaActividad;
  if (quieto >= INACTIVIDAD_MS) {
    await salir();
    $("error-acceso").textContent = "Sesión cerrada por inactividad.";
  } else if (quieto >= INACTIVIDAD_MS - AVISO_MS) {
    $("segundos-cierre").textContent = Math.ceil((INACTIVIDAD_MS - quieto) / 1000);
    $("aviso-inactividad").hidden = false;
  }
}
setInterval(comprobarInactividad, 1000);
// Al volver a la pestaña (o desbloquear el móvil) se comprueba enseguida.
document.addEventListener("visibilitychange", () => { if (!document.hidden) comprobarInactividad(); });

api("/api/sesion").then((s) => mostrar(s.activa)).catch(() => mostrar(false));
