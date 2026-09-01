(() => {
  const input = document.getElementById("stock-filter-input");
  const rows = [...document.querySelectorAll("[data-stock-row]")];
  const count = document.getElementById("filter-count");
  const empty = document.getElementById("filter-no-match");
  const term = document.getElementById("filter-term");
  const clear = document.getElementById("stock-filter-clear");
  const heading = document.querySelector("#stock-list")
    ? document.querySelector(".panel-head h2")
    : null;
  if (!input || !rows.length) return;

  function apply() {
    const raw = input.value.trim();
    const needle = raw.toUpperCase();
    let visible = 0;
    rows.forEach((row) => {
      const haystack = [
        row.dataset.symbol || "",
        row.dataset.companyName || "",
        row.dataset.industry || "",
      ]
        .join(" ")
        .toUpperCase();
      const show = !needle || haystack.includes(needle);
      row.hidden = !show;
      if (show) visible += 1;
    });
    if (count) {
      count.textContent = needle
        ? `${visible} of ${rows.length}`
        : `${rows.length} stocks`;
    }
    if (empty) empty.hidden = visible !== 0;
    if (term) term.textContent = raw;
    if (clear) clear.hidden = !raw;
    if (heading) heading.textContent = needle ? `Matches for “${raw}”` : "All stocks";
    const url = new URL(window.location.href);
    if (needle) url.searchParams.set("q", raw);
    else url.searchParams.delete("q");
    window.history.replaceState({}, "", url);
  }

  input.addEventListener("input", apply);
  if (clear) {
    clear.addEventListener("click", () => {
      input.value = "";
      input.focus();
      apply();
    });
  }
  apply();
})();
