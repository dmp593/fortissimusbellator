document.addEventListener('DOMContentLoaded', () => {
    function removeMessage(message) {
        message.style.opacity = '0'
        setTimeout(() => message.remove(), 300)  // Wait for fade-out before removing
    }

    // Auto-dismiss messages after 3 seconds
    setTimeout(function () {
        document.querySelectorAll('.message').forEach(removeMessage)
    }, 3000)

    // Add event listeners to close buttons
    document.querySelectorAll('.close-button').forEach(function(button) {
        button.addEventListener('click', function() {
            const message = this.closest('.message')
            removeMessage(message)
        });
    });
})