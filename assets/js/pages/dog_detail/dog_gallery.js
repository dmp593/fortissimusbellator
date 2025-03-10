document.addEventListener('DOMContentLoaded', () => {
    function openModal(url, type) {
        const modal = document.getElementById('gallery-modal');
        const modalImage = document.getElementById('modalImage');
        const modalVideo = document.getElementById('modalVideo');
    
        if (type === 'image') {
            modalImage.src = url;
            modalImage.classList.remove('hidden');
            modalVideo.classList.add('hidden');
        } else {
            modalVideo.src = url;
            modalVideo.classList.remove('hidden');
            modalImage.classList.add('hidden');
        }
    
        modal.classList.remove('hidden');
    }
    
    function closeModal() {
        const modal = document.getElementById('gallery-modal');
        const modalImage = document.getElementById('modalImage');
        const modalVideo = document.getElementById('modalVideo');
    
        modal.classList.add('hidden');
        modalImage.src = '';
        modalVideo.src = '';
        modalVideo.pause();
    }
    
    document.querySelectorAll('.modal-open').forEach(element => {
        element.addEventListener('click', () => {
            const fileUrl = element.dataset.fileUrl;
            const fileType = element.dataset.fileType;
            openModal(fileUrl, fileType);
        });
    })

    document.getElementById('close-modal').addEventListener('click', closeModal);

    document.getElementById('gallery-modal').addEventListener('click', (e) => {
        if (e.target.id === 'modal-content') {
            closeModal();
        }
    });
})
