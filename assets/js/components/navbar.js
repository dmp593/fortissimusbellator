document.addEventListener('DOMContentLoaded', () => {
    // Theme Toggle
    document.getElementById('theme-toggle').addEventListener('click', function () {
        const isDark = document.documentElement.classList.toggle('dark');
        const theme = isDark ? 'dark' : 'light';

        document.cookie = `theme=${theme}; path=/; max-age=31536000`;
        
        document.getElementById('sun-icon').classList.toggle('hidden');
        document.getElementById('sun-icon').classList.toggle('size-6');
        
        document.getElementById('moon-icon').classList.toggle('hidden');
        document.getElementById('moon-icon').classList.toggle('size-6');
    });

    // Language Toggle
    document.getElementById('language-toggle').addEventListener('click', function () {
        const currentLang = document.documentElement.lang;
        const newLang = currentLang === 'en' ? 'pt' : 'en';
    
        const pathParts = window.location.pathname.split('/');
        if (['en', 'pt'].includes(pathParts[1])) {
            pathParts[1] = newLang;
        } else {
            pathParts.splice(1, 0, newLang);
        }
    
        const newPath = pathParts.join('/') + window.location.search + window.location.hash;
        window.location.href = newPath;
    });

    // Mobile Menu Toggle
    document.getElementById('mobile-menu-button').addEventListener('click', function () {
        const mobileMenu = document.getElementById('mobile-menu');
        mobileMenu.classList.toggle('hidden');
        mobileMenu.classList.toggle('scale-y-0');
        mobileMenu.classList.toggle('scale-y-100');
    });

    // Desktop Submenu Toggle
    const ourDogsButton = document.getElementById('our-dogs-button');
    const ourDogsSubmenu = document.getElementById('our-dogs-submenu');

    if (ourDogsButton && ourDogsSubmenu) {
        ourDogsButton.addEventListener('click', function (e) {
            ourDogsSubmenu.classList.toggle('scale-y-0');
            ourDogsSubmenu.classList.toggle('scale-y-100');

            // Rotate the arrow based on submenu visibility
            const arrow = ourDogsButton.querySelector('svg');
            arrow.classList.toggle('rotate-180');
        });
    }

    const userMenuButton = document.getElementById('user-menu-button');
    const userSubmenu = document.getElementById('user-submenu');

    if (userMenuButton && userSubmenu) {
        userMenuButton.addEventListener('click', function (e) {
            userSubmenu.classList.toggle('scale-y-0');
            userSubmenu.classList.toggle('scale-y-100');
        });
    }

    // Mobile Submenu Toggle
    const mobileOurDogsButton = document.getElementById('mobile-our-dogs-button');
    const mobileOurDogsSubmenu = document.getElementById('mobile-our-dogs-submenu');

    if (mobileOurDogsButton && mobileOurDogsSubmenu) {
        mobileOurDogsButton.addEventListener('click', function (e) {
            if (mobileOurDogsSubmenu.classList.contains('hidden')) {
                mobileOurDogsSubmenu.classList.toggle('hidden');

                setTimeout(() => {
                    mobileOurDogsSubmenu.classList.toggle('scale-y-0');
                    mobileOurDogsSubmenu.classList.toggle('scale-y-100');
                }, 10); // Small delay to allow CSS transitions
            } else {
                mobileOurDogsSubmenu.classList.toggle('scale-y-0');
                mobileOurDogsSubmenu.classList.toggle('scale-y-100');

                setTimeout(() => {
                    mobileOurDogsSubmenu.classList.toggle('hidden');
                }, 300); // Match the duration of the CSS transition
            }
        });
    }

    // Close Submenus When Clicking Outside
    document.addEventListener('click', function (e) {
        if (ourDogsButton && !ourDogsButton.contains(e.target)) {
            ourDogsSubmenu?.classList.add('scale-y-0');
            ourDogsSubmenu?.classList.remove('scale-y-100');
            ourDogsButton.querySelector('svg')?.classList.remove('rotate-180'); // Reset arrow rotation
        }

        if (userMenuButton && !userMenuButton.contains(e.target)) {
            userSubmenu?.classList.add('scale-y-0');
            userSubmenu?.classList.remove('scale-y-100');
        }
        
        if (mobileOurDogsButton && !mobileOurDogsButton.contains(e.target)) {
            mobileOurDogsSubmenu.classList.add('scale-y-0');
            mobileOurDogsSubmenu.classList.remove('scale-y-100');

            setTimeout(() => {
                mobileOurDogsSubmenu.classList.add('hidden');
            }, 300); // Match the duration of the CSS transition
        }
    });
});