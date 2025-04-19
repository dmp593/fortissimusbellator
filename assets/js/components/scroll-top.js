document.addEventListener("DOMContentLoaded", function () {
    const scrollTopButton = document.getElementById('scroll-top');

    window.addEventListener('scroll', () => {
        if (window.scrollY > 100) {
            scrollTopButton.classList.remove('opacity-0', 'invisible');
            scrollTopButton.classList.add('opacity-100', 'visible');
        } else {
            scrollTopButton.classList.remove('opacity-100', 'visible');
            scrollTopButton.classList.add('opacity-0', 'invisible');
        }
    });

    scrollTopButton.addEventListener('click', () => {
        window.scrollTo({
            top: 0,
            behavior: 'smooth'
        });
    });
});