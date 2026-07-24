document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('.translation-widget').forEach(function (widget) {
    const input = widget.querySelector('input[type="text"], textarea');
    const btn = widget.querySelector('.translate-btn');
    const sourceLang = widget.dataset.sourceLang;
    const targetLang = widget.dataset.targetLang;

    btn.addEventListener('click', function () {
      const text = input.value;
      if (!text) return;
      btn.disabled = true;
      btn.innerHTML = '<span>⏳</span>';
      fetch('/admin/translate/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': getCookie('csrftoken'),
        },
        body: JSON.stringify({
          text: text,
          source_lang: sourceLang,
          target_lang: targetLang,
        })
      })
        .then(response => response.json())
        .then(data => {
          if (data.translated_text) {
            input.value = data.translated_text;
          }
        })
        .finally(() => {
          btn.disabled = false;
          btn.innerHTML = '<span>⇄</span>';
        });
    });
  });
});

function getCookie(name) {
  let cookieValue = null;
  if (document.cookie && document.cookie !== '') {
    const cookies = document.cookie.split(';');
    for (let i = 0; i < cookies.length; i++) {
      const cookie = cookies[i].trim();
      if (cookie.substring(0, name.length + 1) === (name + '=')) {
        cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
        break;
      }
    }
  }
  return cookieValue;
}
