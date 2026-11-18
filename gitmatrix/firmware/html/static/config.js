// Art Matrix's config page autosave: a plugin's <form data-autosave> saves on
// every change, so Art Matrix config.html files work unchanged. As there, the
// whole form goes each time and one post is in flight at a time; GitMatrix also
// waits for changes to settle so slider and arrow-key runs make one flash write,
// and asks before saving while Bluetooth audio is playing (the 409 page).
const SETTLE_MS = 600;

for (const form of document.querySelectorAll("form[data-autosave]")) {
  let saving = false;
  let again = false;
  let timer;
  let pauseAudio = false;

  const post = () => {
    const body = new URLSearchParams(new FormData(form));
    if (pauseAudio) body.set("pause_audio", "1");
    return fetch(form.action || location.href, { method: "POST", body, redirect: "manual", keepalive: true });
  };

  const save = async () => {
    if (saving) { again = true; return; }
    saving = true;
    try {
      do {
        again = false;
        try {
          let response = await post();
          if (response.status === 409 && !pauseAudio &&
              confirm("Bluetooth audio is playing. Saving stops it and your device needs to reconnect.\n\nSave anyway?")) {
            pauseAudio = true;
            response = await post();
          }
          if (response.type === "opaqueredirect" || response.ok) toast("Saved!");
          else if (response.status === 409) toast("Not saved: Bluetooth audio is playing", true);
          else toast("Couldn't save that setting", true);
        } catch (error) {
          if (!(error instanceof TypeError)) throw error;
          toast("Couldn't save, check the board is connected", true);
        }
      } while (again);
    } finally {
      saving = false;
    }
  };

  form.addEventListener("change", () => {
    clearTimeout(timer);
    timer = form.reportValidity() ? setTimeout(() => { timer = null; save(); }, SETTLE_MS) : null;
  });

  // enter saves the field, textareas still get new lines
  form.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && event.target.tagName === "INPUT") {
      event.preventDefault();
      event.target.blur();
    }
  });

  // leaving the page: save the field being typed in and anything still waiting
  window.addEventListener("pagehide", () => {
    if (form.contains(document.activeElement)) document.activeElement.blur();
    if (timer || again) { clearTimeout(timer); timer = null; post(); }
  });
}
