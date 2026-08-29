(() => {
  const input = document.getElementById("stock-q");
  const list = document.getElementById("stock-suggestions");
  if (!input || !list) return;

  let timer = null;
  let active = -1;
  let items = [];

  function hide() {
    list.hidden = true;
    list.innerHTML = "";
    active = -1;
    items = [];
  }

  function render(results) {
    items = results || [];
    if (!items.length) {
      hide();
      return;
    }
    list.innerHTML = items
      .map(
        (row, index) =>
          `<li><button type="button" data-index="${index}" data-symbol="${row.symbol}">` +
          `<span class="sym">${row.symbol}</span>` +
          `<span class="co">${row.company_name || ""}</span>` +
          `</button></li>`
      )
      .join("");
    list.hidden = false;
    active = -1;
  }

  function go(symbol) {
    if (!symbol) return;
    window.location.href = `/stocks/${encodeURIComponent(symbol)}`;
  }

  function setActive(next) {
    const buttons = [...list.querySelectorAll("button")];
    buttons.forEach((button) => button.classList.remove("is-active"));
    if (next < 0 || next >= buttons.length) {
      active = -1;
      return;
    }
    active = next;
    buttons[active].classList.add("is-active");
    buttons[active].scrollIntoView({ block: "nearest" });
  }

  input.addEventListener("input", () => {
    const query = input.value.trim();
    window.clearTimeout(timer);
    if (query.length < 1) {
      hide();
      return;
    }
    timer = window.setTimeout(async () => {
      try {
        const response = await fetch(`/api/stocks/search?q=${encodeURIComponent(query)}`);
        if (!response.ok) return;
        const payload = await response.json();
        if (input.value.trim() !== query) return;
        render(payload.results || []);
      } catch (_) {
        hide();
      }
    }, 180);
  });

  list.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-symbol]");
    if (!button) return;
    go(button.dataset.symbol);
  });

  input.addEventListener("keydown", (event) => {
    if (list.hidden) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive(active + 1 >= items.length ? 0 : active + 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive(active - 1 < 0 ? items.length - 1 : active - 1);
    } else if (event.key === "Enter" && active >= 0) {
      event.preventDefault();
      go(items[active].symbol);
    } else if (event.key === "Escape") {
      hide();
    }
  });

  document.addEventListener("click", (event) => {
    if (!event.target.closest(".search-form")) hide();
  });
})();
