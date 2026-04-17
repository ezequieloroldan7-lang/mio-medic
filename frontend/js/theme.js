/* theme.js — aplica el tema (light/dark) ANTES del primer paint para
   evitar FOUC. Se carga sincrono en <head>, antes del stylesheet.

   Política: light por defecto; dark solo si el usuario lo eligió
   explícitamente (persistido en localStorage.theme). No seguimos
   prefers-color-scheme del SO: la app se ve igual en todos los
   equipos hasta que el usuario decide cambiar.

   API pública expuesta en window.__theme:
     get()     → "light" | "dark" efectivo actual
     set(t)    → "light" | "dark" | null (null = volver a default light)
     toggle()  → flipa entre light y dark
     onChange(fn) → suscribe a cambios de tema
*/
(function () {
  var STORAGE_KEY = "theme";
  var listeners = [];

  function getStored() {
    try { return localStorage.getItem(STORAGE_KEY); } catch (e) { return null; }
  }

  function apply(theme) {
    if (theme === "dark") {
      document.documentElement.setAttribute("data-theme", "dark");
    } else {
      // "light" o cualquier valor inválido → default light (sin attribute)
      document.documentElement.removeAttribute("data-theme");
    }
  }

  function effective() {
    return getStored() === "dark" ? "dark" : "light";
  }

  function notify() {
    var t = effective();
    for (var i = 0; i < listeners.length; i++) {
      try { listeners[i](t); } catch (e) {}
    }
  }

  // Aplicar ya, antes del primer paint
  apply(getStored());

  window.__theme = {
    get: effective,
    set: function (theme) {
      if (theme === "dark") {
        try { localStorage.setItem(STORAGE_KEY, "dark"); } catch (e) {}
      } else {
        // "light" | null | undefined → default light; no persistimos
        try { localStorage.removeItem(STORAGE_KEY); } catch (e) {}
        theme = "light";
      }
      apply(theme);
      notify();
    },
    toggle: function () {
      this.set(effective() === "dark" ? "light" : "dark");
    },
    onChange: function (fn) {
      if (typeof fn === "function") listeners.push(fn);
    }
  };
})();
