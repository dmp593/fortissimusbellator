/** Session-only controller for the local chat assistant. */
(function () {
  "use strict";

  var STORAGE_KEY = "fortissimus_chat_history:v2:" +
    (document.documentElement.lang || "en");
  var STATE_KEY = "fortissimus_chat_state:v1:" +
    (document.documentElement.lang || "en");

  function csrfToken() {
    var match = document.cookie.match(/(?:^|; )csrftoken=([^;]*)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function pageContext() {
    var root = document.getElementById("chat-widget");
    var element = document.getElementById("chat-page-context");
    return {
      page_title: document.title || "",
      page_name: root ? root.dataset.pageName || "" : "",
      page_path: window.location.pathname || "",
      page_type: element ? element.dataset.pageType || "" : "",
      animal_id: element ? element.dataset.animalId || "" : "",
      animal_name: element ? element.dataset.animalName || "" : "",
      litter_id: element ? element.dataset.litterId || "" : "",
      litter_name: element ? element.dataset.litterName || "" : "",
      breed_id: element ? element.dataset.breedId || "" : "",
      breed_name: element ? element.dataset.breedName || "" : "",
    };
  }

  function loadHistory() {
    try {
      var history = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "[]");
      if (!Array.isArray(history)) return [];
      return history.filter(function (message) {
        return message &&
          (message.role === "user" || message.role === "assistant") &&
          typeof message.content === "string";
      });
    } catch (error) {
      return [];
    }
  }

  function loadState() {
    try {
      var state = JSON.parse(sessionStorage.getItem(STATE_KEY) || "{}");
      return state && typeof state === "object" && !Array.isArray(state)
        ? state
        : {};
    } catch (error) {
      return {};
    }
  }

  function ChatWidget(root) {
    this.root = root;
    this.panel = document.getElementById("chat-panel");
    this.toggle = document.getElementById("chat-toggle");
    this.close = document.getElementById("chat-close");
    this.reset = document.getElementById("chat-reset");
    this.form = document.getElementById("chat-form");
    this.input = document.getElementById("chat-input");
    this.send = document.getElementById("chat-send");
    this.messages = document.getElementById("chat-messages");
    this.history = loadHistory();
    this.state = loadState();
    this.loading = false;
    this.mobileViewport = window.matchMedia("(max-width: 47.999rem)");

    this.bindEvents();
    this.renderHistory();
  }

  ChatWidget.prototype.bindEvents = function () {
    var self = this;
    this.toggle.addEventListener("click", function (event) {
      self.setOpen(
        self.panel.classList.contains("hidden"),
        event.detail === 0
      );
    });
    this.close.addEventListener("click", function (event) {
      self.setOpen(false, event.detail === 0);
    });
    this.reset.addEventListener("click", function () { self.resetChat(); });
    this.form.addEventListener("submit", function (event) {
      event.preventDefault();
      self.submit();
    });
    document.querySelectorAll("[data-chat-suggestion]").forEach(function (button) {
      button.addEventListener("click", function () {
        self.input.value = button.dataset.chatSuggestion;
        self.submit(button.dataset.chatIntent);
      });
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && !self.panel.classList.contains("hidden")) {
        self.setOpen(false, true);
      }
    });
    window.addEventListener("resize", function () {
      self.syncViewport();
    });
    if (window.visualViewport) {
      window.visualViewport.addEventListener("resize", function () {
        self.syncViewport();
      });
      window.visualViewport.addEventListener("scroll", function () {
        self.syncViewport();
      });
    }
  };

  ChatWidget.prototype.setOpen = function (open, restoreFocus) {
    this.panel.classList.toggle("hidden", !open);
    this.panel.classList.toggle("flex", open);
    this.root.classList.toggle("chat-widget-open", open);
    this.toggle.setAttribute("aria-expanded", String(open));
    this.syncViewport();
    if (open) {
      if (!this.mobileViewport.matches) {
        this.input.focus({ preventScroll: true });
      }
    } else if (restoreFocus) {
      this.toggle.focus();
    } else if (this.root.contains(document.activeElement)) {
      document.activeElement.blur();
    }
  };

  ChatWidget.prototype.syncViewport = function () {
    var open = !this.panel.classList.contains("hidden");
    var mobileOpen = open && this.mobileViewport.matches;
    document.documentElement.classList.toggle("chat-overlay-open", mobileOpen);

    if (!mobileOpen) {
      this.root.style.removeProperty("--chat-viewport-height");
      this.root.style.removeProperty("--chat-viewport-offset-top");
      return;
    }

    var viewport = window.visualViewport;
    var height = viewport ? viewport.height : window.innerHeight;
    var offsetTop = viewport ? viewport.offsetTop : 0;
    this.root.style.setProperty(
      "--chat-viewport-height",
      Math.round(height) + "px"
    );
    this.root.style.setProperty(
      "--chat-viewport-offset-top",
      Math.round(offsetTop) + "px"
    );
  };

  ChatWidget.prototype.resetChat = function () {
    if (this.loading) return;
    this.history = [];
    try {
      sessionStorage.removeItem(STORAGE_KEY);
      sessionStorage.removeItem(STATE_KEY);
    } catch (error) {
      // The in-memory conversation is still reset when storage is unavailable.
    }
    this.state = {};
    this.renderHistory();
    if (!this.mobileViewport.matches) {
      this.input.focus({ preventScroll: true });
    }
  };

  ChatWidget.prototype.submit = function (intent) {
    var message = this.input.value.trim();
    if (!message || this.loading) return;

    this.input.value = "";
    this.setOpen(true);
    this.renderHistory({ role: "user", content: message });
    this.setLoading(true);

    var self = this;
    fetch(this.root.dataset.messageUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken(),
      },
      body: JSON.stringify({
        message: message,
        history: this.history,
        language: document.documentElement.lang || "en",
        context: pageContext(),
        intent: intent || null,
        state: this.state,
      }),
    })
      .then(function (response) {
        return response.json().then(function (data) {
          if (!response.ok) throw new Error(data.error || self.root.dataset.error);
          return data;
        });
      })
      .then(function (data) {
        self.history = Array.isArray(data.history) ? data.history : [];
        self.state = data.state && typeof data.state === "object"
          ? data.state
          : {};
        try {
          sessionStorage.setItem(STORAGE_KEY, JSON.stringify(self.history));
          sessionStorage.setItem(STATE_KEY, JSON.stringify(self.state));
        } catch (error) {
          // The conversation still works when browser storage is unavailable.
        }
        self.renderHistory();
      })
      .catch(function (error) {
        self.renderHistory(
          { role: "user", content: message },
          { role: "assistant", content: error.message, error: true }
        );
      })
      .finally(function () { self.setLoading(false); });
  };

  ChatWidget.prototype.renderHistory = function () {
    var extras = Array.prototype.slice.call(arguments);
    var items = this.history.concat(extras);
    this.messages.replaceChildren();
    this.addMessage("assistant", this.root.dataset.welcome);
    for (var index = 0; index < items.length; index += 1) {
      this.addMessage(items[index].role, items[index].content, items[index].error);
    }
    this.messages.scrollTop = this.messages.scrollHeight;
  };

  ChatWidget.prototype.addMessage = function (role, content, isError) {
    var row = document.createElement("div");
    row.className = "chat-message-row" +
      (role === "user" ? " chat-message-row-user" : "");

    var avatar = document.createElement("div");
    avatar.className = "chat-avatar " +
      (role === "user" ? "chat-avatar-user" : "chat-avatar-assistant");
    avatar.textContent = role === "user" ? "👤" : "🐕";
    avatar.setAttribute("aria-hidden", "true");

    var bubble = document.createElement("p");
    bubble.className = "chat-bubble " +
      (role === "user"
        ? "chat-bubble-user"
        : isError
          ? "chat-bubble-error"
          : "chat-bubble-assistant");
    bubble.textContent = content;

    row.appendChild(avatar);
    row.appendChild(bubble);
    this.messages.appendChild(row);
  };

  ChatWidget.prototype.setLoading = function (loading) {
    this.loading = loading;
    this.input.disabled = loading;
    this.send.disabled = loading;
    this.reset.disabled = loading;
    this.messages.setAttribute("aria-busy", String(loading));
    this.root.querySelectorAll("[data-chat-suggestion]").forEach(function (button) {
      button.disabled = loading;
    });
    if (!loading && !this.mobileViewport.matches) {
      this.input.focus({ preventScroll: true });
    }
  };

  var root = document.getElementById("chat-widget");
  if (root) new ChatWidget(root);
})();
