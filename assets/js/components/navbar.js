document.addEventListener('DOMContentLoaded', () => {
    const navbar = document.getElementById("navbar");
    const logo = document.getElementById("logo");

    window.addEventListener("scroll", function () {
        if (window.scrollY > 50) { // Adjust the scroll threshold as needed
            navbar.classList.remove("text-white");
            navbar.classList.add("bg-white", "dark:bg-stone-800", "shadow-md", "text-stone-800");
        } else {
            navbar.classList.remove("bg-white", "dark:bg-stone-800", "shadow-md", "text-stone-800");
            navbar.classList.add("text-white");
        }
    });

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

        // Update logo color if scrolled
        if (window.scrollY > 50) {
            if (theme === 'dark') {
                logo.classList.remove("text-stone-800");
                logo.classList.add("text-white");
            } else {
                logo.classList.remove("text-white");
                logo.classList.add("text-stone-800");
            }
        }

        document.cookie = `theme=${theme}; path=/; max-age=31536000`;
    }

    function toggleTheme() {
        const isDarkMode = document.documentElement.classList.contains('dark');
        const newTheme = isDarkMode ? 'light' : 'dark';
        setTheme(newTheme);
        // window.location.reload();
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