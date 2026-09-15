// Apply the saved palette before CSS paints; unknown preferences use Focus.
(() => {
  const keys = ["jade", "blue", "paper", "focus"];
  const apply = (value) => {
    const theme = keys.includes(value) ? value : "focus";
    document.documentElement.dataset.theme = theme;
    return theme;
  };
  try { apply(localStorage.getItem("paam-theme")); } catch { apply("focus"); }
  window.paamTheme = {
    set(value) {
      const theme = apply(value);
      try { localStorage.setItem("paam-theme", theme); return true; } catch { return false; }
    },
  };
  window.addEventListener("storage", (event) => {
    if (event.key === "paam-theme" || event.key === null) {
      apply(event.newValue);
      window.dispatchEvent(new Event("paam-theme-change"));
    }
  });
})();
