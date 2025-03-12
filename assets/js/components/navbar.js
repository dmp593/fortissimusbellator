document.addEventListener('DOMContentLoaded', () => {
    function isDarkTheme() {
        return document.documentElement.classList.contains('dark');
    }

    function setTheme(theme) {
        const moonIcon = document.getElementById('moon-icon');
        const sunIcon = document.getElementById('sun-icon');

        if (theme === 'dark') {
            document.documentElement.classList.add('dark');
            moonIcon.classList.add('hidden');
            sunIcon.classList.remove('hidden');
        } else {
            document.documentElement.classList.remove('dark');
            sunIcon.classList.add('hidden');
            moonIcon.classList.remove('hidden');
        }

        document.cookie = `theme=${theme}; path=/; max-age=31536000`;
    }

    function toggleTheme() {
        const newTheme = isDarkTheme() ? 'light' : 'dark';
        setTheme(newTheme);
        window.location.reload();
    }

    document.getElementById('theme-toggle').addEventListener('click', toggleTheme);

    function setLanguage(lang) {
        document.cookie = `django_language=${lang}; path=/; max-age=31536000`;
        window.location.reload();
    }

    function toggleLanguage() {
        const currentLang = document.documentElement.lang;
        const newLang = currentLang === 'en' ? 'pt' : 'en';
        setLanguage(newLang);
    }

    document.getElementById('language-toggle').addEventListener('click', toggleLanguage);

    // Mobile menu toggle logic
    document.getElementById('mobile-menu-button').addEventListener('click', function () {
        document.getElementById('mobile-menu').classList.toggle('hidden');
    });
});