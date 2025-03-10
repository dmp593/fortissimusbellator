document.addEventListener('DOMContentLoaded', () => {
    // FAQ expand/collapse feature
    function toggleAnswer(question) {
        // Find the answer div (the next sibling of the clicked element's parent)
        const answer = question.nextElementSibling;
            
        // Find the icon inside the clicked element
        const icon = question.querySelector('span');

        // Toggle visibility of the answer
        answer.classList.toggle('hidden');

        // Toggle icon rotation
        icon.classList.toggle('rotate-180');
    }

    document.querySelectorAll(".question").forEach(question => {
        question.addEventListener('click', (event) => toggleAnswer(question))
    })
})