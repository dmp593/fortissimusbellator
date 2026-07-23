(() => {
  "use strict";

  const normalize = (value) => value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .trim()
    .toLocaleLowerCase();

  const currentAliases = (textarea) => textarea.value
    .split(/[\n,;]+/)
    .map((value) => value.trim())
    .filter(Boolean);

  const appendSuggestions = (textarea, suggestions) => {
    const aliases = currentAliases(textarea);
    const known = new Set(aliases.map(normalize));

    suggestions.forEach((suggestion) => {
      const normalized = normalize(suggestion);
      if (normalized && !known.has(normalized)) {
        aliases.push(suggestion);
        known.add(normalized);
      }
    });

    textarea.value = aliases.join("\n");
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  };

  const showStatus = (status, message, isError = false) => {
    status.textContent = message;
    status.classList.toggle("errornote", isError);
  };

  const initializeWidgets = () => {
    const csrfToken = document.querySelector(
      "input[name=csrfmiddlewaretoken]"
    )?.value;

    if (!csrfToken) {
      return;
    }

    document.querySelectorAll("[data-chat-alias-widget]").forEach((widget) => {
      const button = widget.querySelector("[data-chat-alias-generate]");
      const textarea = widget.querySelector("textarea");
      const status = widget.querySelector("[data-chat-alias-status]");
      if (!button || !textarea || !status) {
        return;
      }

      button.addEventListener("click", async () => {
        button.disabled = true;
        button.textContent = button.dataset.loadingLabel;
        showStatus(status, "");

        try {
          const response = await fetch(button.dataset.url, {
            method: "POST",
            credentials: "same-origin",
            headers: {
              "X-CSRFToken": csrfToken,
              "X-Requested-With": "XMLHttpRequest",
            },
          });
          const data = await response.json();
          if (!response.ok) {
            throw new Error(data.error || widget.dataset.errorMessage);
          }

          appendSuggestions(textarea, data.suggestions || []);
          showStatus(
            status,
            data.message || widget.dataset.emptyMessage
          );
        } catch (error) {
          showStatus(
            status,
            error.message || widget.dataset.errorMessage,
            true
          );
        } finally {
          button.disabled = false;
          button.textContent = button.dataset.idleLabel;
        }
      });
    });
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializeWidgets, {
      once: true,
    });
  } else {
    initializeWidgets();
  }
})();
