document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.cert-header').forEach(element => {
        element.addEventListener('click', function () {
            const content = element.nextElementSibling; // .cert-content
            const icon = element.querySelector('span');
            content.classList.toggle('hidden');
            icon.classList.toggle('rotate-180');
        });
    });
})